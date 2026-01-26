"""
Database backup handlers for PostgreSQL and SQLite.

Provides backup and restore functionality for both database types
using their respective native tools (pg_dump/pg_restore, sqlite3 backup API).

SECURITY: Database credentials are passed via environment variables only,
never as command-line arguments (visible in `ps` output).
"""

import asyncio
import gzip
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse

from backup.exceptions import BackupError, TimeoutError, ValidationError
from backup.manifest import compute_file_checksum

logger = logging.getLogger(__name__)


class DatabaseBackupHandler(ABC):
    """Abstract base class for database backup handlers."""

    @abstractmethod
    async def backup(self, output_path: Path, timeout_seconds: int) -> tuple[int, str]:
        """
        Create database backup.

        Args:
            output_path: Path to write backup file
            timeout_seconds: Timeout for backup operation

        Returns:
            Tuple of (size_bytes, checksum_sha256)

        Raises:
            BackupError: If backup fails
            TimeoutError: If backup times out
        """
        pass

    @abstractmethod
    async def restore(self, backup_path: Path, timeout_seconds: int) -> None:
        """
        Restore database from backup.

        Args:
            backup_path: Path to backup file
            timeout_seconds: Timeout for restore operation

        Raises:
            BackupError: If restore fails
            TimeoutError: If restore times out
        """
        pass

    @abstractmethod
    async def get_table_count(self) -> int:
        """
        Get number of tables in database.

        Returns:
            Number of tables
        """
        pass

    @abstractmethod
    def get_database_type(self) -> str:
        """
        Get database type identifier.

        Returns:
            "postgresql" or "sqlite"
        """
        pass


