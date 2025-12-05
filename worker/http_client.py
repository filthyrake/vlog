"""HTTP client for worker-to-API communication."""
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional

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
    ) -> dict:
        """
        Update job progress.

        Args:
            job_id: The job ID
            step: Current step (download, probe, thumbnail, transcode, etc.)
            percent: Progress percentage (0-100)
            qualities: Optional list of quality progress dicts

        Returns:
            Server response with extended claim_expires_at
        """
        data = {
            "current_step": step,
            "progress_percent": percent,
        }
        if qualities:
            data["quality_progress"] = qualities
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

    async def upload_hls(self, video_id: int, output_dir: Path) -> dict:
        """
        Package and upload HLS output as tar.gz.

        Args:
            video_id: The video ID
            output_dir: Directory containing HLS files

        Returns:
            Server response
        """
        # Create tar.gz of output directory
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                for file in output_dir.iterdir():
                    if file.is_file():
                        tar.add(file, arcname=file.name)

            # Upload
            client = await self._get_client()
            url = f"{self.base_url}/api/worker/upload/{video_id}"

            with open(tmp_path, "rb") as f:
                files = {"file": ("hls.tar.gz", f, "application/gzip")}
                resp = await client.post(url, headers=self.headers, files=files)
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
