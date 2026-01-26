"""
Main BackupService orchestrator.

Coordinates database backup, file backup, S3 upload, and retention policy.
"""

import asyncio
import json
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backup.database import get_database_handler
from backup.exceptions import BackupError, BackupLockError, DiskSpaceError
from backup.files import FileBackupHandler
from backup.locking import BackupLock
from backup.manifest import (
    BackupManifest,
    BackupType,
    FileInfo,
    Statistics,
    VideoFilesInfo,
    compute_file_checksum,
    validate_backup_id,
)
from backup.s3 import get_s3_storage

logger = logging.getLogger(__name__)

# VLog version (read from package or env)
VLOG_VERSION = os.getenv("VLOG_VERSION", "dev")


class BackupService:
    """
    Main backup service that orchestrates all backup operations.

    Handles:
    - Creating full, database-only, or incremental backups
    - Uploading to S3 (optional)
    - Managing backup retention
    - Listing and verifying backups
    """

    def __init__(
        self,
        database_url: str,
        videos_dir: Path,
        backup_path: Path,
        schema_version: str = "032",
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
    ):
        """
        Initialize backup service.

        Args:
            database_url: Database connection URL
            videos_dir: Directory containing video files
            backup_path: Directory to store backups
            schema_version: Current database schema version
            progress_callback: Optional callback (stage, detail, current, total)
        """
        self.database_url = database_url
        self.videos_dir = Path(videos_dir).resolve()
        self.backup_path = Path(backup_path).resolve()
        self.schema_version = schema_version
        self.progress_callback = progress_callback

        # Ensure backup path exists
        self.backup_path.mkdir(parents=True, exist_ok=True)

    def _report_progress(self, stage: str, detail: str, current: int = 0, total: int = 0):
        """Report progress to callback if configured."""
        if self.progress_callback:
            self.progress_callback(stage, detail, current, total)

    def _generate_backup_id(self) -> str:
        """Generate unique backup ID based on timestamp."""
        now = datetime.now(timezone.utc)
        return f"backup_{now.strftime('%Y%m%d_%H%M%S')}"

    def _get_free_space(self, path: Path) -> int:
        """Get free space at path in bytes."""
        stat = os.statvfs(path)
        return stat.f_bavail * stat.f_frsize

    async def _get_statistics(self, database) -> Statistics:
        """
        Get backup statistics from database.

        Uses a single combined query for better performance instead of
        making multiple round trips to the database.
        """
        # Combined query to get all statistics in one round trip
        result = await database.fetch_one(
            """
            SELECT
                (SELECT COUNT(*) FROM videos WHERE deleted_at IS NULL) as video_count,
                (SELECT COUNT(*) FROM categories) as category_count,
                (SELECT COUNT(*) FROM users) as user_count,
                (SELECT COALESCE(SUM(duration), 0) FROM videos WHERE deleted_at IS NULL) as total_duration
            """
        )

        return Statistics(
            video_count=result["video_count"] or 0,
            category_count=result["category_count"] or 0,
            user_count=result["user_count"] or 0,
            total_duration_seconds=float(result["total_duration"] or 0),
            total_file_count=0,  # Updated during file backup
            total_size_bytes=0,  # Updated at end
        )

    async def create_backup(
        self,
        backup_type: BackupType = BackupType.FULL,
        include_videos: bool = False,
        description: Optional[str] = None,
        upload_to_s3: bool = False,
        created_by: str = "system",
    ) -> dict:
        """
        Create a backup.

        Args:
            backup_type: Type of backup to create
            include_videos: Whether to include video files
            description: Optional description
            upload_to_s3: Whether to upload to S3
            created_by: Who initiated the backup

        Returns:
            Dict with backup details

        Raises:
            BackupLockError: If another backup is in progress
            BackupError: If backup fails
            DiskSpaceError: If insufficient disk space
        """
        from config import (
            BACKUP_DB_TIMEOUT,
            BACKUP_MAX_SIZE_GB,
            BACKUP_S3_TIMEOUT,
            BACKUP_SIGNING_KEY,
        )

        backup_id = self._generate_backup_id()
        temp_dir = None

        # Acquire lock
        lock = BackupLock(self.backup_path)

        try:
            lock.acquire()
            logger.info(f"Starting {backup_type.value} backup: {backup_id}")
            self._report_progress("init", f"Starting backup {backup_id}")

            # Create temp directory for backup assembly
            temp_dir = Path(tempfile.mkdtemp(prefix=f"{backup_id}_"))

            # Get database handler
            db_handler = get_database_handler(self.database_url)

            # Check disk space (require 2x expected size)
            free_space = self._get_free_space(self.backup_path)
            max_size_bytes = BACKUP_MAX_SIZE_GB * 1024 * 1024 * 1024
            if free_space < max_size_bytes * 2:
                raise DiskSpaceError(
                    f"Insufficient disk space. Required: {max_size_bytes * 2} bytes, Available: {free_space} bytes",
                    required_bytes=max_size_bytes * 2,
                    available_bytes=free_space,
                )

            # Step 1: Backup database
            self._report_progress("database", "Backing up database...")
            db_file = "database.dump" if db_handler.get_database_type() == "postgresql" else "database.db.gz"
            db_path = temp_dir / db_file

            db_size, db_checksum = await db_handler.backup(db_path, BACKUP_DB_TIMEOUT)
            table_count = await db_handler.get_table_count()

            logger.info(f"Database backup complete: {db_size} bytes")

            # Step 2: Backup video files if requested
            video_files_info = VideoFilesInfo(included=False)
            files_size = 0

            if include_videos and backup_type != BackupType.DATABASE_ONLY:
                self._report_progress("files", "Backing up video files...")
                files_dir = temp_dir / "files"

                file_handler = FileBackupHandler(
                    self.videos_dir,
                    progress_callback=lambda p, c, t: self._report_progress("files", p, c, t),
                )

                # For incremental, load previous manifest
                previous_files = None
                if backup_type == BackupType.INCREMENTAL:
                    # Find most recent successful backup manifest
                    previous_files = await self._get_previous_file_list()

                backed_up_files, files_size = await file_handler.backup_files(
                    files_dir,
                    previous_files=previous_files,
                    max_size_bytes=max_size_bytes,
                )

                video_files_info = VideoFilesInfo(
                    included=True,
                    file_count=len(backed_up_files),
                    total_size_bytes=files_size,
                    files=backed_up_files if len(backed_up_files) < 10000 else [],
                    files_list_file="files.jsonl" if len(backed_up_files) >= 10000 else None,
                )

                # Write JSONL file for large backups
                if video_files_info.files_list_file:
                    with open(temp_dir / "files.jsonl", "w") as f:
                        for file_info in backed_up_files:
                            f.write(json.dumps({
                                "path": file_info.path,
                                "size_bytes": file_info.size_bytes,
                                "checksum_sha256": file_info.checksum_sha256,
                                "modified_at": file_info.modified_at,
                            }) + "\n")

            # Step 3: Get statistics
            from api.database import database

            await database.connect()
            try:
                statistics = await self._get_statistics(database)
            finally:
                await database.disconnect()

            statistics.total_file_count = video_files_info.file_count
            statistics.total_size_bytes = db_size + files_size

            # Step 4: Create manifest
            self._report_progress("manifest", "Creating manifest...")
            manifest = BackupManifest.create(
                backup_id=backup_id,
                backup_type=backup_type,
                vlog_version=VLOG_VERSION,
                schema_version=self.schema_version,
                database_type=db_handler.get_database_type(),
                database_file=db_file,
                database_size=db_size,
                database_checksum=db_checksum,
                statistics=statistics,
                video_files=video_files_info,
                description=description,
                table_count=table_count,
            )

            # Sign manifest if key is configured
            if BACKUP_SIGNING_KEY:
                manifest.sign(BACKUP_SIGNING_KEY)

            manifest.save(temp_dir / "manifest.json")

            # Step 5: Create tarball
            self._report_progress("archive", "Creating archive...")
            archive_name = f"{backup_id}.tar.gz"
            archive_path = temp_dir / archive_name

            with tarfile.open(archive_path, "w:gz") as tar:
                for item in temp_dir.iterdir():
                    if item.name != archive_name:
                        tar.add(item, arcname=item.name)

            archive_size = archive_path.stat().st_size
            archive_checksum = compute_file_checksum(archive_path)

            # Step 6: Move to final location
            final_path = self.backup_path / archive_name
            shutil.move(str(archive_path), str(final_path))

            logger.info(f"Backup archive created: {final_path} ({archive_size} bytes)")

            # Step 7: Upload to S3 if requested
            s3_location = None
            if upload_to_s3:
                self._report_progress("s3", "Uploading to S3...")
                s3 = get_s3_storage(
                    progress_callback=lambda op, c, t: self._report_progress("s3", op, c, t)
                )
                if s3:
                    try:
                        s3_location = await s3.upload_file(
                            final_path,
                            archive_name,
                            timeout_seconds=BACKUP_S3_TIMEOUT,
                        )
                        # Verify upload
                        if not await s3.verify_upload(archive_name, archive_size):
                            logger.warning("S3 upload verification failed (size mismatch)")
                    except Exception as e:
                        logger.error(f"S3 upload failed: {e}")
                        # Don't fail backup if S3 upload fails
                else:
                    logger.warning("S3 not configured, skipping upload")

            # Step 8: Record in database
            from api.database import backups, database

            await database.connect()
            try:
                now = datetime.now(timezone.utc)
                await database.execute(
                    backups.insert().values(
                        backup_id=backup_id,
                        backup_type=backup_type.value,
                        status="completed",
                        size_bytes=archive_size,
                        database_size_bytes=db_size,
                        files_size_bytes=files_size,
                        video_count=statistics.video_count,
                        file_count=video_files_info.file_count,
                        description=description,
                        local_path=str(final_path),
                        s3_location=s3_location,
                        manifest_json=manifest.to_json(),
                        manifest_signature=manifest.signature,
                        created_at=now,
                        completed_at=now,
                        created_by=created_by,
                        vlog_version=VLOG_VERSION,
                        schema_version=self.schema_version,
                        database_type=db_handler.get_database_type(),
                    )
                )
            finally:
                await database.disconnect()

            self._report_progress("complete", f"Backup complete: {backup_id}")

            return {
                "backup_id": backup_id,
                "backup_type": backup_type.value,
                "size_bytes": archive_size,
                "database_size_bytes": db_size,
                "files_size_bytes": files_size,
                "video_count": statistics.video_count,
                "file_count": video_files_info.file_count,
                "local_path": str(final_path),
                "s3_location": s3_location,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            # Cleanup temp directory
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            lock.release()

    async def _get_previous_file_list(self) -> Optional[Dict[str, FileInfo]]:
        """Get file list from most recent successful backup."""
        from api.database import backups, database

        await database.connect()
        try:
            result = await database.fetch_one(
                backups.select()
                .where(backups.c.status == "completed")
                .order_by(backups.c.created_at.desc())
                .limit(1)
            )

            if not result or not result["manifest_json"]:
                return None

            manifest = BackupManifest.from_json(result["manifest_json"])
            if not manifest.video_files.included:
                return None

            return {f.path: f for f in manifest.video_files.files}
        finally:
            await database.disconnect()

    async def list_backups(self, include_remote: bool = False) -> list[dict]:
        """
        List available backups.

        Args:
            include_remote: Whether to include S3 backups

        Returns:
            List of backup info dicts
        """
        from api.database import backups, database

        await database.connect()
        try:
            results = await database.fetch_all(
                backups.select()
                .where(backups.c.status == "completed")
                .order_by(backups.c.created_at.desc())
            )

            backup_list = [
                {
                    "backup_id": r["backup_id"],
                    "backup_type": r["backup_type"],
                    "status": r["status"],
                    "size_bytes": r["size_bytes"],
                    "video_count": r["video_count"],
                    "file_count": r["file_count"],
                    "description": r["description"],
                    "local_path": r["local_path"],
                    "s3_location": r["s3_location"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "created_by": r["created_by"],
                }
                for r in results
            ]

            # Add S3-only backups if requested
            if include_remote:
                s3 = get_s3_storage()
                if s3:
                    try:
                        s3_backups = await s3.list_backups()
                        known_ids = {b["backup_id"] for b in backup_list}

                        for s3_backup in s3_backups:
                            backup_id = s3_backup["key"].replace(".tar.gz", "")
                            if backup_id not in known_ids:
                                backup_list.append({
                                    "backup_id": backup_id,
                                    "backup_type": "unknown",
                                    "status": "remote",
                                    "size_bytes": s3_backup["size"],
                                    "video_count": None,
                                    "file_count": None,
                                    "description": None,
                                    "local_path": None,
                                    "s3_location": f"s3://{s3.bucket}/{s3._get_full_key(s3_backup['key'])}",
                                    "created_at": s3_backup["last_modified"],
                                    "created_by": None,
                                })
                    except Exception as e:
                        logger.warning(f"Failed to list S3 backups: {e}")

            return backup_list
        finally:
            await database.disconnect()

    async def delete_backup(self, backup_id: str, delete_remote: bool = True) -> bool:
        """
        Delete a backup.

        Args:
            backup_id: Backup to delete
            delete_remote: Whether to also delete from S3

        Returns:
            True if deleted

        Raises:
            BackupError: If deletion fails
            ValidationError: If backup ID format is invalid
        """
        # Validate backup ID format to prevent injection attacks
        validate_backup_id(backup_id)

        from api.database import backups, database

        await database.connect()
        try:
            result = await database.fetch_one(
                backups.select().where(backups.c.backup_id == backup_id)
            )

            if not result:
                raise BackupError(f"Backup not found: {backup_id}")

            # Delete local file
            if result["local_path"]:
                local_path = Path(result["local_path"])
                if local_path.exists():
                    local_path.unlink()
                    logger.info(f"Deleted local backup: {local_path}")

            # Delete from S3
            if delete_remote and result["s3_location"]:
                s3 = get_s3_storage()
                if s3:
                    try:
                        await s3.delete_backup(f"{backup_id}.tar.gz")
                    except Exception as e:
                        logger.warning(f"Failed to delete from S3: {e}")

            # Delete database record
            await database.execute(
                backups.delete().where(backups.c.backup_id == backup_id)
            )

            logger.info(f"Deleted backup: {backup_id}")
            return True
        finally:
            await database.disconnect()

    async def apply_retention_policy(self, retention_count: int) -> list[str]:
        """
        Apply retention policy, deleting old backups.

        Args:
            retention_count: Number of backups to retain

        Returns:
            List of deleted backup IDs
        """
        from api.database import backups, database

        await database.connect()
        try:
            # Get all completed backups ordered by date
            results = await database.fetch_all(
                backups.select()
                .where(backups.c.status == "completed")
                .order_by(backups.c.created_at.desc())
            )

            # Keep newest retention_count backups
            to_delete = results[retention_count:]
            deleted = []

            for backup in to_delete:
                try:
                    await self.delete_backup(backup["backup_id"])
                    deleted.append(backup["backup_id"])
                except Exception as e:
                    logger.error(f"Failed to delete old backup {backup['backup_id']}: {e}")

            if deleted:
                logger.info(f"Retention policy applied: deleted {len(deleted)} old backups")

            return deleted
        finally:
            await database.disconnect()
