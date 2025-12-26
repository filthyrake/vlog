"""HTTP client for worker-to-API communication."""

import asyncio
import random
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

import httpx


class WorkerAPIError(Exception):
    """Exception raised when Worker API returns an error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API error {status_code}: {message}")


# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # seconds
DEFAULT_RETRY_MAX_DELAY = 30.0  # seconds

# Timeout presets (seconds)
TIMEOUT_HEARTBEAT = 15.0  # Short timeout for lightweight ops
TIMEOUT_CLAIM = 30.0  # Moderate timeout for claim
TIMEOUT_PROGRESS = 15.0  # Short timeout for progress updates
TIMEOUT_DEFAULT = 60.0  # Default for most operations
TIMEOUT_FILE_TRANSFER = 300.0  # Long timeout for file transfers


class WorkerAPIClient:
    """HTTP client for communicating with the Worker API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = TIMEOUT_FILE_TRANSFER,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        """
        Initialize the worker API client.

        Args:
            base_url: Base URL of the Worker API (e.g., http://localhost:9002)
            api_key: Worker API key for authentication
            timeout: Default request timeout in seconds (5 minutes for file transfers)
            max_retries: Max retry attempts for transient errors
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-Worker-API-Key": api_key}
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            # Configure connection limits for reliability
            limits = httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,  # Keep connections alive longer
            )
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=limits,
            )
        return self._client

    def _is_retryable_error(self, exc: Exception) -> bool:
        """Check if an error is transient and should be retried."""
        if isinstance(exc, httpx.TimeoutException):
            return True
        if isinstance(exc, httpx.ConnectError):
            return True
        if isinstance(exc, httpx.ReadError):
            return True
        if isinstance(exc, httpx.WriteError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            # Retry on server errors (5xx) but not client errors (4xx)
            return exc.response.status_code >= 500
        return False

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        **kwargs,
    ) -> dict:
        """Make an API request with retry logic for transient errors."""
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        retries = max_retries if max_retries is not None else self.max_retries
        req_timeout = timeout if timeout is not None else self.timeout

        last_error: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                resp = await client.request(
                    method,
                    url,
                    headers=self.headers,
                    json=json,
                    timeout=req_timeout,
                    **kwargs,
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                # Don't retry 4xx errors (except 429 rate limit)
                if e.response.status_code < 500 and e.response.status_code != 429:
                    try:
                        detail = e.response.json().get("detail", str(e))
                    except Exception:
                        detail = str(e)
                    raise WorkerAPIError(e.response.status_code, detail)
                # Will retry on 5xx or 429
            except httpx.RequestError as e:
                last_error = e
                if not self._is_retryable_error(e):
                    raise WorkerAPIError(0, f"Connection error: {e}")

            # Calculate backoff with jitter
            if attempt < retries:
                delay = min(
                    DEFAULT_RETRY_BASE_DELAY * (2**attempt),
                    DEFAULT_RETRY_MAX_DELAY,
                )
                # Add jitter (±25%)
                delay = delay * (0.75 + random.random() * 0.5)
                await asyncio.sleep(delay)

        # All retries exhausted
        if isinstance(last_error, httpx.HTTPStatusError):
            try:
                detail = last_error.response.json().get("detail", str(last_error))
            except Exception:
                detail = str(last_error)
            raise WorkerAPIError(last_error.response.status_code, detail)
        else:
            raise WorkerAPIError(0, f"Connection error after {retries + 1} attempts: {last_error}")

    async def heartbeat(
        self,
        status: str = "active",
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Send heartbeat to server.

        Args:
            status: Worker status (active, busy, idle)
            metadata: Optional metadata to update

        Returns:
            Server response with server_time
        """
        data = {"status": status}
        if metadata:
            data["metadata"] = metadata
        return await self._request(
            "POST",
            "/api/worker/heartbeat",
            json=data,
            timeout=TIMEOUT_HEARTBEAT,
        )

    async def claim_job(self, job_id: Optional[int] = None) -> dict:
        """
        Attempt to claim a transcoding job.

        Args:
            job_id: Optional specific job ID to claim (for Redis-dispatched jobs).
                    If provided, will only claim this specific job.
                    If not provided, claims any available job from the database.

        Returns:
            Job info if claimed, or message indicating no jobs available
        """
        params = {}
        if job_id is not None:
            params["job_id"] = job_id

        return await self._request(
            "POST",
            "/api/worker/claim",
            timeout=TIMEOUT_CLAIM,
            params=params if params else None,
        )

    async def update_progress(
        self,
        job_id: int,
        step: str,
        percent: int,
        qualities: Optional[List[dict]] = None,
        duration: Optional[float] = None,
        source_width: Optional[int] = None,
        source_height: Optional[int] = None,
    ) -> dict:
        """
        Update job progress.

        Args:
            job_id: The job ID
            step: Current step (download, probe, thumbnail, transcode, etc.)
            percent: Progress percentage (0-100)
            qualities: Optional list of quality progress dicts
            duration: Optional video duration in seconds
            source_width: Optional source video width
            source_height: Optional source video height

        Returns:
            Server response with extended claim_expires_at
        """
        data = {
            "current_step": step,
            "progress_percent": percent,
        }
        if qualities:
            data["quality_progress"] = qualities
        if duration is not None:
            data["duration"] = duration
        if source_width is not None:
            data["source_width"] = source_width
        if source_height is not None:
            data["source_height"] = source_height
        return await self._request(
            "POST",
            f"/api/worker/{job_id}/progress",
            json=data,
            timeout=TIMEOUT_PROGRESS,
        )

    async def download_source(self, video_id: int, dest_path: Path) -> None:
        """
        Download source file from server.

        Args:
            video_id: The video ID
            dest_path: Local path to save the file
        """
        client = await self._get_client()
        url = f"{self.base_url}/api/worker/source/{video_id}"

        try:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)

    async def upload_quality(
        self,
        video_id: int,
        quality_name: str,
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> dict:
        """
        Upload a single quality's HLS files after transcoding completes.

        Uses streaming upload with progress tracking to support extending job
        claims during large uploads (issue #266).

        Args:
            video_id: The video ID
            quality_name: Quality name (e.g., "original", "2160p", "1080p")
            output_dir: Directory containing HLS files
            progress_callback: Optional async callback(bytes_sent, total_bytes)
                               called periodically during upload to allow
                               extending job claims for long uploads.

        Returns:
            Server response
        """
        # Create tar.gz of just this quality's files
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        file_size_mb = 0.0  # Default value in case of early exception
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Add playlist file
                playlist = output_dir / f"{quality_name}.m3u8"
                if playlist.exists():
                    tar.add(playlist, arcname=playlist.name)

                # Add segment files
                for segment in output_dir.glob(f"{quality_name}_*.ts"):
                    tar.add(segment, arcname=segment.name)

            file_size = tmp_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)

            # Dynamic timeout: 5 min base + 1 min per 100MB
            upload_timeout = max(300, 300 + (file_size // (100 * 1024 * 1024)) * 60)
            upload_timeout = min(upload_timeout, 3600)  # Cap at 1 hour

            client = await self._get_client()
            url = f"{self.base_url}/api/worker/upload/{video_id}/quality/{quality_name}"

            # Use streaming upload with progress callback (like upload_hls)
            # This allows extending job claims during large quality uploads
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

            # Multipart header for file field
            multipart_header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="quality.tar.gz"\r\n'
                f"Content-Type: application/gzip\r\n\r\n"
            ).encode()

            # Multipart footer
            multipart_footer = f"\r\n--{boundary}--\r\n".encode()

            # Calculate total content length for progress tracking
            content_length = len(multipart_header) + file_size + len(multipart_footer)

            async def multipart_stream():
                """Stream the multipart form data with progress tracking."""
                yield multipart_header

                chunk_size = 1024 * 1024  # 1MB chunks
                bytes_sent = len(multipart_header)
                last_callback_time = time.time()
                callback_interval = 60.0  # Call progress callback every 60 seconds

                with open(tmp_path, "rb") as f:
                    while chunk := f.read(chunk_size):
                        yield chunk
                        bytes_sent += len(chunk)

                        # Call progress callback periodically to extend claim
                        if progress_callback:
                            now = time.time()
                            if now - last_callback_time >= callback_interval:
                                # Let exceptions propagate - ClaimExpiredError needs to stop the upload
                                await progress_callback(bytes_sent, content_length)
                                last_callback_time = now

                yield multipart_footer

            headers = {
                **self.headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(content_length),
            }

            resp = await client.post(
                url,
                content=multipart_stream(),
                headers=headers,
                timeout=upload_timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except httpx.TimeoutException as e:
            raise WorkerAPIError(
                0,
                f"Upload timeout for {quality_name} ({file_size_mb:.1f}MB): {e}",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    async def upload_finalize(
        self,
        video_id: int,
        output_dir: Path,
        skip_master: bool = False,
    ) -> dict:
        """
        Upload final files (master.m3u8 and thumbnail.jpg) after all qualities uploaded.

        Args:
            video_id: The video ID
            output_dir: Directory containing the files
            skip_master: If True, don't upload master.m3u8 (for selective retranscode)

        Returns:
            Server response
        """
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Add master playlist (unless skipped for selective retranscode)
                if not skip_master:
                    master = output_dir / "master.m3u8"
                    if master.exists():
                        tar.add(master, arcname=master.name)

                # Add thumbnail
                thumb = output_dir / "thumbnail.jpg"
                if thumb.exists():
                    tar.add(thumb, arcname=thumb.name)

            client = await self._get_client()
            url = f"{self.base_url}/api/worker/upload/{video_id}/finalize"

            with open(tmp_path, "rb") as f:
                files = {"file": ("finalize.tar.gz", f, "application/gzip")}
                resp = await client.post(
                    url,
                    files=files,
                    headers=self.headers,
                    timeout=60,  # Small files, short timeout
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def upload_hls(
        self,
        video_id: int,
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> dict:
        """
        Package and upload HLS output as tar.gz using streaming upload.

        Args:
            video_id: The video ID
            output_dir: Directory containing HLS files
            progress_callback: Optional async callback(bytes_sent, total_bytes)
                               called periodically during upload to allow
                               extending job claims for long uploads.

        Returns:
            Server response

        Note:
            Uses streaming upload to avoid loading entire tar.gz into memory.
            For large 4K videos with multiple quality variants, the tar.gz
            can be 5-15GB, which would exhaust worker memory if loaded at once.
        """
        # Create tar.gz of output directory
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                for file in output_dir.iterdir():
                    if file.is_file():
                        tar.add(file, arcname=file.name)

            # Get file size for logging
            file_size = tmp_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)

            # Upload using streaming to avoid loading entire file into memory
            # This is critical for large HLS outputs (4K videos can be 5-15GB)
            client = await self._get_client()
            url = f"{self.base_url}/api/worker/upload/{video_id}"

            # Use a longer timeout for large uploads (1 hour max)
            # Base: 5 minutes + 1 minute per 100MB
            upload_timeout = max(300, 300 + (file_size // (100 * 1024 * 1024)) * 60)
            upload_timeout = min(upload_timeout, 3600)  # Cap at 1 hour

            # Build multipart form data manually with streaming file
            # We need to create a proper multipart boundary
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

            # Multipart header for file field
            multipart_header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="hls.tar.gz"\r\n'
                f"Content-Type: application/gzip\r\n\r\n"
            ).encode()

            # Multipart footer
            multipart_footer = f"\r\n--{boundary}--\r\n".encode()

            # Calculate total content length for progress tracking
            content_length = len(multipart_header) + file_size + len(multipart_footer)

            async def multipart_stream():
                """Stream the multipart form data with progress tracking."""
                yield multipart_header

                chunk_size = 1024 * 1024  # 1MB chunks
                bytes_sent = len(multipart_header)
                last_callback_time = time.time()
                callback_interval = 60.0  # Call progress callback every 60 seconds

                with open(tmp_path, "rb") as f:
                    while chunk := f.read(chunk_size):
                        yield chunk
                        bytes_sent += len(chunk)

                        # Call progress callback periodically to extend claim
                        if progress_callback:
                            now = time.time()
                            if now - last_callback_time >= callback_interval:
                                # Let exceptions propagate - ClaimExpiredError needs to stop the upload
                                await progress_callback(bytes_sent, content_length)
                                last_callback_time = now

                yield multipart_footer

            headers = {
                **self.headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(content_length),
            }

            # Use a custom timeout for this upload
            resp = await client.post(
                url,
                content=multipart_stream(),
                headers=headers,
                timeout=upload_timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except httpx.TimeoutException as e:
            raise WorkerAPIError(
                0,
                f"Upload timeout after {upload_timeout}s for {file_size_mb:.1f}MB file: {e}",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    async def complete_job(
        self,
        job_id: int,
        qualities: List[dict],
        duration: Optional[float] = None,
        source_width: Optional[int] = None,
        source_height: Optional[int] = None,
    ) -> dict:
        """
        Mark job as complete.

        Args:
            job_id: The job ID
            qualities: List of quality info dicts with name, width, height, bitrate
            duration: Video duration in seconds
            source_width: Source video width
            source_height: Source video height

        Returns:
            Server response
        """
        data = {"qualities": qualities}
        if duration is not None:
            data["duration"] = duration
        if source_width is not None:
            data["source_width"] = source_width
        if source_height is not None:
            data["source_height"] = source_height
        return await self._request(
            "POST",
            f"/api/worker/{job_id}/complete",
            json=data,
            timeout=TIMEOUT_DEFAULT,
        )

    async def fail_job(
        self,
        job_id: int,
        error: str,
        retry: bool = True,
    ) -> dict:
        """
        Report job failure.

        Args:
            job_id: The job ID
            error: Error message
            retry: Whether to allow retry

        Returns:
            Server response with retry info
        """
        data = {
            "error_message": error[:500],
            "retry": retry,
        }
        return await self._request(
            "POST",
            f"/api/worker/{job_id}/fail",
            json=data,
            timeout=TIMEOUT_DEFAULT,
        )

    # Re-encode job methods
    async def claim_reencode_job(self) -> dict:
        """
        Attempt to claim a re-encode job from the queue.

        Returns:
            Job info if claimed, or empty dict if no jobs available
        """
        return await self._request(
            "POST",
            "/api/reencode/claim",
            timeout=TIMEOUT_CLAIM,
        )

    async def update_reencode_job(
        self,
        job_id: int,
        status: str,
        error_message: Optional[str] = None,
        retry_count: Optional[int] = None,
    ) -> dict:
        """
        Update re-encode job status.

        Args:
            job_id: The re-encode job ID
            status: New status (pending, in_progress, completed, failed)
            error_message: Optional error message for failed jobs
            retry_count: Optional retry count update

        Returns:
            Server response
        """
        data = {"status": status}
        if error_message:
            data["error_message"] = error_message
        if retry_count is not None:
            data["retry_count"] = retry_count

        return await self._request(
            "PATCH",
            f"/api/reencode/{job_id}",
            json=data,
            timeout=TIMEOUT_DEFAULT,
        )

    async def get_video_info(self, video_id: int) -> dict:
        """
        Get video information for re-encoding.

        Args:
            video_id: The video ID

        Returns:
            Video metadata including slug and current format
        """
        return await self._request(
            "GET",
            f"/api/videos/{video_id}",
            timeout=TIMEOUT_DEFAULT,
        )

    async def download_reencode_source(self, job_id: int, dest_path: Path) -> None:
        """
        Download existing video files for re-encoding.

        Args:
            job_id: The re-encode job ID
            dest_path: Local path to save the tar.gz file
        """
        client = await self._get_client()
        url = f"{self.base_url}/api/reencode/{job_id}/download"

        try:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)

    async def upload_reencode_result(
        self,
        job_id: int,
        tar_path: Path,
    ) -> dict:
        """
        Upload re-encoded video files.

        Args:
            job_id: The re-encode job ID
            tar_path: Path to the tar.gz file to upload

        Returns:
            Server response
        """
        client = await self._get_client()
        url = f"{self.base_url}/api/reencode/{job_id}/upload"

        file_size = tar_path.stat().st_size
        # Calculate timeout based on file size (5 min base + 1 min per 100MB)
        upload_timeout = max(300, 300 + (file_size // (100 * 1024 * 1024)) * 60)
        upload_timeout = min(upload_timeout, 3600)  # Cap at 1 hour

        try:
            with open(tar_path, "rb") as f:
                files = {"file": ("reencode.tar.gz", f, "application/gzip")}
                resp = await client.post(
                    url,
                    files=files,
                    headers=self.headers,
                    timeout=upload_timeout,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
