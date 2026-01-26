"""
Backup integrity verification.

Provides checksum verification for backup archives and manifests.
"""

import asyncio
import logging
import tarfile
from pathlib import Path
from typing import Optional

from backup.exceptions import IntegrityError, ValidationError
from backup.manifest import (
    CHECKSUM_CHUNK_SIZE,
    BackupManifest,
    compute_file_checksum,
    validate_backup_id,
    verify_file_checksum,
)

logger = logging.getLogger(__name__)


class BackupVerifier:
    """
    Verifies backup integrity.

    Checks:
    - Archive integrity
    - Manifest signature (if signed)
    - Database dump checksum
    - Video file checksums (if included)
    """

    def __init__(self, backup_path: Optional[Path] = None):
        """
        Initialize verifier.

        Args:
            backup_path: Directory containing backup archives
        """
        self.backup_path = backup_path

    async def verify_backup(
        self,
        backup_id: str,
        signing_key: Optional[str] = None,
        verify_files: bool = True,
    ) -> dict:
        """
        Verify backup integrity.

        Args:
            backup_id: Backup to verify
            signing_key: Key for signature verification (optional)
            verify_files: Whether to verify individual file checksums

        Returns:
            Dict with verification results

        Raises:
            ValidationError: If backup not found or ID format invalid
            IntegrityError: If verification fails
        """
        # Validate backup ID format to prevent injection attacks
        validate_backup_id(backup_id)

        from api.database import backups, database

        # Get backup info
        await database.connect()
        try:
            result = await database.fetch_one(
                backups.select().where(backups.c.backup_id == backup_id)
            )

            if not result:
                raise ValidationError(f"Backup not found: {backup_id}")

            archive_path = Path(result["local_path"]) if result["local_path"] else None

            if not archive_path or not archive_path.exists():
                raise ValidationError(f"Backup archive not found: {archive_path}")

        finally:
            await database.disconnect()

        logger.info(f"Verifying backup: {backup_id}")

        verification_result = {
            "backup_id": backup_id,
            "archive_valid": False,
            "manifest_valid": False,
            "signature_valid": None,
            "database_valid": False,
            "files_valid": None,
            "errors": [],
        }

        # Step 1: Verify archive can be opened
        try:
            await self._verify_archive(archive_path)
            verification_result["archive_valid"] = True
            logger.info("Archive integrity: OK")
        except Exception as e:
            verification_result["errors"].append(f"Archive integrity: {e}")
            raise IntegrityError(f"Archive is corrupted: {e}")

        # Step 2: Extract and verify manifest
        manifest = await self._extract_manifest(archive_path)
        verification_result["manifest_valid"] = True
        logger.info("Manifest: OK")

        # Step 3: Verify manifest signature
        if signing_key:
            try:
                manifest.verify_signature(signing_key)
                verification_result["signature_valid"] = True
                logger.info("Manifest signature: OK")
            except IntegrityError as e:
                verification_result["signature_valid"] = False
                verification_result["errors"].append(f"Signature: {e}")
                raise
        elif manifest.signature:
            verification_result["signature_valid"] = None
            logger.warning("Manifest is signed but no signing key provided for verification")

        # Step 4: Verify database dump checksum
        try:
            db_checksum = await self._extract_and_checksum(
                archive_path, manifest.database.file
            )
            if db_checksum != manifest.database.checksum_sha256:
                raise IntegrityError(
                    f"Database checksum mismatch",
                    expected_checksum=manifest.database.checksum_sha256,
                    actual_checksum=db_checksum,
                    file_path=manifest.database.file,
                )
            verification_result["database_valid"] = True
            logger.info("Database checksum: OK")
        except IntegrityError:
            raise
        except Exception as e:
            verification_result["errors"].append(f"Database: {e}")
            raise IntegrityError(f"Failed to verify database: {e}")

        # Step 5: Verify file checksums if requested and files are included
        if verify_files and manifest.video_files.included:
            verification_result["files_valid"] = True
            failed_files = []

            for file_info in manifest.video_files.files:
                try:
                    file_checksum = await self._extract_and_checksum(
                        archive_path, f"files/{file_info.path}"
                    )
                    if file_checksum != file_info.checksum_sha256:
                        failed_files.append(file_info.path)
                except Exception as e:
                    failed_files.append(file_info.path)
                    logger.warning(f"Failed to verify {file_info.path}: {e}")

            if failed_files:
                verification_result["files_valid"] = False
                verification_result["errors"].append(
                    f"Failed files: {', '.join(failed_files[:10])}"
                    + (f" and {len(failed_files) - 10} more" if len(failed_files) > 10 else "")
                )
                raise IntegrityError(
                    f"{len(failed_files)} files failed verification"
                )

            logger.info(f"File checksums: OK ({len(manifest.video_files.files)} files)")

        logger.info(f"Backup verification complete: {backup_id}")
        return verification_result

    async def _verify_archive(self, archive_path: Path) -> None:
        """Verify archive can be opened and is not corrupted."""
        def _check():
            with tarfile.open(archive_path, "r:gz") as tar:
                # Iterate through all members to verify integrity
                for member in tar:
                    pass  # Just verify it can be read

        await asyncio.get_running_loop().run_in_executor(None, _check)

    async def _extract_manifest(self, archive_path: Path) -> BackupManifest:
        """Extract and parse manifest from archive."""
        def _extract():
            with tarfile.open(archive_path, "r:gz") as tar:
                manifest_file = tar.extractfile("manifest.json")
                if not manifest_file:
                    raise ValidationError("Manifest not found in archive")
                content = manifest_file.read().decode("utf-8")
                return BackupManifest.from_json(content)

        return await asyncio.get_running_loop().run_in_executor(None, _extract)

    async def _extract_and_checksum(self, archive_path: Path, member_name: str) -> str:
        """Extract a member and compute its checksum."""
        import hashlib

        def _compute():
            sha256 = hashlib.sha256()
            with tarfile.open(archive_path, "r:gz") as tar:
                member_file = tar.extractfile(member_name)
                if not member_file:
                    raise ValidationError(f"File not found in archive: {member_name}")

                # Use 1 MB chunks for better performance on large files
                while chunk := member_file.read(CHECKSUM_CHUNK_SIZE):
                    sha256.update(chunk)

            return sha256.hexdigest()

        return await asyncio.get_running_loop().run_in_executor(None, _compute)


async def verify_backup(
    backup_id: str,
    signing_key: Optional[str] = None,
    verify_files: bool = True,
) -> dict:
    """
    Convenience function to verify a backup.

    Args:
        backup_id: Backup to verify
        signing_key: Key for signature verification
        verify_files: Whether to verify file checksums

    Returns:
        Verification result dict
    """
    verifier = BackupVerifier()
    return await verifier.verify_backup(
        backup_id,
        signing_key=signing_key,
        verify_files=verify_files,
    )
