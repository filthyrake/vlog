#!/usr/bin/env python3
"""
VLog CLI - Command line interface for video management.
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TotalFileSizeColumn,
    TransferSpeedColumn,
)

from api.errors import truncate_error
from config import (
    ADMIN_API_SECRET,
    ADMIN_PORT,
    ERROR_DETAIL_MAX_LENGTH,
    ERROR_SUMMARY_MAX_LENGTH,
    MAX_UPLOAD_SIZE,
    WORKER_ADMIN_SECRET,
    WORKER_API_PORT,
)


# Import for settings migration (lazy to avoid circular imports)
def _get_settings_module():
    """Lazy import settings module to avoid circular imports."""
    from api import settings_service
    return settings_service

# Download timeout in seconds (default 1 hour, configurable via environment)
DOWNLOAD_TIMEOUT = int(os.getenv("VLOG_DOWNLOAD_TIMEOUT", "3600"))


def positive_int(value: str) -> int:
    """Argparse type converter that validates positive integers."""
    i = int(value)
    if i <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {i}")
    return i

# Default timeout for API requests (30 seconds)
DEFAULT_API_TIMEOUT = int(os.getenv("VLOG_API_TIMEOUT", "30"))

# Upload timeout in seconds (default 2 hours, configurable via environment)
# Very long timeout for large uploads, but not infinite to prevent hanging
UPLOAD_TIMEOUT = int(os.getenv("VLOG_UPLOAD_TIMEOUT", "7200"))

# Admin API URL - can override host and port, or use the port from config
_default_api_url = f"http://localhost:{ADMIN_PORT}"
API_BASE = os.getenv("VLOG_ADMIN_API_URL", _default_api_url).rstrip("/") + "/api"

# Worker API URL - can override host and port
_default_worker_api_url = f"http://localhost:{WORKER_API_PORT}"
WORKER_API_BASE = os.getenv("VLOG_WORKER_API_URL", _default_worker_api_url).rstrip("/") + "/api"


class CLIError(Exception):
    """Custom exception for CLI errors."""

    pass


class ProgressFileWrapper:
    """Wrapper for file objects that reports upload progress."""

    def __init__(self, file, progress, task_id):
        """
        Initialize the progress file wrapper.

        Args:
            file: The file object to wrap
            progress: The rich Progress instance
            task_id: The task ID from progress.add_task()
        """
        self.file = file
        self.progress = progress
        self.task_id = task_id

    def read(self, size=-1):
        """
        Read from the file and update progress.

        Only updates progress when data is actually read (non-empty).
        Empty reads at EOF don't advance progress as no bytes were transferred.
        """
        data = self.file.read(size)
        if data:
            self.progress.update(self.task_id, advance=len(data))
        return data

    def seek(self, *args, **kwargs):
        """
        Forward seek to the underlying file.

        Note: Seek does not affect progress tracking. Progress is only
        advanced when bytes are read via read(), ensuring accurate tracking
        even if the file position changes.
        """
        return self.file.seek(*args, **kwargs)

    def tell(self):
        """Forward tell to the underlying file."""
        return self.file.tell()

    def close(self):
        """Close method - does not close the underlying file as it's managed externally."""
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit - does not close the file as it's managed externally."""
        pass


def safe_json_response(response, default_error="Request failed"):
    """
    Safely parse JSON response with proper error handling.

    Args:
        response: httpx.Response object
        default_error: Default error message if response has no detail

    Returns:
        Parsed JSON data if successful

    Raises:
        CLIError: If response status is not successful or JSON parsing fails
    """
    if not response.is_success:
        # Try to extract error detail from response
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, httpx.ResponseNotRead):
            # If JSON parsing fails, use raw text or default
            detail = truncate_error(response.text, ERROR_DETAIL_MAX_LENGTH) if response.text else default_error
        raise CLIError(f"API error ({response.status_code}): {detail}")

    # Try to parse JSON from successful response
    try:
        return response.json()
    except (ValueError, httpx.ResponseNotRead):
        raise CLIError(f"Invalid JSON response: {truncate_error(response.text, ERROR_SUMMARY_MAX_LENGTH)}")