class PostgreSQLBackupHandler(DatabaseBackupHandler):
    """
    PostgreSQL backup handler using pg_dump/pg_restore.

    Uses custom format (-Fc) for compressed, selective restore capability.
    Credentials are passed via PGPASSWORD environment variable only.
    """

    def __init__(self, database_url: str):
        """
        Initialize PostgreSQL handler.

        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url
        self._parse_connection_params()

    def _parse_connection_params(self) -> None:
        """Parse connection parameters from database URL."""
        parsed = urlparse(self.database_url)

        self.host = parsed.hostname or "localhost"
        self.port = str(parsed.port or 5432)
        self.database = parsed.path.lstrip("/") or "vlog"
        self.user = parsed.username or "vlog"
        self.password = parsed.password or ""

    def _get_pg_env(self) -> dict[str, str]:
        """
        Get environment variables for pg_dump/pg_restore.

        SECURITY: Password is passed via PGPASSWORD only, never as CLI arg.
        """
        env = os.environ.copy()
        if self.password:
            env["PGPASSWORD"] = self.password
        return env

    def _get_pg_args(self) -> list[str]:
        """Get common pg_dump/pg_restore arguments."""
        return [
            "-h", self.host,
            "-p", self.port,
            "-U", self.user,
            "-d", self.database,
        ]

    async def backup(self, output_path: Path, timeout_seconds: int) -> tuple[int, str]:
        """
        Create PostgreSQL backup using pg_dump.

        Uses custom format (-Fc) for compression and selective restore.
        """
        logger.info(f"Starting PostgreSQL backup to {output_path}")

        # Build pg_dump command
        cmd = [
            "pg_dump",
            "-Fc",  # Custom format (compressed)
            "--no-owner",  # Don't include ownership commands
            "--no-acl",  # Don't include access control
            *self._get_pg_args(),
            "-f", str(output_path),
        ]

        try:
            # Run pg_dump with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=self._get_pg_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError(
                    f"Database backup timed out after {timeout_seconds} seconds",
                    operation="pg_dump",
                    timeout_seconds=timeout_seconds,
                )

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                # Sanitize error message (remove credentials if any)
                error_msg = error_msg.replace(self.password, "***") if self.password else error_msg
                raise BackupError(f"pg_dump failed: {error_msg}")

            # Verify output file exists
            if not output_path.exists():
                raise BackupError("pg_dump completed but output file not found")

            # Get size and compute checksum
            size_bytes = output_path.stat().st_size
            checksum = compute_file_checksum(output_path)

            logger.info(f"PostgreSQL backup complete: {size_bytes} bytes")
            return size_bytes, checksum

        except (OSError, FileNotFoundError) as e:
            raise BackupError(f"Failed to run pg_dump: {e}. Is PostgreSQL client installed?")

    async def restore(self, backup_path: Path, timeout_seconds: int) -> None:
        """
        Restore PostgreSQL database using pg_restore.

        Drops existing data and restores from backup.
        """
        logger.info(f"Starting PostgreSQL restore from {backup_path}")

        if not backup_path.exists():
            raise ValidationError(f"Backup file not found: {backup_path}")

        # Build pg_restore command
        cmd = [
            "pg_restore",
            "-Fc",  # Custom format
            "--clean",  # Drop objects before recreating
            "--if-exists",  # Don't error if objects don't exist
            "--no-owner",
            "--no-acl",
            *self._get_pg_args(),
            str(backup_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=self._get_pg_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError(
                    f"Database restore timed out after {timeout_seconds} seconds",
                    operation="pg_restore",
                    timeout_seconds=timeout_seconds,
                )

            # pg_restore returns non-zero for warnings too, check stderr for actual errors
            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                # Filter out common non-fatal warnings
                error_lines = [
                    line for line in stderr_text.split("\n")
                    if "error" in line.lower() and "already exists" not in line.lower()
                ]
                if error_lines:
                    error_msg = "\n".join(error_lines[:5])  # First 5 error lines
                    error_msg = error_msg.replace(self.password, "***") if self.password else error_msg
                    raise BackupError(f"pg_restore errors: {error_msg}")

            logger.info("PostgreSQL restore complete")

        except (OSError, FileNotFoundError) as e:
            raise BackupError(f"Failed to run pg_restore: {e}. Is PostgreSQL client installed?")

    async def get_table_count(self) -> int:
        """Get number of tables in PostgreSQL database."""
        cmd = [
            "psql",
            *self._get_pg_args(),
            "-t",  # Tuples only
            "-c", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=self._get_pg_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return int(stdout.decode().strip())
            return 0
        except Exception:
            return 0

    def get_database_type(self) -> str:
        return "postgresql"


class SQLiteBackupHandler(DatabaseBackupHandler):
    """
    SQLite backup handler using sqlite3 backup API.

    Creates a compressed backup using gzip after checkpointing WAL.
    """

    def __init__(self, database_path: Path):
        """
        Initialize SQLite handler.

        Args:
            database_path: Path to SQLite database file
        """
        self.database_path = database_path

    async def backup(self, output_path: Path, timeout_seconds: int) -> tuple[int, str]:
        """
        Create SQLite backup using backup API.

        Performs WAL checkpoint and creates compressed backup.
        """
        logger.info(f"Starting SQLite backup to {output_path}")

        if not self.database_path.exists():
            raise ValidationError(f"Database file not found: {self.database_path}")

        def _do_backup():
            """Sync backup operation to run in thread."""
            # Create temporary uncompressed backup
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                tmp_path = Path(tmp.name)

            try:
                # Connect to source database
                source = sqlite3.connect(str(self.database_path))

                # Checkpoint WAL if in WAL mode
                try:
                    source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.OperationalError:
                    pass  # Not in WAL mode

                # Connect to destination
                dest = sqlite3.connect(str(tmp_path))

                # Use backup API
                source.backup(dest)

                dest.close()
                source.close()

                # Compress to output path
                with open(tmp_path, "rb") as f_in:
                    with gzip.open(output_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

            finally:
                # Clean up temp file
                if tmp_path.exists():
                    tmp_path.unlink()

        try:
            # Run backup with timeout
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _do_backup),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Database backup timed out after {timeout_seconds} seconds",
                operation="sqlite_backup",
                timeout_seconds=timeout_seconds,
            )

        # Verify output file exists
        if not output_path.exists():
            raise BackupError("SQLite backup completed but output file not found")

        # Get size and compute checksum
        size_bytes = output_path.stat().st_size
        checksum = compute_file_checksum(output_path)

        logger.info(f"SQLite backup complete: {size_bytes} bytes")
        return size_bytes, checksum

    async def restore(self, backup_path: Path, timeout_seconds: int) -> None:
        """
        Restore SQLite database from compressed backup.

        Replaces the existing database file.
        """
        logger.info(f"Starting SQLite restore from {backup_path}")

        if not backup_path.exists():
            raise ValidationError(f"Backup file not found: {backup_path}")

        def _do_restore():
            """Sync restore operation to run in thread."""
            # Decompress to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                tmp_path = Path(tmp.name)

            try:
                with gzip.open(backup_path, "rb") as f_in:
                    with open(tmp_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

                # Verify it's a valid SQLite database
                conn = sqlite3.connect(str(tmp_path))
                conn.execute("SELECT 1")
                conn.close()

                # Replace existing database
                # First, try to close any existing connections (best effort)
                shutil.copy2(tmp_path, self.database_path)

            finally:
                # Clean up temp file
                if tmp_path.exists():
                    tmp_path.unlink()

        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _do_restore),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Database restore timed out after {timeout_seconds} seconds",
                operation="sqlite_restore",
                timeout_seconds=timeout_seconds,
            )

        logger.info("SQLite restore complete")

    async def get_table_count(self) -> int:
        """Get number of tables in SQLite database."""
        try:
            conn = sqlite3.connect(str(self.database_path))
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def get_database_type(self) -> str:
        return "sqlite"


def get_database_handler(database_url: str) -> DatabaseBackupHandler:
    """
    Factory function to get appropriate database handler.

    Args:
        database_url: Database connection URL

    Returns:
        DatabaseBackupHandler instance for the database type

    Raises:
        ValidationError: If database URL is invalid or unsupported
    """
    if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
        return PostgreSQLBackupHandler(database_url)
    elif database_url.startswith("sqlite:///"):
        # Extract path from sqlite:/// URL
        path = database_url.replace("sqlite:///", "")
        # Handle relative paths (sqlite:///./path) and absolute (sqlite:////path)
        if path.startswith("./"):
            path = path[2:]
        elif path.startswith("/"):
            pass  # Already absolute
        else:
            # Relative path
            pass
        return SQLiteBackupHandler(Path(path))
    else:
        raise ValidationError(
            f"Unsupported database URL scheme. Expected postgresql:// or sqlite:/// but got: {database_url[:20]}..."
        )
