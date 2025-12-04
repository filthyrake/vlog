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

from config import ADMIN_PORT

# Download timeout in seconds (default 1 hour, configurable via environment)
DOWNLOAD_TIMEOUT = int(os.getenv("VLOG_DOWNLOAD_TIMEOUT", "3600"))

# Default timeout for API requests (30 seconds)
DEFAULT_API_TIMEOUT = int(os.getenv("VLOG_API_TIMEOUT", "30"))

# Admin API URL - can override host and port, or use the port from config
_default_api_url = f"http://localhost:{ADMIN_PORT}"
API_BASE = os.getenv("VLOG_ADMIN_API_URL", _default_api_url).rstrip("/") + "/api"


class CLIError(Exception):
    """Custom exception for CLI errors."""

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
            detail = response.text[:200] if response.text else default_error
        raise CLIError(f"API error ({response.status_code}): {detail}")

    # Try to parse JSON from successful response
    try:
        return response.json()
    except (ValueError, httpx.ResponseNotRead):
        raise CLIError(f"Invalid JSON response: {response.text[:100]}")


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

    # Warn about very large files (> 10GB)
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
    if result.scheme not in ('http', 'https'):
        raise CLIError(f"Invalid URL scheme: '{result.scheme}'. Use http or https.")
    if not result.netloc:
        raise CLIError("Invalid URL: missing domain")
    return url


def cmd_upload(args):
    """Upload a video."""
    try:
        file_path = Path(args.file)
        validate_file(file_path)

        title = args.title or file_path.stem.replace("-", " ").replace("_", " ").title()

        print(f"Uploading: {file_path.name}")
        print(f"Title: {title}")

        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {
                "title": title,
                "description": args.description or "",
            }
            if args.category:
                # Look up category ID by name/slug
                try:
                    response = httpx.get(f"{API_BASE}/categories", timeout=DEFAULT_API_TIMEOUT)
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

            with httpx.Client(timeout=None) as client:
                response = client.post(f"{API_BASE}/videos", files=files, data=data)

            result = safe_json_response(response)
            print("Success! Video queued for processing.")
            print(f"  ID: {result['video_id']}")
            print(f"  Slug: {result['slug']}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        print("Make sure the admin server is running.")
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


def cmd_list(args):
    """List videos."""
    try:
        params = {}
        if args.status:
            params["status"] = args.status

        response = httpx.get(f"{API_BASE}/videos", params=params, timeout=DEFAULT_API_TIMEOUT)
        videos = safe_json_response(response)

        if not videos:
            print("No videos found.")
            return

        print(f"{'ID':<5} {'Status':<12} {'Title':<40} {'Category':<15}")
        print("-" * 75)
        for v in videos:
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
                timeout=DEFAULT_API_TIMEOUT,
            )
            cat = safe_json_response(response)
            print(f"Created category: {cat['name']} (slug: {cat['slug']})")
        else:
            response = httpx.get(f"{API_BASE}/categories", timeout=DEFAULT_API_TIMEOUT)
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
        response = httpx.delete(f"{API_BASE}/videos/{args.video_id}", timeout=DEFAULT_API_TIMEOUT)
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
            validate_file(video_file)

            title = args.title or video_file.stem

            print(f"Downloaded: {video_file.name}")
            print(f"Uploading as: {title}")

            # Upload the video
            with open(video_file, "rb") as f:
                files = {"file": (video_file.name, f)}
                data = {
                    "title": title,
                    "description": args.description or "",
                }
                if args.category:
                    try:
                        response = httpx.get(f"{API_BASE}/categories", timeout=DEFAULT_API_TIMEOUT)
                        cats = safe_json_response(response)
                        for cat in cats:
                            if cat["name"].lower() == args.category.lower() or cat["slug"] == args.category:
                                data["category_id"] = cat["id"]
                                break
                    except (CLIError, httpx.ConnectError, httpx.TimeoutException) as e:
                        print(f"Warning: Could not fetch categories: {e}")
                        print("Uploading without category")

                with httpx.Client(timeout=None) as client:
                    response = client.post(f"{API_BASE}/videos", files=files, data=data)

                result = safe_json_response(response)
                print("Success! Video queued for processing.")
                print(f"  ID: {result['video_id']}")
                print(f"  Slug: {result['slug']}")

    except httpx.ConnectError:
        print(f"Error: Could not connect to admin API at {API_BASE}")
        print("Make sure the admin server is running.")
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
    list_parser.set_defaults(func=cmd_list)

    # Categories command
    cat_parser = subparsers.add_parser("categories", help="List or create categories")
    cat_parser.add_argument("--create", metavar="NAME", help="Create a new category")
    cat_parser.add_argument("-d", "--description", help="Category description (with --create)")
    cat_parser.set_defaults(func=cmd_categories)

    # Delete command
    del_parser = subparsers.add_parser("delete", help="Delete a video")
    del_parser.add_argument("video_id", type=int, help="Video ID to delete")
    del_parser.set_defaults(func=cmd_delete)

    # Download command (from YouTube)
    dl_parser = subparsers.add_parser("download", help="Download from YouTube and upload")
    dl_parser.add_argument("url", help="Video URL (YouTube, Vimeo, and many other sites supported)")
    dl_parser.add_argument("-t", "--title", help="Override video title")
    dl_parser.add_argument("-d", "--description", help="Video description")
    dl_parser.add_argument("-c", "--category", help="Category name or slug")
    dl_parser.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
