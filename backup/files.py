"""
Incremental video file backup.

Handles copying video files with:
- Incremental backup (only new/changed files)
- SHA-256 checksum computation during copy
- Progress callbacks for CLI display
- Path validation to prevent traversal attacks
"""

import asyncio
import hashlib
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backup.exceptions import BackupError, DiskSpaceError, ValidationError
from backup.manifest import FileInfo

logger = logging.getLogger(__name__)

# Chunk size for file copying (1 MB)
COPY_CHUNK_SIZE = 1024 * 1024

# Maximum path length to prevent issues
MAX_PATH_LENGTH = 4096


def validate_path_safe(path: Path, allowed_parent: Path) -> Path:
    """
    Validate a path is safe (no traversal, within allowed parent).

    Uses pattern from worker/remote_transcoder.py:153-224.

    Args:
        path: Path to validate
        allowed_parent: Parent directory that path must be within

    Returns:
        Resolved absolute path

    Raises:
        ValidationError: If path is unsafe
    """
    # Convert to string for checks
    path_str = str(path)

    # Check for path traversal attempts
    if ".." in path_str:
        raise ValidationError(f"Path traversal attempt detected: {path_str}")

    # Check path length
    if len(path_str) > MAX_PATH_LENGTH:
        raise ValidationError(f"Path too long ({len(path_str)} > {MAX_PATH_LENGTH})")

    # Resolve to absolute path
    resolved = path.resolve()
    allowed_resolved = allowed_parent.resolve()

    # Verify within allowed parent
    try:
        resolved.relative_to(allowed_resolved)
    except ValueError:
        raise ValidationError(
            f"Path {resolved} is not within allowed parent {allowed_resolved}"
        )

    return resolved


