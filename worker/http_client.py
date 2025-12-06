"""HTTP client for worker-to-API communication."""

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


class WorkerAPIClient:
    """HTTP client for communicating with the Worker API."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 300.0):
        """
        Initialize the worker API client.

        Args:
            base_url: Base URL of the Worker API (e.g., http://localhost:9002)
            api_key: Worker API key for authentication
            timeout: Request timeout in seconds (default 5 minutes for file transfers)
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-Worker-API-Key": api_key}
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """Make an API request and handle errors."""
        client = await self._get_client()
        url = f"{self.base_url}{path}"

        try:
            resp = await client.request(
                method,
                url,
                headers=self.headers,
                json=json,
                **kwargs,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WorkerAPIError(e.response.status_code, detail)
        except httpx.RequestError as e:
            raise WorkerAPIError(0, f"Connection error: {e}")

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
        return await self._request("POST", "/api/worker/heartbeat", json=data)

    async def claim_job(self) -> dict:
        """
        Attempt to claim a transcoding job.

        Returns:
            Job info if claimed, or message indicating no jobs available
        """
        return await self._request("POST", "/api/worker/claim")

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
        return await self._request("POST", f"/api/worker/{job_id}/progress", json=data)

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
    ) -> dict:
        """
        Upload a single quality's HLS files after transcoding completes.

        Args:
            video_id: The video ID
            quality_name: Quality name (e.g., "original", "2160p", "1080p")
            output_dir: Directory containing HLS files

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

            # Upload using multipart form
            client = await self._get_client()
            url = f"{self.base_url}/api/worker/upload/{video_id}/quality/{quality_name}"

            with open(tmp_path, "rb") as f:
                files = {"file": ("quality.tar.gz", f, "application/gzip")}
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
    ) -> dict:
        """
        Upload final files (master.m3u8 and thumbnail.jpg) after all qualities uploaded.

        Args:
            video_id: The video ID
            output_dir: Directory containing the files

        Returns:
            Server response
        """
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Add master playlist
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
        return await self._request("POST", f"/api/worker/{job_id}/complete", json=data)

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
        return await self._request("POST", f"/api/worker/{job_id}/fail", json=data)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
