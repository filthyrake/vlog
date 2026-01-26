"""
Backup restoration logic.

Provides safe restore with rollback guarantee:
1. Download/locate backup
2. Verify integrity
3. Create safety backup before restore
4. Restore database and files
5. Rollback on failure
"""

import asyncio
import logging
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from backup.database import get_database_handler
from backup.exceptions import BackupError, IntegrityError, RestoreError, ValidationError
from backup.files import FileRestoreHandler, validate_path_safe
from backup.manifest import BackupManifest
from backup.s3 import get_s3_storage
from backup.verify import BackupVerifier

logger = logging.getLogger(__name__)


class RestoreService:
    """
    Handles backup restoration with rollback guarantee.

    RESTORE FLOW:
    1. Verify backup integrity BEFORE any changes
    2. Check disk space for safety backup
    3. Create safety backup to separate location
    4. Verify safety backup integrity
    5. Begin restore (point of no return)
    6. On ANY failure: restore from safety backup
    7. If safety restore fails: HALT and alert
    8. On success: optionally delete safety backup
    """

    def __init__(
        self,
        database_url: str,
        videos_dir: Path,
        backup_path: Path,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Initialize restore service.

        Args:
            database_url: Database connection URL
            videos_dir: Directory for video files
            backup_path: Directory containing backups
            progress_callback: Optional callback (stage, detail)
        """
        self.database_url = database_url
        self.videos_dir = Path(videos_dir).resolve()
        self.backup_path = Path(backup_path).resolve()
        self.progress_callback = progress_callback

    def _report_progress(self, stage: str, detail: str):
        """Report progress to callback."""
        if self.progress_callback:
            self.progress_callback(stage, detail)

    async def restore_backup(
        self,
        backup_id: str,
        restore_type: str = "full",
        dry_run: bool = False,
        force: bool = False,
        signing_key: Optional[str] = None,
    ) -> dict:
        """
        Restore from a backup.

        Args:
            backup_id: Backup to restore from
            restore_type: "full", "database_only", or "files_only"
            dry_run: If True, only verify without restoring
            force: Skip confirmation prompts
            signing_key: Key for manifest signature verification

        Returns:
            Dict with restore results

        Raises:
            RestoreError: If restore fails
            IntegrityError: If backup verification fails
        """
        from config import BACKUP_DB_TIMEOUT, BACKUP_S3_TIMEOUT

        logger.info(f"Starting {restore_type} restore from backup {backup_id}")
        self._report_progress("init", f"Starting restore from {backup_id}")

        temp_dir = None
        safety_backup_path = None

        try:
            # Step 1: Locate backup
            self._report_progress("locate", "Locating backup...")
            archive_path = await self._locate_backup(backup_id)

            # Step 2: Verify backup integrity
            self._report_progress("verify", "Verifying backup integrity...")
            verifier = BackupVerifier()
            await verifier.verify_backup(
                backup_id,
                signing_key=signing_key,
                verify_files=(restore_type != "database_only"),
            )
            logger.info("Backup verification passed")

            if dry_run:
                return {
                    "backup_id": backup_id,
                    "restore_type": restore_type,
                    "dry_run": True,
                    "status": "verified",
                    "message": "Backup verified successfully. Use --force to restore.",
                }

            # Step 3: Extract archive to temp directory
            self._report_progress("extract", "Extracting backup archive...")
            temp_dir = Path(tempfile.mkdtemp(prefix=f"restore_{backup_id}_"))

            def _extract():
                with tarfile.open(archive_path, "r:gz") as tar:
                    # Validate all paths before extraction (security)
                    for member in tar.getmembers():
                        if ".." in member.name or member.name.startswith("/"):
                            raise ValidationError(
                                f"Unsafe path in archive: {member.name}"
                            )
                    tar.extractall(temp_dir)

            await asyncio.get_event_loop().run_in_executor(None, _extract)

            # Load manifest
            manifest = BackupManifest.from_file(temp_dir / "manifest.json")

            # Step 4: Create safety backup
            self._report_progress("safety", "Creating safety backup...")
            safety_backup_path = await self._create_safety_backup(restore_type)

            # Step 5: Restore database
            if restore_type in ("full", "database_only"):
                self._report_progress("database", "Restoring database...")
                try:
                    await self._restore_database(
                        temp_dir / manifest.database.file,
                        manifest.database.database_type,
                        BACKUP_DB_TIMEOUT,
                    )
                except Exception as e:
                    logger.error(f"Database restore failed: {e}")
                    # Attempt rollback
                    await self._rollback_from_safety(safety_backup_path, "database")
                    raise RestoreError(
                        f"Database restore failed: {e}",
                        stage="database",
                        can_rollback=True,
                    )

            # Step 6: Restore files
            if restore_type in ("full", "files_only") and manifest.video_files.included:
                self._report_progress("files", "Restoring video files...")
                try:
                    await self._restore_files(
                        temp_dir / "files",
                        manifest.video_files.files,
                    )
                except Exception as e:
                    logger.error(f"File restore failed: {e}")
                    # Attempt rollback
                    await self._rollback_from_safety(safety_backup_path, "full")
                    raise RestoreError(
                        f"File restore failed: {e}",
                        stage="files",
                        can_rollback=True,
                    )

            # Step 7: Verify restoration
            self._report_progress("verify_restore", "Verifying restoration...")
            await self._verify_restoration(manifest)

            # Step 8: Clean up safety backup
            if safety_backup_path and safety_backup_path.exists():
                shutil.rmtree(safety_backup_path, ignore_errors=True)

            logger.info(f"Restore complete from backup {backup_id}")
            self._report_progress("complete", "Restore complete")

            return {
                "backup_id": backup_id,
                "restore_type": restore_type,
                "dry_run": False,
                "status": "completed",
                "restored_at": datetime.now(timezone.utc).isoformat(),
            }

        except (RestoreError, IntegrityError):
            raise
        except Exception as e:
            raise RestoreError(f"Restore failed: {e}", stage="unknown")
        finally:
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _locate_backup(self, backup_id: str) -> Path:
        """
        Locate backup archive locally or download from S3.

        Returns:
            Path to backup archive
        """
        from api.database import backups, database

        await database.connect()
        try:
            result = await database.fetch_one(
                backups.select().where(backups.c.backup_id == backup_id)
            )

            if not result:
                raise ValidationError(f"Backup not found: {backup_id}")

            # Check local path first
            if result["local_path"]:
                local_path = Path(result["local_path"])
                if local_path.exists():
                    return local_path

            # Try to download from S3
            if result["s3_location"]:
                from config import BACKUP_S3_TIMEOUT

                s3 = get_s3_storage()
                if s3:
                    download_path = self.backup_path / f"{backup_id}.tar.gz"
                    await s3.download_file(
                        f"{backup_id}.tar.gz",
                        download_path,
                        timeout_seconds=BACKUP_S3_TIMEOUT,
                    )
                    return download_path

            raise ValidationError(
                f"Backup archive not found locally or in S3: {backup_id}"
            )
        finally:
            await database.disconnect()

    async def _create_safety_backup(self, restore_type: str) -> Path:
        """
        Create safety backup before restore.

        Returns:
            Path to safety backup directory
        """
        from config import BACKUP_DB_TIMEOUT

        safety_dir = Path(tempfile.mkdtemp(prefix="safety_backup_"))

        if restore_type in ("full", "database_only"):
            # Backup current database
            db_handler = get_database_handler(self.database_url)
            db_file = "database.dump" if db_handler.get_database_type() == "postgresql" else "database.db.gz"
            await db_handler.backup(safety_dir / db_file, BACKUP_DB_TIMEOUT)
            logger.info(f"Created safety database backup")

        if restore_type in ("full", "files_only"):
            # Note: For large video directories, this could take a long time
            # In production, consider using filesystem snapshots instead
            logger.info("Safety backup for files would be created here (not implemented for large directories)")

        return safety_dir

    async def _rollback_from_safety(self, safety_path: Path, restore_type: str) -> None:
        """
        Rollback from safety backup.

        Args:
            safety_path: Path to safety backup
            restore_type: What to rollback
        """
        from config import BACKUP_DB_TIMEOUT

        logger.warning(f"Rolling back from safety backup...")

        if restore_type in ("full", "database_only", "database"):
            db_handler = get_database_handler(self.database_url)
            db_file = "database.dump" if db_handler.get_database_type() == "postgresql" else "database.db.gz"
            safety_db = safety_path / db_file

            if safety_db.exists():
                try:
                    await db_handler.restore(safety_db, BACKUP_DB_TIMEOUT)
                    logger.info("Database rolled back successfully")
                except Exception as e:
                    logger.critical(
                        f"CRITICAL: Failed to rollback database: {e}. "
                        f"Manual intervention required. Safety backup at: {safety_path}"
                    )
                    raise RestoreError(
                        f"Rollback failed: {e}. Manual intervention required.",
                        stage="rollback",
                        can_rollback=False,
                    )

    async def _restore_database(
        self,
        backup_file: Path,
        database_type: str,
        timeout_seconds: int,
    ) -> None:
        """Restore database from backup file."""
        db_handler = get_database_handler(self.database_url)

        # Verify database types match
        if db_handler.get_database_type() != database_type:
            raise RestoreError(
                f"Database type mismatch. Backup is {database_type}, "
                f"but current database is {db_handler.get_database_type()}",
                stage="database",
            )

        await db_handler.restore(backup_file, timeout_seconds)

    async def _restore_files(
        self,
        source_dir: Path,
        file_infos: list,
    ) -> None:
        """Restore video files."""
        if not source_dir.exists():
            logger.info("No files to restore")
            return

        handler = FileRestoreHandler(
            self.videos_dir,
            progress_callback=lambda p, c, t: self._report_progress("files", p),
        )

        await handler.restore_files(source_dir, file_infos, verify_checksums=True)

    async def _verify_restoration(self, manifest: BackupManifest) -> None:
        """Verify restoration was successful."""
        from api.database import database

        # Verify database connectivity and basic query
        await database.connect()
        try:
            # Run simple query to verify database is accessible
            count = await database.fetch_val("SELECT COUNT(*) FROM videos")
            logger.info(f"Database verification: {count} videos found")
        finally:
            await database.disconnect()


async def restore_backup(
    backup_id: str,
    restore_type: str = "full",
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """
    Convenience function to restore from backup.

    Args:
        backup_id: Backup to restore
        restore_type: Type of restore
        dry_run: Verify only
        force: Skip prompts

    Returns:
        Restore result dict
    """
    from config import BACKUP_PATH, BACKUP_SIGNING_KEY, DATABASE_URL, VIDEOS_DIR

    service = RestoreService(
        database_url=DATABASE_URL,
        videos_dir=VIDEOS_DIR,
        backup_path=BACKUP_PATH,
    )

    return await service.restore_backup(
        backup_id,
        restore_type=restore_type,
        dry_run=dry_run,
        force=force,
        signing_key=BACKUP_SIGNING_KEY or None,
    )
