"""HTTP client for worker-to-API communication."""

import asyncio
import logging
import random
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

import httpx

logger = logging.getLogger(__name__)


class WorkerAPIError(Exception):
    """Exception raised when Worker API returns an error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API error {status_code}: {message}")


class CircuitBreakerOpen(WorkerAPIError):
    """Exception raised when circuit breaker is open."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(0, f"Circuit breaker open - API unavailable, retry after {retry_after:.1f}s")


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

# Circuit breaker configuration (Issue #453)
# Opens circuit after consecutive failures, preventing wasted retry time
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3  # Open circuit after 3 consecutive failures
CIRCUIT_BREAKER_BASE_RESET_SECONDS = 30.0  # Base reset time (doubles each time)
CIRCUIT_BREAKER_MAX_RESET_SECONDS = 300.0  # Max reset time (5 minutes)


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

        # Circuit breaker state (Issue #453)
        # Prevents wasting time on retries when API is down
        self._circuit_open = False
        self._circuit_open_until: Optional[datetime] = None
        self._consecutive_failures = 0
        self._circuit_open_count = 0  # Track how many times circuit has opened
        self._half_open = False  # True when testing if circuit should close

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open and raise if so.

        Raises:
            CircuitBreakerOpen: If circuit is open and reset time hasn't passed
        """
        if not self._circuit_open:
            return

        now = datetime.now()
        if self._circuit_open_until and now < self._circuit_open_until:
            retry_after = (self._circuit_open_until - now).total_seconds()
            raise CircuitBreakerOpen(retry_after)

        # Reset time has passed, try half-open state (allow one probe request through)
        # If the probe succeeds, circuit closes. If it fails, circuit re-opens immediately.
        logger.info("Circuit breaker entering half-open state, allowing probe request")
        self._circuit_open = False
        self._half_open = True

    def _record_success(self) -> None:
        """Record a successful request, resetting circuit breaker."""
        if self._half_open:
            logger.info("Half-open probe succeeded, closing circuit breaker")
        elif self._consecutive_failures > 0:
            logger.info(f"API request succeeded after {self._consecutive_failures} failures, resetting circuit breaker")
        self._consecutive_failures = 0
        self._circuit_open = False
        self._circuit_open_count = 0
        self._half_open = False

    def _record_failure(self) -> None:
        """Record a failed request, potentially opening circuit breaker."""
        self._consecutive_failures += 1

        # If in half-open state, immediately re-open circuit on any failure
        if self._half_open:
            self._half_open = False
            self._circuit_open = True
            self._circuit_open_count += 1

            # Exponential backoff for reset time (doubles each time circuit opens)
            reset_seconds = min(
                CIRCUIT_BREAKER_BASE_RESET_SECONDS * (2 ** (self._circuit_open_count - 1)),
                CIRCUIT_BREAKER_MAX_RESET_SECONDS,
            )
            self._circuit_open_until = datetime.now() + timedelta(seconds=reset_seconds)

            logger.warning(
                f"Half-open probe failed, re-opening circuit breaker. "
                f"Will retry after {reset_seconds:.1f}s (open count: {self._circuit_open_count})"
            )
            return

        if self._consecutive_failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self._circuit_open = True
            self._circuit_open_count += 1

            # Exponential backoff for reset time (doubles each time circuit opens)
            reset_seconds = min(
                CIRCUIT_BREAKER_BASE_RESET_SECONDS * (2 ** (self._circuit_open_count - 1)),
                CIRCUIT_BREAKER_MAX_RESET_SECONDS,
            )
            self._circuit_open_until = datetime.now() + timedelta(seconds=reset_seconds)

            logger.warning(
                f"Circuit breaker opened after {self._consecutive_failures} consecutive failures. "
                f"Will retry after {reset_seconds:.1f}s (open count: {self._circuit_open_count})"
            )

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
        """Make an API request with retry logic and circuit breaker (Issue #453)."""
        # Check circuit breaker before attempting request
        self._check_circuit_breaker()

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
                # Success - reset circuit breaker
                self._record_success()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                # Don't retry 4xx errors (except 429 rate limit)
                if e.response.status_code < 500 and e.response.status_code != 429:
                    # Client error - don't count against circuit breaker
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

        # All retries exhausted - record failure for circuit breaker
        self._record_failure()
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
        code_version: Optional[str] = None,
    ) -> dict:
        """
        Send heartbeat to server.

        Args:
            status: Worker status (active, busy, idle)
            metadata: Optional metadata to update
            code_version: Worker's code version for compatibility checking

        Returns:
            Server response with server_time, required_version, version_ok
        """
        data = {"status": status}
        if metadata:
            data["metadata"] = metadata
        if code_version:
            data["code_version"] = code_version
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
        # Check circuit breaker before attempting download
        self._check_circuit_breaker()

        client = await self._get_client()
        url = f"{self.base_url}/api/worker/source/{video_id}"

        try:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
            # Success - record for circuit breaker
            self._record_success()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            # Connection/timeout errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Download failed: {e}")

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
        # Check circuit breaker before attempting upload
        self._check_circuit_breaker()

        # Create tar.gz of just this quality's files
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        file_size_mb = 0.0  # Default value in case of early exception
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Check for CMAF subdirectory structure (output_dir/{quality_name}/)
                cmaf_dir = output_dir / quality_name
                if cmaf_dir.is_dir():
                    # CMAF format: files are in subdirectory
                    # Add all files from the quality subdirectory
                    for f in cmaf_dir.iterdir():
                        if f.is_file():
                            # Preserve subdirectory structure: {quality_name}/filename
                            tar.add(f, arcname=f"{quality_name}/{f.name}")
                else:
                    # HLS/TS format: files are in root with quality prefix
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
            # Success - record for circuit breaker
            self._record_success()
            return resp.json()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except httpx.TimeoutException as e:
            # Timeout counts against circuit breaker
            self._record_failure()
            raise WorkerAPIError(
                0,
                f"Upload timeout for {quality_name} ({file_size_mb:.1f}MB): {e}",
            )
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as e:
            # Connection errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Upload failed for {quality_name}: {e}")
        finally:
            tmp_path.unlink(missing_ok=True)

    async def upload_finalize(
        self,
        video_id: int,
        output_dir: Path,
        skip_master: bool = False,
    ) -> dict:
        """
        Upload final files (master.m3u8, manifest.mpd, and thumbnail.jpg) after all qualities uploaded.

        Args:
            video_id: The video ID
            output_dir: Directory containing the files
            skip_master: If True, don't upload master.m3u8 (for selective retranscode)

        Returns:
            Server response
        """
        # Check circuit breaker before attempting upload
        self._check_circuit_breaker()

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Add master playlist (unless skipped for selective retranscode)
                if not skip_master:
                    master = output_dir / "master.m3u8"
                    if master.exists():
                        tar.add(master, arcname=master.name)

                # Add DASH manifest (for CMAF streaming)
                mpd = output_dir / "manifest.mpd"
                if mpd.exists():
                    tar.add(mpd, arcname=mpd.name)

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
                # Success - record for circuit breaker
                self._record_success()
                return resp.json()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError) as e:
            # Connection/timeout errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Upload finalize failed: {e}")
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
        # Check circuit breaker before attempting upload
        self._check_circuit_breaker()

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
            # Success - record for circuit breaker
            self._record_success()
            return resp.json()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except httpx.TimeoutException as e:
            # Timeout counts against circuit breaker
            self._record_failure()
            raise WorkerAPIError(
                0,
                f"Upload timeout after {upload_timeout}s for {file_size_mb:.1f}MB file: {e}",
            )
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as e:
            # Connection errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Upload HLS failed: {e}")
        finally:
            tmp_path.unlink(missing_ok=True)

    async def complete_job(
        self,
        job_id: int,
        qualities: List[dict],
        duration: Optional[float] = None,
        source_width: Optional[int] = None,
        source_height: Optional[int] = None,
        streaming_format: Optional[str] = None,
        streaming_codec: Optional[str] = None,
    ) -> dict:
        """
        Mark job as complete.

        Issue #455: Generates a unique completion_token for idempotency on retry.
        If the request fails and is retried, the server will recognize the token
        and return early if the completion was already processed.

        Args:
            job_id: The job ID
            qualities: List of quality info dicts with name, width, height, bitrate
            duration: Video duration in seconds
            source_width: Source video width
            source_height: Source video height
            streaming_format: Streaming format used ("hls_ts" or "cmaf")
            streaming_codec: Video codec used ("h264", "hevc", "av1")

        Returns:
            Server response
        """
        # Issue #455: Generate idempotency token for retry safety
        completion_token = f"{job_id}-{uuid.uuid4()}"

        data = {"qualities": qualities, "completion_token": completion_token}
        if duration is not None:
            data["duration"] = duration
        if source_width is not None:
            data["source_width"] = source_width
        if source_height is not None:
            data["source_height"] = source_height
        if streaming_format is not None:
            data["streaming_format"] = streaming_format
        if streaming_codec is not None:
            data["streaming_codec"] = streaming_codec
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
        # Check circuit breaker before attempting download
        self._check_circuit_breaker()

        client = await self._get_client()
        url = f"{self.base_url}/api/reencode/{job_id}/download"

        try:
            async with client.stream("GET", url, headers=self.headers) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
            # Success - record for circuit breaker
            self._record_success()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            # Connection/timeout errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Download reencode source failed: {e}")

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
        # Check circuit breaker before attempting upload
        self._check_circuit_breaker()

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
                # Success - record for circuit breaker
                self._record_success()
                return resp.json()
        except httpx.HTTPStatusError as e:
            # Record failure for 5xx errors (server issues)
            if e.response.status_code >= 500:
                self._record_failure()
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError) as e:
            # Connection/timeout errors count against circuit breaker
            self._record_failure()
            raise WorkerAPIError(0, f"Upload reencode result failed: {e}")

    # =========================================================================
    # Streaming Segment Upload Methods (Issue #478)
    # =========================================================================

    async def upload_segment(
        self,
        video_id: int,
        quality: str,
        filename: str,
        data: bytes,
        checksum: str,
        timeout: float = 60.0,
    ) -> dict:
        """
        Upload a single segment file to the server (Issue #478).

        This method uploads individual segments as FFmpeg writes them,
        eliminating the blocking tar.gz creation that caused heartbeat failures.

        Features:
        - SHA256 checksum verification (Ada's integrity guarantee)
        - 60s timeout (reasonable for typical segment sizes)
        - Exponential backoff retry via _request

        Args:
            video_id: The video ID
            quality: Quality name (e.g., "1080p", "720p")
            filename: Segment filename (e.g., "seg_0001.m4s", "init.mp4")
            data: Raw segment bytes
            checksum: SHA256 hex digest of the data

        Returns:
            Server response with write status and checksum verification

        Raises:
            WorkerAPIError: On HTTP error or connection failure
            WorkerAPIError(409, ...): If claim has expired
        """
        # Check circuit breaker before attempting upload
        self._check_circuit_breaker()

        client = await self._get_client()
        url = f"{self.base_url}/api/worker/upload/{video_id}/segment/{quality}/{filename}"

        headers = {
            **self.headers,
            "Content-Type": "application/octet-stream",
            "X-Content-SHA256": checksum,
        }

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post(
                    url,
                    content=data,
                    headers=headers,
                    timeout=timeout,
                )
                resp.raise_for_status()
                self._record_success()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                # Don't retry 4xx errors
                if e.response.status_code < 500:
                    try:
                        detail = e.response.json().get("detail", str(e))
                    except Exception:
                        detail = str(e)
                    raise WorkerAPIError(e.response.status_code, detail)
            except httpx.RequestError as e:
                last_error = e
                if not self._is_retryable_error(e):
                    raise WorkerAPIError(0, f"Upload segment failed: {e}")

            # Backoff before retry
            if attempt < self.max_retries:
                delay = min(
                    DEFAULT_RETRY_BASE_DELAY * (2**attempt),
                    DEFAULT_RETRY_MAX_DELAY,
                )
                delay = delay * (0.75 + random.random() * 0.5)
                await asyncio.sleep(delay)

        # All retries exhausted
        self._record_failure()
        if isinstance(last_error, httpx.HTTPStatusError):
            try:
                detail = last_error.response.json().get("detail", str(last_error))
            except Exception:
                detail = str(last_error)
            raise WorkerAPIError(last_error.response.status_code, detail)
        else:
            raise WorkerAPIError(0, f"Upload segment failed after retries: {last_error}")

    async def get_segments_status(
        self,
        video_id: int,
        quality: str,
    ) -> dict:
        """
        Get status of uploaded segments for resume support (Issue #478).

        Query the server to find which segments have already been received
        for a given quality. Used when resuming after worker restart.

        Args:
            video_id: The video ID
            quality: Quality name to check

        Returns:
            Dict with:
                - quality: Quality name
                - received_segments: List of filenames already received
                - total_size_bytes: Total bytes received

        Raises:
            WorkerAPIError: On HTTP error or connection failure
        """
        return await self._request(
            "GET",
            f"/api/worker/upload/{video_id}/segments/status",
            params={"quality": quality},
            timeout=TIMEOUT_DEFAULT,
        )

    async def finalize_quality_upload(
        self,
        video_id: int,
        quality: str,
        segment_count: int,
        manifest_checksum: str = None,
    ) -> dict:
        """
        Finalize a quality's segment upload (Issue #478).

        Called after all segments for a quality are uploaded.
        Server verifies segment count matches expected before marking complete.

        Args:
            video_id: The video ID
            quality: Quality name (e.g., "1080p")
            segment_count: Expected number of segment files
            manifest_checksum: Optional SHA256 of manifest file

        Returns:
            Dict with:
                - status: "ok" or "incomplete"
                - complete: True if all segments received
                - missing_segments: List of missing segment names (if incomplete)

        Raises:
            WorkerAPIError: On HTTP error or connection failure
            WorkerAPIError(409, ...): If claim has expired
        """
        data = {
            "quality": quality,
            "segment_count": segment_count,
        }
        if manifest_checksum:
            data["manifest_checksum"] = manifest_checksum

        return await self._request(
            "POST",
            f"/api/worker/upload/{video_id}/segment/finalize",
            json=data,
            timeout=TIMEOUT_DEFAULT,
        )

    async def verify_job_complete(self, job_id: int) -> dict:
        """
        Verify that job completion was recorded and files are present (Issue #461).

        Workers call this before cleaning up their local work directory to ensure
        the server has all the files and the completion was recorded in the database.

        Args:
            job_id: The job ID to verify

        Returns:
            Dict with:
                - all_files_present: True if all expected files exist on disk
                - video_status: Current status in database
                - job_completed: Whether job completion was recorded
                - qualities_present: List of quality directories found
                - missing_files: List of any expected but missing files

        Raises:
            WorkerAPIError: On HTTP error or connection failure
        """
        return await self._request(
            "GET",
            f"/api/worker/{job_id}/verify-complete",
            timeout=TIMEOUT_DEFAULT,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
