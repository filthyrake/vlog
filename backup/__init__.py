"""
VLog Backup and Restore System.

Comprehensive backup system with database backup (PostgreSQL & SQLite),
optional video file backup, S3 remote storage, and built-in scheduling.

See: https://github.com/filthyrake/vlog/issues/216
"""

from backup.exceptions import (
    BackupError,
    BackupLockError,
    BackupTimeoutError,
    DiskSpaceError,
    IntegrityError,
    RestoreError,
    S3Error,
    ValidationError,
)
from backup.manifest import BackupManifest, BackupType, FileInfo, validate_backup_id
from backup.service import BackupService

__all__ = [
    # Service
    "BackupService",
    # Exceptions
    "BackupError",
    "BackupLockError",
    "BackupTimeoutError",
    "DiskSpaceError",
    "IntegrityError",
    "RestoreError",
    "S3Error",
    "ValidationError",
    # Data classes
    "BackupManifest",
    "BackupType",
    "FileInfo",
    # Utilities
    "validate_backup_id",
]