class FileBackupHandler:
    """
    Handles incremental video file backup.

    Compares files against previous backup manifest to determine
    which files need to be copied.
    """

    def __init__(
        self,
        source_dir: Path,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        """
        Initialize file backup handler.

        Args:
            source_dir: Source directory containing video files
            progress_callback: Optional callback for progress updates
                              (file_path, bytes_copied, total_bytes)
        """
        self.source_dir = source_dir.resolve()
        self.progress_callback = progress_callback

    def get_free_space(self, path: Path) -> int:
        """
        Get free space at path in bytes.

        Args:
            path: Path to check

        Returns:
            Free space in bytes
        """
        stat = os.statvfs(path)
        return stat.f_bavail * stat.f_frsize

    async def scan_files(self) -> list[tuple[Path, int, datetime]]:
        """
        Scan source directory for all files.

        Returns:
            List of (path, size, modified_time) tuples
        """
        files = []

        def _scan():
            for root, dirs, filenames in os.walk(self.source_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                for filename in filenames:
                    # Skip hidden files
                    if filename.startswith("."):
                        continue

                    filepath = Path(root) / filename
                    try:
                        stat = filepath.stat()
                        files.append((
                            filepath,
                            stat.st_size,
                            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        ))
                    except OSError as e:
                        logger.warning(f"Could not stat file {filepath}: {e}")

        await asyncio.get_event_loop().run_in_executor(None, _scan)
        return files

    async def get_files_to_backup(
        self,
        previous_files: Optional[Dict[str, FileInfo]] = None,
    ) -> List[tuple]:
        """
        Determine which files need to be backed up.

        Args:
            previous_files: Dict of relative_path -> FileInfo from previous backup

        Returns:
            List of (path, size) tuples for files to backup
        """
        all_files = await self.scan_files()
        files_to_backup = []

        for filepath, size, mtime in all_files:
            relative_path = filepath.relative_to(self.source_dir)
            relative_str = str(relative_path)

            # Check if file is new or changed
            if previous_files is None:
                # Full backup - include all files
                files_to_backup.append((filepath, size))
            elif relative_str not in previous_files:
                # New file
                files_to_backup.append((filepath, size))
            else:
                # Check if modified (by comparing modification time)
                prev_info = previous_files[relative_str]
                prev_mtime = datetime.fromisoformat(prev_info.modified_at)
                if mtime > prev_mtime:
                    files_to_backup.append((filepath, size))

        return files_to_backup

    async def copy_file_with_checksum(
        self,
        source: Path,
        dest: Path,
    ) -> tuple[int, str]:
        """
        Copy file and compute checksum simultaneously.

        Args:
            source: Source file path
            dest: Destination file path

        Returns:
            Tuple of (size_bytes, sha256_checksum)

        Raises:
            BackupError: If copy fails
        """
        def _copy():
            sha256 = hashlib.sha256()
            total_bytes = 0

            # Ensure destination directory exists
            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(source, "rb") as src_f, open(dest, "wb") as dst_f:
                while chunk := src_f.read(COPY_CHUNK_SIZE):
                    sha256.update(chunk)
                    dst_f.write(chunk)
                    total_bytes += len(chunk)

                    if self.progress_callback:
                        self.progress_callback(
                            str(source),
                            total_bytes,
                            source.stat().st_size,
                        )

            return total_bytes, sha256.hexdigest()

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _copy)
        except OSError as e:
            raise BackupError(f"Failed to copy {source}: {e}")

    async def backup_files(
        self,
        dest_dir: Path,
        previous_files: Optional[Dict[str, FileInfo]] = None,
        max_size_bytes: Optional[int] = None,
    ) -> tuple:
        """
        Backup files to destination directory.

        Args:
            dest_dir: Destination directory for backup
            previous_files: Files from previous backup (for incremental)
            max_size_bytes: Maximum total size to backup

        Returns:
            Tuple of (list of FileInfo, total_size_bytes)

        Raises:
            DiskSpaceError: If insufficient disk space
            BackupError: If backup fails
        """
        logger.info(f"Starting file backup from {self.source_dir} to {dest_dir}")

        # Get files to backup
        files_to_backup = await self.get_files_to_backup(previous_files)

        if not files_to_backup:
            logger.info("No files need to be backed up")
            return [], 0

        # Calculate total size
        total_size = sum(size for _, size in files_to_backup)
        logger.info(f"Found {len(files_to_backup)} files to backup ({total_size} bytes)")

        # Check max size limit
        if max_size_bytes and total_size > max_size_bytes:
            raise BackupError(
                f"Total file size ({total_size} bytes) exceeds maximum ({max_size_bytes} bytes)"
            )

        # Check disk space (require 2x for safety margin)
        free_space = self.get_free_space(dest_dir.parent)
        required_space = total_size * 2
        if free_space < required_space:
            raise DiskSpaceError(
                f"Insufficient disk space. Required: {required_space} bytes, Available: {free_space} bytes",
                required_bytes=required_space,
                available_bytes=free_space,
            )

        # Create destination directory
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy files
        backed_up_files = []
        backed_up_size = 0

        for source_path, expected_size in files_to_backup:
            relative_path = source_path.relative_to(self.source_dir)
            dest_path = dest_dir / relative_path

            try:
                size, checksum = await self.copy_file_with_checksum(source_path, dest_path)
                mtime = datetime.fromtimestamp(
                    source_path.stat().st_mtime, tz=timezone.utc
                )

                backed_up_files.append(FileInfo(
                    path=str(relative_path),
                    size_bytes=size,
                    checksum_sha256=checksum,
                    modified_at=mtime.isoformat(),
                ))
                backed_up_size += size

                logger.debug(f"Backed up: {relative_path} ({size} bytes)")

            except Exception as e:
                logger.error(f"Failed to backup {source_path}: {e}")
                raise BackupError(f"Failed to backup file {source_path}: {e}")

        logger.info(f"File backup complete: {len(backed_up_files)} files, {backed_up_size} bytes")
        return backed_up_files, backed_up_size


class FileRestoreHandler:
    """
    Handles restoring video files from backup.

    Validates paths and checksums during restore.
    """

    def __init__(
        self,
        dest_dir: Path,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        """
        Initialize file restore handler.

        Args:
            dest_dir: Destination directory for restored files
            progress_callback: Optional callback for progress updates
        """
        self.dest_dir = dest_dir.resolve()
        self.progress_callback = progress_callback

    async def restore_files(
        self,
        source_dir: Path,
        file_infos: List[FileInfo],
        verify_checksums: bool = True,
    ) -> int:
        """
        Restore files from backup.

        Args:
            source_dir: Source directory (backup location)
            file_infos: List of FileInfo from manifest
            verify_checksums: Whether to verify checksums after copy

        Returns:
            Number of files restored

        Raises:
            ValidationError: If path validation fails
            BackupError: If restore fails
        """
        logger.info(f"Starting file restore from {source_dir} to {self.dest_dir}")

        restored_count = 0

        for file_info in file_infos:
            # Validate path safety
            relative_path = Path(file_info.path)

            # Check for path traversal
            if ".." in str(relative_path):
                raise ValidationError(f"Path traversal in backup: {file_info.path}")

            source_path = source_dir / relative_path
            dest_path = self.dest_dir / relative_path

            # Validate source is within backup
            validate_path_safe(source_path, source_dir)

            # Validate dest is within dest_dir
            validate_path_safe(dest_path, self.dest_dir)

            # Copy file
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            def _copy():
                sha256 = hashlib.sha256()
                total_bytes = 0

                with open(source_path, "rb") as src_f, open(dest_path, "wb") as dst_f:
                    while chunk := src_f.read(COPY_CHUNK_SIZE):
                        sha256.update(chunk)
                        dst_f.write(chunk)
                        total_bytes += len(chunk)

                        if self.progress_callback:
                            self.progress_callback(
                                str(relative_path),
                                total_bytes,
                                file_info.size_bytes,
                            )

                return sha256.hexdigest()

            try:
                actual_checksum = await asyncio.get_event_loop().run_in_executor(
                    None, _copy
                )

                # Verify checksum if requested
                if verify_checksums and actual_checksum != file_info.checksum_sha256:
                    raise BackupError(
                        f"Checksum mismatch for {file_info.path}: "
                        f"expected {file_info.checksum_sha256}, got {actual_checksum}"
                    )

                restored_count += 1
                logger.debug(f"Restored: {relative_path}")

            except OSError as e:
                raise BackupError(f"Failed to restore {file_info.path}: {e}")

        logger.info(f"File restore complete: {restored_count} files")
        return restored_count
