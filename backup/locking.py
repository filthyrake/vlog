"""
Backup locking mechanism.

Prevents concurrent backup operations using both file-based locks
and database flags for distributed safety.

Supports both Unix (fcntl) and Windows (msvcrt) platforms.
"""

import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from backup.exceptions import BackupLockError

# Platform-specific file locking
if sys.platform == "win32":
    import msvcrt

    def _lock_file(fd):
        """Acquire exclusive lock on file (Windows)."""
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_file(fd):
        """Release lock on file (Windows)."""
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fd):
        """Acquire exclusive lock on file (Unix)."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fd):
        """Release lock on file (Unix)."""
        fcntl.flock(fd, fcntl.LOCK_UN)

logger = logging.getLogger(__name__)


class BackupLock:
    """
    Distributed backup lock using file locks and database flags.

    Uses a two-layer approach:
    1. File lock for single-machine protection
    2. Database flag for distributed deployments

    The lock is automatically released on process exit or context manager exit.
    """

    LOCK_FILENAME = ".backup.lock"
    LOCK_TIMEOUT_SECONDS = 10

    def __init__(self, lock_dir: Path):
        """
        Initialize backup lock.

        Args:
            lock_dir: Directory to store lock file
        """
        self.lock_dir = lock_dir
        self.lock_file_path = lock_dir / self.LOCK_FILENAME
        self._file_handle = None
        self._acquired = False

    def _acquire_file_lock(self) -> bool:
        """
        Acquire file-based lock.

        Returns:
            True if lock acquired, False otherwise
        """
        try:
            # Ensure lock directory exists
            self.lock_dir.mkdir(parents=True, exist_ok=True)

            # Open lock file (create if doesn't exist)
            self._file_handle = open(self.lock_file_path, "w")

            # Try to acquire exclusive lock (non-blocking)
            _lock_file(self._file_handle.fileno())

            # Write lock info
            lock_info = {
                "pid": os.getpid(),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            self._file_handle.write(str(lock_info))
            self._file_handle.flush()

            logger.debug(f"Acquired file lock at {self.lock_file_path}")
            return True

        except (IOError, OSError) as e:
            # Lock already held by another process
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            logger.debug(f"Failed to acquire file lock: {e}")
            return False

    def _release_file_lock(self) -> None:
        """Release file-based lock."""
        if self._file_handle:
            try:
                _unlock_file(self._file_handle.fileno())
                self._file_handle.close()
                # Clean up lock file
                if self.lock_file_path.exists():
                    self.lock_file_path.unlink()
                logger.debug(f"Released file lock at {self.lock_file_path}")
            except (IOError, OSError) as e:
                logger.warning(f"Error releasing file lock: {e}")
            finally:
                self._file_handle = None

    async def acquire_db_lock(self, database) -> bool:
        """
        Acquire database-based lock.

        Checks for any in-progress backups in the database.

        Args:
            database: Database connection

        Returns:
            True if no conflicting backup, False otherwise
        """
        from api.database import backups

        # Check for any backup in progress
        in_progress_statuses = [
            "pending",
            "backing_up_database",
            "backing_up_files",
            "uploading_s3",
        ]

        result = await database.fetch_one(
            backups.select().where(backups.c.status.in_(in_progress_statuses))
        )

        if result:
            logger.warning(
                f"Another backup is in progress: {result['backup_id']} (status: {result['status']})"
            )
            return False

        return True

    def acquire(self) -> bool:
        """
        Acquire backup lock (file-based only, sync version).

        Returns:
            True if lock acquired

        Raises:
            BackupLockError: If lock cannot be acquired
        """
        if not self._acquire_file_lock():
            raise BackupLockError(
                "Another backup operation is in progress. "
                "Wait for it to complete or check for stale locks."
            )

        self._acquired = True
        return True

    async def acquire_async(self, database=None) -> bool:
        """
        Acquire backup lock (file + optional database lock).

        Args:
            database: Optional database connection for distributed lock

        Returns:
            True if lock acquired

        Raises:
            BackupLockError: If lock cannot be acquired
        """
        # First try file lock
        if not self._acquire_file_lock():
            raise BackupLockError(
                "Another backup operation is in progress (file lock held). "
                "Wait for it to complete or check for stale locks."
            )

        # Then check database if provided
        if database:
            try:
                if not await self.acquire_db_lock(database):
                    self._release_file_lock()
                    raise BackupLockError(
                        "Another backup operation is in progress (database lock). "
                        "Check backup status in the database."
                    )
            except Exception as e:
                self._release_file_lock()
                if isinstance(e, BackupLockError):
                    raise
                raise BackupLockError(f"Failed to check database lock: {e}")

        self._acquired = True
        return True

    def release(self) -> None:
        """Release backup lock."""
        if self._acquired:
            self._release_file_lock()
            self._acquired = False

    def __enter__(self) -> "BackupLock":
        """Context manager entry."""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.release()

    async def __aenter__(self) -> "BackupLock":
        """Async context manager entry."""
        await self.acquire_async()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        self.release()


@contextmanager
def backup_lock(lock_dir: Path) -> Generator[BackupLock, None, None]:
    """
    Context manager for backup lock.

    Args:
        lock_dir: Directory to store lock file

    Yields:
        BackupLock instance

    Raises:
        BackupLockError: If lock cannot be acquired
    """
    lock = BackupLock(lock_dir)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()
