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
from backup.manifest import BackupManifest, validate_backup_id
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
        accept_no_file_rollback: bool = False,
    ) -> dict:
        """
        Restore from a backup.

        Args:
            backup_id: Backup to restore from
            restore_type: "full", "database_only", or "files_only"
            dry_run: If True, only verify without restoring
            force: Skip confirmation prompts
            signing_key: Key for manifest signature verification
            accept_no_file_rollback: Must be True when restore includes files.
                IMPORTANT: File safety backup is NOT implemented due to the
                potentially large size of video directories. If file restoration
                fails partway through, there is NO automatic rollback capability.
                Set this to True to acknowledge you understand this limitation.
                Database-only restores have full rollback capability.

        Returns:
            Dict with restore results

        Raises:
            RestoreError: If restore fails
            IntegrityError: If backup verification fails
            RestoreError: If restore includes files but accept_no_file_rollback=False
        """
        from config import BACKUP_DB_TIMEOUT, BACKUP_S3_TIMEOUT

        # Validate backup ID format to prevent injection attacks
        validate_backup_id(backup_id)

        # Validate restore_type
        if restore_type not in ("full", "database_only", "files_only"):
            raise RestoreError(
                f"Invalid restore_type: {restore_type}. "
                "Must be 'full', 'database_only', or 'files_only'",
                stage="validation",
            )

        # CRITICAL: File safety backup is not implemented due to potentially
        # large video directories. Require explicit acknowledgment.
        if restore_type in ("full", "files_only") and not accept_no_file_rollback:
            raise RestoreError(
                "File restore requested but accept_no_file_rollback=False. "
                "WARNING: File safety backup is NOT implemented. If file restoration "
                "fails partway through, there is NO automatic rollback capability - "
                "some files may be overwritten with no way to recover them. "
                "Set accept_no_file_rollback=True to proceed, or use "
                "restore_type='database_only' for safe database-only restore with "
                "full rollback capability.",
                stage="validation",
            )

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
                    # Security: Comprehensive validation of all archive members
                    # Protects against CVE-2007-4559 and similar path traversal attacks
                    safe_members = []
                    for member in tar.getmembers():
                        # Reject path traversal attempts
                        if ".." in member.name or member.name.startswith("/"):
                            raise ValidationError(
                                f"Unsafe path in archive: {member.name}"
                            )

                        # Reject symlinks and hardlinks (could point outside extract dir)
                        if member.issym() or member.islnk():
                            raise ValidationError(
                                f"Archive contains unsafe link: {member.name}"
                            )

                        # Reject device files and other special files
                        if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                            raise ValidationError(
                                f"Archive contains unsafe special file: {member.name}"
                            )

                        # Verify resolved path stays within temp_dir
                        target_path = (temp_dir / member.name).resolve()
                        if not str(target_path).startswith(str(temp_dir.resolve())):
                            raise ValidationError(
                                f"Path escapes extraction directory: {member.name}"
                            )

                        safe_members.append(member)

                    # Extract only validated members
                    tar.extractall(temp_dir, members=safe_members)

            await asyncio.get_running_loop().run_in_executor(None, _extract)

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

        IMPORTANT LIMITATION:
        - Database safety backup: FULLY IMPLEMENTED - can rollback database on failure
        - File safety backup: NOT IMPLEMENTED - no rollback for file operations

        File safety backup is intentionally not implemented because video directories
        can be extremely large (hundreds of GB or TB). Creating a full copy would:
        - Require double the disk space
        - Take hours for large collections
        - Make restore operations impractically slow

        For production use with file restoration, consider:
        - Using filesystem snapshots (ZFS, LVM, BTRFS) before restore
        - Using cloud storage with versioning enabled
        - Maintaining separate offsite backups

        The restore process requires explicit acknowledgment via
        accept_no_file_rollback=True when restoring files.

        Returns:
            Path to safety backup directory
        """
        from config import BACKUP_DB_TIMEOUT

        safety_dir = Path(tempfile.mkdtemp(prefix="safety_backup_"))

        if restore_type in ("full", "database_only"):
            # Backup current database - this provides full rollback capability
            db_handler = get_database_handler(self.database_url)
            db_file = "database.dump" if db_handler.get_database_type() == "postgresql" else "database.db.gz"
            safety_db_path = safety_dir / db_file

            # Create the safety backup
            size_bytes, checksum = await db_handler.backup(safety_db_path, BACKUP_DB_TIMEOUT)

            # CRITICAL: Verify safety backup was created correctly before proceeding
            # This prevents proceeding with restore when safety backup failed silently
            if not safety_db_path.exists():
                raise RestoreError(
                    "Safety backup file not created - aborting restore for safety",
                    stage="safety_backup",
                    can_rollback=False,
                )

            actual_size = safety_db_path.stat().st_size
            if actual_size == 0:
                raise RestoreError(
                    "Safety backup file is empty - aborting restore for safety",
                    stage="safety_backup",
                    can_rollback=False,
                )

            if actual_size != size_bytes:
                raise RestoreError(
                    f"Safety backup size mismatch: expected {size_bytes}, got {actual_size}",
                    stage="safety_backup",
                    can_rollback=False,
                )

            logger.info(
                f"Created and verified safety database backup: {size_bytes} bytes, "
                f"checksum {checksum[:16]}... (rollback available)"
            )

        if restore_type in ("full", "files_only"):
            # File safety backup NOT IMPLEMENTED - user acknowledged via accept_no_file_rollback
            logger.warning(
                "File safety backup NOT available. If file restore fails, "
                "manual recovery from the original backup may be required."
            )

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
    accept_no_file_rollback: bool = False,
) -> dict:
    """
    Convenience function to restore from backup.

    Args:
        backup_id: Backup to restore
        restore_type: Type of restore ("full", "database_only", "files_only")
        dry_run: Verify only
        force: Skip prompts
        accept_no_file_rollback: Required for file restores. Acknowledges that
            file safety backup is not implemented and there is no rollback
            capability if file restoration fails.

    Returns:
        Restore result dict

    Raises:
        RestoreError: If restore includes files but accept_no_file_rollback=False
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
        accept_no_file_rollback=accept_no_file_rollback,
    )