def validate_file(file_path):
    """
    Validate file exists and is readable.

    Args:
        file_path: Path object pointing to the file

    Returns:
        int: File size in bytes

    Raises:
        CLIError: If file doesn't exist, isn't readable, or is empty
    """
    if not file_path.exists():
        raise CLIError(f"File not found: {file_path}")

    if not file_path.is_file():
        raise CLIError(f"Path is not a file: {file_path}")

    if not os.access(file_path, os.R_OK):
        raise CLIError(f"File is not readable: {file_path}")

    # Check if file is empty
    file_size = file_path.stat().st_size
    if file_size == 0:
        raise CLIError(f"File is empty: {file_path}")

    # Enforce max upload size limit
    if file_size > MAX_UPLOAD_SIZE:
        max_size_gb = MAX_UPLOAD_SIZE / (1024 * 1024 * 1024)
        file_size_gb = file_size / (1024 * 1024 * 1024)
        raise CLIError(
            f"File too large ({file_size_gb:.2f} GB). "
            f"Maximum upload size is {max_size_gb:.0f} GB"
        )

    # Warn about large files (> 10GB) that may take a while to upload
    if file_size > 10 * 1024 * 1024 * 1024:
        print(f"Warning: Large file detected ({file_size / (1024**3):.2f} GB). Upload may take a while.")

    return file_size


def validate_url(url):
    """
    Validate URL before passing to yt-dlp.

    Args:
        url: URL string to validate

    Returns:
        str: The validated URL

    Raises:
        CLIError: If URL is invalid (wrong scheme, missing domain, or malformed)
    """
    result = urlparse(url)
    if not result.scheme:
        raise CLIError("Invalid URL: missing scheme. Use http:// or https://")
    if result.scheme not in ("http", "https"):
        raise CLIError(f"Invalid URL scheme: '{result.scheme}'. Use http or https.")
    if not result.netloc:
        raise CLIError("Invalid URL: missing domain")
    return url


def get_admin_headers() -> dict:
    """Get headers for admin API requests."""
    headers = {}
    if ADMIN_API_SECRET:
        headers["X-Admin-Secret"] = ADMIN_API_SECRET
    return headers


def handle_auth_error(response) -> bool:
    """
    Check for auth errors and provide helpful message.

    Returns True if an auth error was handled (and program should exit).
    """
    if response.status_code == 401:
        print("Error: Authentication required.")
        print("The admin API requires authentication. Set VLOG_ADMIN_API_SECRET environment variable.")
        sys.exit(1)
    elif response.status_code == 403:
        print("Error: Authentication failed - invalid secret.")
        print("Check that VLOG_ADMIN_API_SECRET matches the server configuration.")
        sys.exit(1)
    return False


