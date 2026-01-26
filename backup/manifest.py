"""
Backup manifest generation and parsing.

The manifest is a JSON file that describes the contents of a backup,
including file lists, checksums, and metadata for integrity verification.
"""

import hashlib
import hmac
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

from backup.exceptions import IntegrityError, ValidationError

logger = logging.getLogger(__name__)

# Manifest version for compatibility checking
MANIFEST_VERSION = "1.0"


class BackupType(str, Enum):
    """Backup type enumeration."""

    FULL = "full"
    DATABASE_ONLY = "database_only"
    INCREMENTAL = "incremental"


@dataclass
class FileInfo:
    """Information about a file in the backup."""

    path: str  # Relative path within backup
    size_bytes: int
    checksum_sha256: str
    modified_at: str  # ISO 8601 timestamp


@dataclass
class DatabaseInfo:
    """Information about the database backup."""

    file: str  # Filename within backup archive
    size_bytes: int
    checksum_sha256: str
    database_type: str  # postgresql or sqlite
    table_count: Optional[int] = None


@dataclass
class VLogInfo:
    """VLog instance information for compatibility checking."""

    version: str
    schema_version: str
    database_type: str


@dataclass
class Statistics:
    """Backup statistics."""

    video_count: int
    category_count: int
    user_count: int
    total_duration_seconds: float
    total_file_count: int
    total_size_bytes: int


@dataclass
class VideoFilesInfo:
    """Information about video files in the backup."""

    included: bool
    file_count: int = 0
    total_size_bytes: int = 0
    # For large deployments, files are listed in a separate JSONL file
    files_list_file: Optional[str] = None
    # For smaller backups, files are embedded
    files: List[FileInfo] = field(default_factory=list)


@dataclass
class BackupManifest:
    """
    Complete backup manifest.

    Contains all information needed to verify and restore a backup.
    """

    version: str
    backup_id: str
    created_at: str  # ISO 8601 timestamp
    backup_type: BackupType
    description: Optional[str]

    vlog_info: VLogInfo
    database: DatabaseInfo
    statistics: Statistics
    video_files: VideoFilesInfo

    # Manifest integrity
    signature: Optional[str] = None

    @classmethod
    def create(
        cls,
        backup_id: str,
        backup_type: BackupType,
        vlog_version: str,
        schema_version: str,
        database_type: str,
        database_file: str,
        database_size: int,
        database_checksum: str,
        statistics: Statistics,
        video_files: Optional[VideoFilesInfo] = None,
        description: Optional[str] = None,
        table_count: Optional[int] = None,
    ) -> "BackupManifest":
        """
        Create a new backup manifest.

        Args:
            backup_id: Unique backup identifier
            backup_type: Type of backup
            vlog_version: VLog application version
            schema_version: Database schema version
            database_type: Database type (postgresql or sqlite)
            database_file: Filename of database backup
            database_size: Size of database backup in bytes
            database_checksum: SHA-256 checksum of database backup
            statistics: Backup statistics
            video_files: Video files information (optional)
            description: User-provided description
            table_count: Number of tables in database

        Returns:
            BackupManifest instance
        """
        return cls(
            version=MANIFEST_VERSION,
            backup_id=backup_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            backup_type=backup_type,
            description=description,
            vlog_info=VLogInfo(
                version=vlog_version,
                schema_version=schema_version,
                database_type=database_type,
            ),
            database=DatabaseInfo(
                file=database_file,
                size_bytes=database_size,
                checksum_sha256=database_checksum,
                database_type=database_type,
                table_count=table_count,
            ),
            statistics=statistics,
            video_files=video_files or VideoFilesInfo(included=False),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to dictionary."""
        data = asdict(self)
        # Convert enum to string
        data["backup_type"] = self.backup_type.value
        return data

    def to_json(self, indent: int = 2) -> str:
        """Convert manifest to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupManifest":
        """
        Create manifest from dictionary.

        Args:
            data: Dictionary containing manifest data

        Returns:
            BackupManifest instance

        Raises:
            ValidationError: If data is invalid
        """
        try:
            # Convert nested dicts to dataclasses
            vlog_info = VLogInfo(**data["vlog_info"])
            database = DatabaseInfo(**data["database"])
            statistics = Statistics(**data["statistics"])

            # Handle video files
            video_files_data = data.get("video_files", {"included": False})
            if "files" in video_files_data:
                video_files_data["files"] = [
                    FileInfo(**f) for f in video_files_data["files"]
                ]
            video_files = VideoFilesInfo(**video_files_data)

            return cls(
                version=data["version"],
                backup_id=data["backup_id"],
                created_at=data["created_at"],
                backup_type=BackupType(data["backup_type"]),
                description=data.get("description"),
                vlog_info=vlog_info,
                database=database,
                statistics=statistics,
                video_files=video_files,
                signature=data.get("signature"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValidationError(f"Invalid manifest data: {e}")

    @classmethod
    def from_json(cls, json_str: str) -> "BackupManifest":
        """
        Create manifest from JSON string.

        Args:
            json_str: JSON string

        Returns:
            BackupManifest instance
        """
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON: {e}")

    @classmethod
    def from_file(cls, path: Path) -> "BackupManifest":
        """
        Load manifest from file.

        Args:
            path: Path to manifest file

        Returns:
            BackupManifest instance
        """
        if not path.exists():
            raise ValidationError(f"Manifest file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    def save(self, path: Path) -> None:
        """
        Save manifest to file.

        Args:
            path: Path to save manifest
        """
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    def sign(self, signing_key: str) -> str:
        """
        Sign manifest with HMAC-SHA256.

        Args:
            signing_key: Secret key for signing

        Returns:
            Hex-encoded signature
        """
        # Create canonical JSON (sorted keys, no whitespace)
        # Exclude signature field
        data = self.to_dict()
        data.pop("signature", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))

        signature = hmac.new(
            signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.signature = signature
        return signature

    def verify_signature(self, signing_key: str) -> bool:
        """
        Verify manifest signature.

        Args:
            signing_key: Secret key for verification

        Returns:
            True if signature is valid

        Raises:
            IntegrityError: If signature is invalid or missing
        """
        if not self.signature:
            raise IntegrityError("Manifest is not signed")

        # Create canonical JSON (sorted keys, no whitespace)
        data = self.to_dict()
        data.pop("signature", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))

        expected = hmac.new(
            signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(self.signature, expected):
            raise IntegrityError(
                "Manifest signature verification failed",
                expected_checksum=expected,
                actual_checksum=self.signature,
            )

        return True


def compute_file_checksum(path: Path, chunk_size: int = 8192) -> str:
    """
    Compute SHA-256 checksum of a file.

    Args:
        path: Path to file
        chunk_size: Read chunk size in bytes

    Returns:
        Hex-encoded SHA-256 checksum
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_file_checksum(path: Path, expected_checksum: str) -> bool:
    """
    Verify file checksum.

    Args:
        path: Path to file
        expected_checksum: Expected SHA-256 checksum

    Returns:
        True if checksum matches

    Raises:
        IntegrityError: If checksum doesn't match
    """
    actual = compute_file_checksum(path)
    if actual != expected_checksum:
        raise IntegrityError(
            f"Checksum mismatch for {path}",
            expected_checksum=expected_checksum,
            actual_checksum=actual,
            file_path=str(path),
        )
    return True
