"""
Code version tracking for worker compatibility checks.

This file is updated during Docker build with the git commit hash.
The server uses this to reject workers running outdated code.

Build process:
    docker build --build-arg CODE_VERSION=$(git rev-parse --short HEAD) ...

The Dockerfile should write the build arg to this file or set it as an env var.
"""

import os

# Code version - set during Docker build or read from environment
# Format: short git commit hash (7 chars) or "dev" for local development
CODE_VERSION = os.environ.get("VLOG_CODE_VERSION", "dev")

# Build timestamp - set during Docker build
BUILD_TIMESTAMP = os.environ.get("VLOG_BUILD_TIMESTAMP", "")

# For local development, try to read from git
if CODE_VERSION == "dev":
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            CODE_VERSION = result.stdout.strip()
    except Exception:
        pass  # Keep "dev" if git is unavailable


def get_version_info() -> dict:
    """Get full version information for debugging."""
    return {
        "code_version": CODE_VERSION,
        "build_timestamp": BUILD_TIMESTAMP or "unknown",
    }
