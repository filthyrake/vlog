"""
Backup-specific exception types.

Provides granular exception hierarchy for backup operations,
enabling proper error handling and reporting.
"""

from typing import Optional


class BackupError(Exception):
    """Base exception for all backup-related errors."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class BackupLockError(BackupError):
    """Raised when unable to acquire backup lock (another backup running)."""

    pass


class IntegrityError(BackupError):
    """Raised when backup integrity verification fails."""

    def __init__(
        self,
        message: str,
        expected_checksum: Optional[str] = None,
        actual_checksum: Optional[str] = None,
        file_path: Optional[str] = None,
    ):
        super().__init__(
            message,
            {
                "expected_checksum": expected_checksum,
                "actual_checksum": actual_checksum,
                "file_path": file_path,
            },
        )
        self.expected_checksum = expected_checksum
        self.actual_checksum = actual_checksum
        self.file_path = file_path


class RestoreError(BackupError):
    """Raised when backup restoration fails."""

    def __init__(self, message: str, stage: Optional[str] = None, can_rollback: bool = True):
        super().__init__(message, {"stage": stage, "can_rollback": can_rollback})
        self.stage = stage
        self.can_rollback = can_rollback


class S3Error(BackupError):
    """Raised for S3-related errors (upload, download, connection)."""

    def __init__(self, message: str, operation: Optional[str] = None, bucket: Optional[str] = None):
        super().__init__(message, {"operation": operation, "bucket": bucket})
        self.operation = operation
        self.bucket = bucket


class ValidationError(BackupError):
    """Raised when backup validation fails (manifest, paths, etc.)."""

    pass


class TimeoutError(BackupError):
    """Raised when a backup operation times out."""

    def __init__(self, message: str, operation: str, timeout_seconds: int):
        super().__init__(
            message, {"operation": operation, "timeout_seconds": timeout_seconds}
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class DiskSpaceError(BackupError):
    """Raised when insufficient disk space for backup operation."""

    def __init__(
        self,
        message: str,
        required_bytes: Optional[int] = None,
        available_bytes: Optional[int] = None,
    ):
        super().__init__(
            message,
            {"required_bytes": required_bytes, "available_bytes": available_bytes},
        )
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
