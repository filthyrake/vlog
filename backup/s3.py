"""
S3 storage integration for backup uploads and downloads.

Supports:
- AWS S3
- S3-compatible storage (MinIO, DigitalOcean Spaces, Backblaze B2, etc.)
- Multipart uploads with parallel part uploads and progress tracking
- Server-side encryption (AES-256)
- Retry with exponential backoff
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Callable, List, Optional, Tuple

from backup.exceptions import S3Error, ValidationError

logger = logging.getLogger(__name__)

# Multipart upload threshold (5 MB)
MULTIPART_THRESHOLD = 5 * 1024 * 1024

# Multipart chunk size (10 MB)
MULTIPART_CHUNK_SIZE = 10 * 1024 * 1024

# Maximum concurrent part uploads for multipart
MAX_CONCURRENT_UPLOADS = 4

# Maximum retry attempts
MAX_RETRIES = 3

# Retry delays (seconds) - exponential backoff
RETRY_DELAYS = [1, 2, 4]


class S3Storage:
    """
    S3 storage handler for backup upload/download.

    Supports both AWS S3 and S3-compatible storage backends.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        """
        Initialize S3 storage.

        Credentials are read from environment or IAM role:
        - AWS_ACCESS_KEY_ID
        - AWS_SECRET_ACCESS_KEY
        - AWS_SESSION_TOKEN (optional, for temporary credentials)

        Args:
            bucket: S3 bucket name
            prefix: Key prefix for all objects
            region: AWS region (default: us-east-1)
            endpoint_url: Custom endpoint for S3-compatible storage
            progress_callback: Optional progress callback (operation, bytes, total)
        """
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.region = region
        self.endpoint_url = endpoint_url
        self.progress_callback = progress_callback
        self._client = None

    def _get_client(self):
        """
        Get boto3 S3 client (lazy initialization).

        Raises:
            S3Error: If boto3 is not installed or credentials are missing
        """
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config

                # Configure client
                config = Config(
                    region_name=self.region,
                    retries={"max_attempts": MAX_RETRIES, "mode": "adaptive"},
                    signature_version="s3v4",
                )

                kwargs = {
                    "config": config,
                }

                if self.endpoint_url:
                    kwargs["endpoint_url"] = self.endpoint_url

                self._client = boto3.client("s3", **kwargs)

            except ImportError:
                raise S3Error(
                    "boto3 is not installed. Install with: pip install boto3",
                    operation="init",
                )
            except Exception as e:
                raise S3Error(f"Failed to initialize S3 client: {e}", operation="init")

        return self._client

    def _get_full_key(self, key: str) -> str:
        """Get full S3 key with prefix."""
        return f"{self.prefix}{key}"

    async def check_bucket_access(self) -> bool:
        """
        Verify bucket exists and is accessible.

        Returns:
            True if bucket is accessible

        Raises:
            S3Error: If bucket is not accessible
        """
        def _check():
            client = self._get_client()
            try:
                client.head_bucket(Bucket=self.bucket)
                return True
            except client.exceptions.NoSuchBucket:
                raise S3Error(
                    f"Bucket '{self.bucket}' does not exist",
                    operation="head_bucket",
                    bucket=self.bucket,
                )
            except Exception as e:
                raise S3Error(
                    f"Cannot access bucket '{self.bucket}': {e}",
                    operation="head_bucket",
                    bucket=self.bucket,
                )

        return await asyncio.get_running_loop().run_in_executor(None, _check)

    async def upload_file(
        self,
        local_path: Path,
        key: str,
        timeout_seconds: int = 3600,
    ) -> str:
        """
        Upload file to S3 with multipart upload for large files.

        Uses server-side encryption (AES-256).

        Args:
            local_path: Local file path
            key: S3 object key (without prefix)
            timeout_seconds: Upload timeout

        Returns:
            Full S3 URI (s3://bucket/key)

        Raises:
            S3Error: If upload fails
        """
        if not local_path.exists():
            raise ValidationError(f"File not found: {local_path}")

        full_key = self._get_full_key(key)
        file_size = local_path.stat().st_size

        logger.info(f"Uploading {local_path} to s3://{self.bucket}/{full_key} ({file_size} bytes)")

        def _upload():
            client = self._get_client()

            # Extra args for server-side encryption
            extra_args = {
                "ServerSideEncryption": "AES256",
            }

            # Use multipart upload for large files
            if file_size > MULTIPART_THRESHOLD:
                return self._multipart_upload(
                    client, local_path, full_key, file_size, extra_args
                )
            else:
                return self._simple_upload(
                    client, local_path, full_key, extra_args
                )

        for attempt in range(MAX_RETRIES):
            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, _upload),
                    timeout=timeout_seconds,
                )
                break
            except asyncio.TimeoutError:
                if attempt == MAX_RETRIES - 1:
                    raise S3Error(
                        f"Upload timed out after {timeout_seconds} seconds",
                        operation="upload",
                        bucket=self.bucket,
                    )
                logger.warning(f"Upload attempt {attempt + 1} timed out, retrying...")
            except S3Error:
                raise
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise S3Error(f"Upload failed: {e}", operation="upload", bucket=self.bucket)
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"Upload attempt {attempt + 1} failed: {e}, retrying in {delay}s...")
                await asyncio.sleep(delay)

        s3_uri = f"s3://{self.bucket}/{full_key}"
        logger.info(f"Upload complete: {s3_uri}")
        return s3_uri

    def _simple_upload(self, client, local_path: Path, key: str, extra_args: dict) -> None:
        """Simple upload for small files."""
        with open(local_path, "rb") as f:
            client.upload_fileobj(f, self.bucket, key, ExtraArgs=extra_args)

    def _multipart_upload(
        self,
        client,
        local_path: Path,
        key: str,
        file_size: int,
        extra_args: dict,
    ) -> None:
        """
        Multipart upload for large files with parallel part uploads.

        Uploads parts in parallel using a thread pool to maximize throughput.
        Progress is tracked atomically across all concurrent uploads.
        """
        # Initiate multipart upload
        response = client.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            **extra_args,
        )
        upload_id = response["UploadId"]

        # Calculate parts
        parts_info: List[Tuple[int, int, int]] = []  # (part_number, offset, size)
        offset = 0
        part_number = 1
        while offset < file_size:
            chunk_size = min(MULTIPART_CHUNK_SIZE, file_size - offset)
            parts_info.append((part_number, offset, chunk_size))
            offset += chunk_size
            part_number += 1

        # Thread-safe progress tracking
        uploaded_bytes = [0]  # Use list for mutable closure
        progress_lock = Lock()

        def upload_part(part_info: Tuple[int, int, int]) -> dict:
            """Upload a single part (runs in thread pool)."""
            p_num, p_offset, p_size = part_info

            # Read chunk from file at specific offset
            with open(local_path, "rb") as f:
                f.seek(p_offset)
                chunk = f.read(p_size)

            # Upload part
            part_response = client.upload_part(
                Bucket=self.bucket,
                Key=key,
                PartNumber=p_num,
                UploadId=upload_id,
                Body=chunk,
            )

            # Update progress atomically
            with progress_lock:
                uploaded_bytes[0] += len(chunk)
                if self.progress_callback:
                    self.progress_callback("upload", uploaded_bytes[0], file_size)

            return {
                "PartNumber": p_num,
                "ETag": part_response["ETag"],
            }

        try:
            # Upload parts in parallel
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_UPLOADS) as executor:
                # Submit all parts and collect results
                # Parts are uploaded in parallel but we preserve order for completion
                part_results = list(executor.map(upload_part, parts_info))

            # Sort parts by part number (required for completion)
            parts = sorted(part_results, key=lambda p: p["PartNumber"])

            # Complete multipart upload
            client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )

            logger.info(
                f"Completed parallel multipart upload: {len(parts)} parts, "
                f"{MAX_CONCURRENT_UPLOADS} concurrent workers"
            )

        except Exception as e:
            # Abort multipart upload on failure
            try:
                client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            except Exception:
                pass  # Best effort cleanup
            raise S3Error(f"Multipart upload failed: {e}", operation="upload", bucket=self.bucket)

    async def download_file(
        self,
        key: str,
        local_path: Path,
        timeout_seconds: int = 3600,
    ) -> None:
        """
        Download file from S3.

        Args:
            key: S3 object key (without prefix)
            local_path: Local destination path
            timeout_seconds: Download timeout

        Raises:
            S3Error: If download fails
        """
        full_key = self._get_full_key(key)

        logger.info(f"Downloading s3://{self.bucket}/{full_key} to {local_path}")

        def _download():
            client = self._get_client()

            # Get object size for progress tracking
            try:
                head = client.head_object(Bucket=self.bucket, Key=full_key)
                file_size = head["ContentLength"]
            except Exception as e:
                raise S3Error(
                    f"Object not found: s3://{self.bucket}/{full_key}: {e}",
                    operation="head_object",
                    bucket=self.bucket,
                )

            # Ensure parent directory exists
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # Download with progress tracking
            downloaded_bytes = 0

            with open(local_path, "wb") as f:
                response = client.get_object(Bucket=self.bucket, Key=full_key)
                body = response["Body"]

                while True:
                    chunk = body.read(MULTIPART_CHUNK_SIZE)
                    if not chunk:
                        break

                    f.write(chunk)
                    downloaded_bytes += len(chunk)

                    if self.progress_callback:
                        self.progress_callback("download", downloaded_bytes, file_size)

        for attempt in range(MAX_RETRIES):
            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, _download),
                    timeout=timeout_seconds,
                )
                break
            except asyncio.TimeoutError:
                if attempt == MAX_RETRIES - 1:
                    raise S3Error(
                        f"Download timed out after {timeout_seconds} seconds",
                        operation="download",
                        bucket=self.bucket,
                    )
                logger.warning(f"Download attempt {attempt + 1} timed out, retrying...")
            except S3Error:
                raise
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise S3Error(f"Download failed: {e}", operation="download", bucket=self.bucket)
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"Download attempt {attempt + 1} failed: {e}, retrying in {delay}s...")
                await asyncio.sleep(delay)

        logger.info(f"Download complete: {local_path}")

    async def list_backups(self) -> list[dict]:
        """
        List available backups in S3.

        Returns:
            List of backup info dicts with keys: key, size, last_modified

        Raises:
            S3Error: If listing fails
        """
        def _list():
            client = self._get_client()
            backups = []

            # List objects with prefix
            paginator = client.get_paginator("list_objects_v2")

            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Only include backup archives (tar.gz)
                    if key.endswith(".tar.gz"):
                        backups.append({
                            "key": key.replace(self.prefix, ""),
                            "size": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                        })

            return backups

        try:
            return await asyncio.get_running_loop().run_in_executor(None, _list)
        except Exception as e:
            raise S3Error(f"Failed to list backups: {e}", operation="list", bucket=self.bucket)

    async def delete_backup(self, key: str) -> None:
        """
        Delete a backup from S3.

        Args:
            key: S3 object key (without prefix)

        Raises:
            S3Error: If deletion fails
        """
        full_key = self._get_full_key(key)

        logger.info(f"Deleting s3://{self.bucket}/{full_key}")

        def _delete():
            client = self._get_client()
            client.delete_object(Bucket=self.bucket, Key=full_key)

        try:
            await asyncio.get_running_loop().run_in_executor(None, _delete)
            logger.info(f"Deleted: s3://{self.bucket}/{full_key}")
        except Exception as e:
            raise S3Error(f"Failed to delete backup: {e}", operation="delete", bucket=self.bucket)

    async def verify_upload(self, key: str, expected_size: int) -> bool:
        """
        Verify uploaded object exists with correct size.

        Args:
            key: S3 object key (without prefix)
            expected_size: Expected file size in bytes

        Returns:
            True if object exists with correct size

        Raises:
            S3Error: If verification fails
        """
        full_key = self._get_full_key(key)

        def _verify():
            client = self._get_client()
            try:
                head = client.head_object(Bucket=self.bucket, Key=full_key)
                actual_size = head["ContentLength"]
                return actual_size == expected_size
            except Exception as e:
                raise S3Error(
                    f"Verification failed for s3://{self.bucket}/{full_key}: {e}",
                    operation="head_object",
                    bucket=self.bucket,
                )

        return await asyncio.get_running_loop().run_in_executor(None, _verify)


def get_s3_storage(
    bucket: Optional[str] = None,
    prefix: Optional[str] = None,
    region: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> Optional[S3Storage]:
    """
    Get S3 storage instance from configuration.

    Uses config values if parameters not provided.

    Returns:
        S3Storage instance or None if S3 is not configured
    """
    from config import (
        BACKUP_S3_BUCKET,
        BACKUP_S3_PREFIX,
        BACKUP_S3_REGION,
        BACKUP_S3_ENDPOINT_URL,
    )

    bucket = bucket or BACKUP_S3_BUCKET
    if not bucket:
        return None

    return S3Storage(
        bucket=bucket,
        prefix=prefix or BACKUP_S3_PREFIX,
        region=region or BACKUP_S3_REGION,
        endpoint_url=endpoint_url or BACKUP_S3_ENDPOINT_URL or None,
        progress_callback=progress_callback,
    )