def cmd_upload(args):
    """Upload a video."""
    try:
        file_path = Path(args.file)
        file_size = validate_file(file_path)

        title = args.title or file_path.stem.replace("-", " ").replace("_", " ").title()

        print(f"Uploading: {file_path.name}")
        print(f"Title: {title}")

        # Prepare data payload
        data = {
            "title": title,
            "description": args.description or "",
        }
        if args.category:
            # Look up category ID by name/slug
            try:
                response = httpx.get(f"{API_BASE}/categories", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
                handle_auth_error(response)
                cats = safe_json_response(response)
                cat_match = None
                for cat in cats:
                    if cat["name"].lower() == args.category.lower() or cat["slug"] == args.category:
                        cat_match = cat
                        break
                if cat_match:
                    data["category_id"] = cat_match["id"]
                else:
                    print(f"Warning: Category '{args.category}' not found, uploading without category")
            except (CLIError, httpx.ConnectError, httpx.TimeoutException) as e:
                print(f"Warning: Could not fetch categories: {e}")
                print("Uploading without category")

        # Upload with progress indicator
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            FileSizeColumn(),
            TextColumn("/"),
            TotalFileSizeColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task_id = progress.add_task("Uploading...", total=file_size)

            with open(file_path, "rb") as f:
                # Wrap the file object with progress tracking
                wrapped_file = ProgressFileWrapper(f, progress, task_id)
                files = {"file": (file_path.name, wrapped_file)}

                with httpx.Client(timeout=httpx.Timeout(UPLOAD_TIMEOUT)) as client:
                    response = client.post(f"{API_BASE}/videos", files=files, data=data, headers=get_admin_headers())

        handle_auth_error(response)
        result = safe_json_response(response)
        print("Success! Video queued for processing.")
        print(f"  ID: {result['video_id']}")
        print(f"  Slug: {result['slug']}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        print("Make sure the admin server is running.")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Upload timed out (exceeded {UPLOAD_TIMEOUT}s timeout)")
        print("You can increase the timeout with VLOG_UPLOAD_TIMEOUT environment variable")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_list(args):
    """List videos."""
    try:
        if args.archived:
            # List archived/deleted videos
            if args.status:
                print("Warning: --status is ignored when listing archived videos")

            response = httpx.get(f"{API_BASE}/videos/archived", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
            handle_auth_error(response)
            result = safe_json_response(response)
            videos_list = result.get("videos", [])
            total = result.get("total", len(videos_list))

            if not videos_list:
                print("No archived videos found.")
                return

            print(f"Archived videos ({total} total):")
            print(f"{'ID':<5} {'Deleted At':<20} {'Title':<40} {'Slug':<20}")
            print("-" * 90)
            for v in videos_list:
                title = v["title"][:38] + ".." if len(v["title"]) > 40 else v["title"]
                slug = v["slug"][:18] + ".." if len(v["slug"]) > 20 else v["slug"]
                deleted_at = v["deleted_at"][:19] if v["deleted_at"] else "-"
                print(f"{v['id']:<5} {deleted_at:<20} {title:<40} {slug:<20}")
        else:
            # List active videos
            params = {}
            if args.status:
                params["status"] = args.status

            response = httpx.get(f"{API_BASE}/videos", params=params, headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
            handle_auth_error(response)
            videos_list = safe_json_response(response)

            if not videos_list:
                print("No videos found.")
                return

            print(f"{'ID':<5} {'Status':<12} {'Title':<40} {'Category':<15}")
            print("-" * 75)
            for v in videos_list:
                title = v["title"][:38] + ".." if len(v["title"]) > 40 else v["title"]
                cat = v["category_name"] or "-"
                print(f"{v['id']:<5} {v['status']:<12} {title:<40} {cat:<15}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Request timed out while connecting to {API_BASE}")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_categories(args):
    """List or create categories."""
    try:
        if args.create:
            response = httpx.post(
                f"{API_BASE}/categories",
                json={"name": args.create, "description": args.description or ""},
                headers=get_admin_headers(),
                timeout=DEFAULT_API_TIMEOUT,
            )
            handle_auth_error(response)
            cat = safe_json_response(response)
            print(f"Created category: {cat['name']} (slug: {cat['slug']})")
        else:
            response = httpx.get(f"{API_BASE}/categories", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
            handle_auth_error(response)
            categories = safe_json_response(response)

            if not categories:
                print("No categories found.")
                return

            print(f"{'ID':<5} {'Name':<25} {'Slug':<25} {'Videos':<10}")
            print("-" * 65)
            for c in categories:
                print(f"{c['id']:<5} {c['name']:<25} {c['slug']:<25} {c['video_count']:<10}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Request timed out while connecting to {API_BASE}")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_delete(args):
    """Delete a video."""
    try:
        response = httpx.delete(f"{API_BASE}/videos/{args.video_id}", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
        handle_auth_error(response)
        safe_json_response(response)  # Will raise CLIError if not successful
        print(f"Video {args.video_id} deleted.")
    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Request timed out while connecting to {API_BASE}")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_download(args):
    """Download a video from YouTube and upload it."""
    try:
        import subprocess
        import tempfile
    except ImportError:
        print("Error: Required modules not available")
        sys.exit(1)

    # Validate URL before proceeding
    try:
        validate_url(args.url)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Check if yt-dlp is available
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: yt-dlp is not installed. Install with: pip install yt-dlp")
        sys.exit(1)

    print(f"Downloading: {args.url}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = f"{tmpdir}/%(title)s.%(ext)s"

            cmd = [
                "yt-dlp",
                "-f",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format",
                "mp4",
                "-o",
                output_template,
                args.url,
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT)
            except subprocess.TimeoutExpired:
                print(f"Error: Download timed out after {DOWNLOAD_TIMEOUT} seconds")
                print("You can increase the timeout with VLOG_DOWNLOAD_TIMEOUT environment variable")
                sys.exit(1)

            if result.returncode != 0:
                print(f"Error downloading: {result.stderr}")
                sys.exit(1)

            # Find the downloaded file
            downloaded = list(Path(tmpdir).glob("*.mp4"))
            if not downloaded:
                downloaded = list(Path(tmpdir).glob("*"))

            if not downloaded:
                print("Error: No file was downloaded")
                sys.exit(1)

            video_file = downloaded[0]

            # Validate the downloaded file
            file_size = validate_file(video_file)

            title = args.title or video_file.stem

            print(f"Downloaded: {video_file.name}")
            print(f"Uploading as: {title}")

            # Prepare data payload
            data = {
                "title": title,
                "description": args.description or "",
            }
            if args.category:
                try:
                    response = httpx.get(f"{API_BASE}/categories", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
                    handle_auth_error(response)
                    cats = safe_json_response(response)
                    for cat in cats:
                        if cat["name"].lower() == args.category.lower() or cat["slug"] == args.category:
                            data["category_id"] = cat["id"]
                            break
                except (CLIError, httpx.ConnectError, httpx.TimeoutException) as e:
                    print(f"Warning: Could not fetch categories: {e}")
                    print("Uploading without category")

            # Upload with progress indicator
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                FileSizeColumn(),
                TextColumn("/"),
                TotalFileSizeColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task_id = progress.add_task("Uploading...", total=file_size)

                with open(video_file, "rb") as f:
                    # Wrap the file object with progress tracking
                    wrapped_file = ProgressFileWrapper(f, progress, task_id)
                    files = {"file": (video_file.name, wrapped_file)}

                    with httpx.Client(timeout=httpx.Timeout(UPLOAD_TIMEOUT)) as client:
                        response = client.post(f"{API_BASE}/videos", files=files, data=data, headers=get_admin_headers())

            handle_auth_error(response)
            result = safe_json_response(response)
            print("Success! Video queued for processing.")
            print(f"  ID: {result['video_id']}")
            print(f"  Slug: {result['slug']}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        print("Make sure the admin server is running.")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Upload timed out (exceeded {UPLOAD_TIMEOUT}s timeout)")
        print("You can increase the timeout with VLOG_UPLOAD_TIMEOUT environment variable")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_worker(args):
    """Worker management commands."""
    # Check for admin secret - required for worker management
    # Prefer ADMIN_API_SECRET, fall back to WORKER_ADMIN_SECRET for backwards compatibility
    worker_admin_secret = ADMIN_API_SECRET or WORKER_ADMIN_SECRET
    if not worker_admin_secret:
        print("Error: VLOG_ADMIN_API_SECRET environment variable is required for worker management.")
        print()
        print("Generate a secret with:")
        print('  python -c "import secrets; print(secrets.token_urlsafe(32))"')
        print()
        print("Then set it in your environment:")
        print("  export VLOG_ADMIN_API_SECRET=<your-secret>")
        sys.exit(1)

    # Headers for admin authentication
    admin_headers = {"X-Admin-Secret": worker_admin_secret}

    try:
        if args.worker_command == "register":
            # Register a new worker
            data = {"worker_type": args.type}
            if args.name:
                data["worker_name"] = args.name

            response = httpx.post(
                f"{WORKER_API_BASE}/worker/register",
                json=data,
                headers=admin_headers,
                timeout=DEFAULT_API_TIMEOUT,
            )
            result = safe_json_response(response)

            print("Worker registered successfully!")
            print(f"  Worker ID: {result['worker_id']}")
            print(f"  API Key: {result['api_key']}")
            print()
            print("IMPORTANT: Save the API key - it will not be shown again!")
            print()
            print("To use this worker, set the environment variable:")
            print(f"  export VLOG_WORKER_API_KEY={result['api_key']}")

        elif args.worker_command == "list":
            # List all workers
            response = httpx.get(
                f"{WORKER_API_BASE}/workers",
                headers=admin_headers,
                timeout=DEFAULT_API_TIMEOUT,
            )
            result = safe_json_response(response)

            workers = result.get("workers", [])
            if not workers:
                print("No workers registered.")
                return

            print(f"Workers: {result['active_count']} active, {result['offline_count']} offline")
            print()
            print(f"{'ID':<10} {'Name':<20} {'Type':<8} {'Status':<10} {'Last Heartbeat':<20} {'Current Job':<15}")
            print("-" * 90)

            for w in workers:
                worker_id = w["worker_id"][:8] + "..."
                name = (w["worker_name"] or "-")[:18]
                wtype = w["worker_type"]
                status = w["status"]
                heartbeat = w["last_heartbeat"][:19] if w["last_heartbeat"] else "-"
                job = w.get("current_video_slug") or "-"

                print(f"{worker_id:<10} {name:<20} {wtype:<8} {status:<10} {heartbeat:<20} {job:<15}")

        elif args.worker_command == "revoke":
            # Revoke a worker's API key
            response = httpx.post(
                f"{WORKER_API_BASE}/workers/{args.worker_id}/revoke",
                headers=admin_headers,
                timeout=DEFAULT_API_TIMEOUT,
            )
            safe_json_response(response)
            print(f"Worker {args.worker_id} has been revoked.")

        elif args.worker_command == "status":
            # Show worker status summary
            response = httpx.get(
                f"{WORKER_API_BASE}/workers",
                headers=admin_headers,
                timeout=DEFAULT_API_TIMEOUT,
            )
            result = safe_json_response(response)

            print("Worker Status Summary")
            print("=" * 40)
            print(f"  Total workers: {result['total_count']}")
            print(f"  Active: {result['active_count']}")
            print(f"  Offline: {result['offline_count']}")

            # Show active workers with current jobs
            active = [w for w in result.get("workers", []) if w["status"] == "active"]
            if active:
                print()
                print("Active Workers:")
                for w in active:
                    name = w["worker_name"] or w["worker_id"][:8]
                    job = w.get("current_video_slug")
                    if job:
                        print(f"  {name}: processing {job}")
                    else:
                        print(f"  {name}: idle")

    except httpx.ConnectError:
        print(f"Error: Could not connect to Worker API at {WORKER_API_BASE}")
        print("Make sure the worker API server is running (port 9002).")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"Error: Request timed out while connecting to {WORKER_API_BASE}")
        sys.exit(1)
    except CLIError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def cmd_settings(args):
    """Settings management commands."""
    import asyncio

    if args.settings_command == "migrate-from-env":
        # Migrate settings from environment variables to database
        print("Migrating settings from environment variables to database...")
        print()

        from api.database import configure_database, database

        try:
            settings_module = _get_settings_module()

            async def do_migration():
                # Connect to database for CLI migration
                await database.connect()
                await configure_database()
                try:
                    return await settings_module.seed_settings_from_env(updated_by="cli-migration")
                finally:
                    await database.disconnect()

            result = asyncio.run(do_migration())

            seeded = result.get("seeded", 0)
            skipped = result.get("skipped", 0)
            details = result.get("details", [])

            if seeded > 0:
                print(f"✓ Migrated {seeded} settings to database")
                for d in details:
                    if d["status"] == "seeded":
                        print(f"  • {d['key']}: {d['value']} (from {d['from_env']})")
                print()

            if skipped > 0 and args.verbose:
                print(f"Skipped {skipped} settings (already exist or no env var)")
                for d in details:
                    if d["status"] == "skipped":
                        print(f"  • {d['key']}: {d.get('reason', 'unknown')}")
                print()

            # Show which env vars are safe to remove
            safe_to_remove = []
            for d in details:
                if d["status"] == "seeded":
                    safe_to_remove.append(d["from_env"])

            if safe_to_remove:
                print("The following environment variables can now be removed from .env:")
                for env_var in sorted(safe_to_remove):
                    print(f"  {env_var}")
                print()
                print("Note: Keep bootstrap variables (ports, paths, secrets, database URL)")

            if seeded == 0 and skipped > 0:
                print("No new settings to migrate. Settings may already exist in the database.")
                print("Use --verbose to see skipped settings.")

        except Exception as e:
            print(f"Error during migration: {e}")
            sys.exit(1)

    elif args.settings_command == "list":
        # List settings from database
        try:
            response = httpx.get(f"{API_BASE}/settings", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
            handle_auth_error(response)
            result = safe_json_response(response)

            # API returns {"categories": {...}}
            categories_data = result.get("categories", result) if isinstance(result, dict) else {}

            if not categories_data:
                print("No settings found in database.")
                print("Run 'vlog settings migrate-from-env' to migrate from environment variables.")
                return

            for category, settings_list in categories_data.items():
                print(f"\n[{category}]")
                for s in settings_list:
                    value_display = s["value"]
                    if isinstance(value_display, str) and len(value_display) > 50:
                        value_display = value_display[:47] + "..."
                    print(f"  {s['key']}: {value_display}")

        except httpx.ConnectError:
            print(f"Error: Could not connect to admin API at {API_BASE}")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"Error: Request timed out while connecting to {API_BASE}")
            sys.exit(1)
        except CLIError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.settings_command == "get":
        # Get a single setting
        key = args.key
        try:
            response = httpx.get(f"{API_BASE}/settings/key/{key}", headers=get_admin_headers(), timeout=DEFAULT_API_TIMEOUT)
            handle_auth_error(response)
            result = safe_json_response(response)

            print(f"Key: {result['key']}")
            print(f"Value: {result['value']}")
            print(f"Type: {result['value_type']}")
            print(f"Category: {result['category']}")
            if result.get("description"):
                print(f"Description: {result['description']}")
            if result.get("constraints"):
                print(f"Constraints: {result['constraints']}")

        except httpx.ConnectError:
            print(f"Error: Could not connect to admin API at {API_BASE}")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"Error: Request timed out while connecting to {API_BASE}")
            sys.exit(1)
        except CLIError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.settings_command == "set":
        # Set a setting value
        key = args.key
        value = args.value

        # Try to parse as JSON first (for booleans, numbers, etc.)
        import json as json_module
        try:
            parsed_value = json_module.loads(value)
        except json_module.JSONDecodeError:
            # Keep as string
            parsed_value = value

        try:
            response = httpx.post(
                f"{API_BASE}/settings",
                json={"key": key, "value": parsed_value},
                headers=get_admin_headers(),
                timeout=DEFAULT_API_TIMEOUT,
            )
            handle_auth_error(response)
            safe_json_response(response)
            print(f"Setting updated: {key} = {parsed_value}")

        except httpx.ConnectError:
            print(f"Error: Could not connect to admin API at {API_BASE}")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"Error: Request timed out while connecting to {API_BASE}")
            sys.exit(1)
        except CLIError as e:
            error_msg = str(e)
            # Check for common validation errors and provide helpful messages
            if "must be" in error_msg.lower() or "invalid" in error_msg.lower():
                print(f"Validation error for '{key}': {error_msg}")
                print("\nUse 'vlog settings get <key>' to see valid constraints.")
            elif "not found" in error_msg.lower():
                print(f"Setting '{key}' not found in database.")
                print("Run 'vlog settings migrate-from-env' first, or use 'vlog settings list' to see available settings.")
            else:
                print(f"Error: {error_msg}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="vlog", description="VLog CLI - Manage your video library")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Upload command
    upload_parser = subparsers.add_parser("upload", help="Upload a video file")
    upload_parser.add_argument("file", help="Video file to upload")
    upload_parser.add_argument("-t", "--title", help="Video title (default: filename)")
    upload_parser.add_argument("-d", "--description", help="Video description")
    upload_parser.add_argument("-c", "--category", help="Category name or slug")
    upload_parser.set_defaults(func=cmd_upload)

    # List command
    list_parser = subparsers.add_parser("list", help="List videos")
    list_parser.add_argument(
        "-s", "--status", choices=["pending", "processing", "ready", "failed"], help="Filter by status"
    )
    list_parser.add_argument(
        "--archived", action="store_true", help="List archived/deleted videos instead of active videos"
    )
    list_parser.set_defaults(func=cmd_list)

    # Categories command
    cat_parser = subparsers.add_parser("categories", help="List or create categories")
    cat_parser.add_argument("--create", metavar="NAME", help="Create a new category")
    cat_parser.add_argument("-d", "--description", help="Category description (with --create)")
    cat_parser.set_defaults(func=cmd_categories)

    # Delete command
    del_parser = subparsers.add_parser("delete", help="Delete a video")
    del_parser.add_argument("video_id", type=positive_int, help="Video ID to delete")
    del_parser.set_defaults(func=cmd_delete)

    # Download command (from YouTube)
    dl_parser = subparsers.add_parser("download", help="Download from YouTube and upload")
    dl_parser.add_argument("url", help="Video URL (YouTube, Vimeo, and many other sites supported)")
    dl_parser.add_argument("-t", "--title", help="Override video title")
    dl_parser.add_argument("-d", "--description", help="Video description")
    dl_parser.add_argument("-c", "--category", help="Category name or slug")
    dl_parser.set_defaults(func=cmd_download)

    # Worker management command
    worker_parser = subparsers.add_parser("worker", help="Manage transcoding workers")
    worker_subparsers = worker_parser.add_subparsers(dest="worker_command", required=True)

    # worker register
    worker_register = worker_subparsers.add_parser("register", help="Register a new worker")
    worker_register.add_argument("-n", "--name", help="Worker name (optional)")
    worker_register.add_argument(
        "-t", "--type", choices=["local", "remote"], default="remote", help="Worker type (default: remote)"
    )

    # worker list
    worker_subparsers.add_parser("list", help="List all registered workers")

    # worker status
    worker_subparsers.add_parser("status", help="Show worker status summary")

    # worker revoke
    worker_revoke = worker_subparsers.add_parser("revoke", help="Revoke a worker's API key")
    worker_revoke.add_argument("worker_id", help="Worker ID (UUID) to revoke")

    worker_parser.set_defaults(func=cmd_worker)

    # Settings management command
    settings_parser = subparsers.add_parser("settings", help="Manage database-backed settings")
    settings_subparsers = settings_parser.add_subparsers(dest="settings_command", required=True)

    # settings migrate-from-env
    migrate_parser = settings_subparsers.add_parser(
        "migrate-from-env",
        help="Migrate settings from environment variables to database",
    )
    migrate_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show skipped settings details"
    )

    # settings list
    settings_subparsers.add_parser("list", help="List all settings from database")

    # settings get
    get_parser = settings_subparsers.add_parser("get", help="Get a single setting value")
    get_parser.add_argument("key", help="Setting key (e.g., transcoding.hls_segment_duration)")

    # settings set
    set_parser = settings_subparsers.add_parser("set", help="Set a setting value")
    set_parser.add_argument("key", help="Setting key")
    set_parser.add_argument("value", help="New value (JSON-parseable for numbers/booleans)")

    settings_parser.set_defaults(func=cmd_settings)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
