#!/usr/bin/env python3
"""
Health check script for remote transcoding workers.

This script performs comprehensive health checks to ensure the worker
is functioning properly, including:
- Worker API connectivity
- FFmpeg availability
- Optional GPU availability check

Used by Docker HEALTHCHECK and Kubernetes liveness/readiness probes.

Exit codes:
    0: Healthy
    1: Unhealthy
"""

import asyncio
import glob
import os
import subprocess
import sys
from typing import Tuple

# Import worker API client for connectivity check
try:
    from config import WORKER_API_KEY, WORKER_API_URL
    from worker.http_client import WorkerAPIClient, WorkerAPIError
except ImportError as e:
    print(f"ERROR: Failed to import dependencies: {e}", file=sys.stderr)
    sys.exit(1)


def check_ffmpeg() -> Tuple[bool, str]:
    """
    Check if FFmpeg is available and functional.

    Returns:
        Tuple of (success: bool, error_message: str)
    """
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5, text=True)
        if result.returncode != 0:
            return False, f"FFmpeg returned non-zero exit code: {result.returncode}"

        # Check if output contains expected version string
        if "ffmpeg version" not in result.stdout.lower():
            return False, "FFmpeg output doesn't contain version info"

        return True, ""
    except FileNotFoundError:
        return False, "FFmpeg not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg version check timed out"
    except Exception as e:
        return False, f"FFmpeg check failed: {e}"


def check_gpu_optional() -> Tuple[bool, str]:
    """
    Optional GPU availability check.

    This is a non-critical check - if GPU is expected but not available,
    we return a warning but don't fail the health check since CPU fallback
    is available.

    Returns:
        Tuple of (success: bool, warning_message: str)
    """
    hwaccel_type = os.getenv("VLOG_HWACCEL_TYPE", "auto")

    # If hwaccel is explicitly disabled, skip GPU check
    if hwaccel_type == "none":
        return True, ""

    # Check for NVIDIA GPU
    if hwaccel_type in ("nvidia", "auto"):
        try:
            result = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5, text=True)
            if result.returncode == 0 and "GPU" in result.stdout:
                return True, ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Check for Intel GPU (VAAPI)
    if hwaccel_type in ("intel", "auto"):
        try:
            # Check for /dev/dri/renderD* devices
            dri_devices = glob.glob("/dev/dri/renderD*")
            if dri_devices:
                return True, ""
        except Exception:
            pass

    # If we expected a GPU but didn't find one, return warning
    if hwaccel_type in ("nvidia", "intel"):
        return True, f"GPU type {hwaccel_type} configured but not detected (CPU fallback available)"

    # For "auto" mode, no GPU is okay
    return True, ""


async def check_api_connectivity() -> Tuple[bool, str]:
    """
    Check connectivity to the Worker API.

    Returns:
        Tuple of (success: bool, error_message: str)
    """
    # Check if API key is configured
    if not WORKER_API_KEY:
        return False, "VLOG_WORKER_API_KEY not configured"

    client = WorkerAPIClient(WORKER_API_URL, WORKER_API_KEY)

    try:
        # Try to send a heartbeat to verify connectivity
        # Use status "idle" to indicate this is just a health check
        await client.heartbeat(status="idle")
        return True, ""
    except WorkerAPIError as e:
        # Authentication errors are critical
        if e.status_code == 401:
            return False, "Worker API authentication failed (invalid API key)"
        elif e.status_code == 403:
            return False, "Worker API access forbidden"
        # Network/server errors might be temporary
        elif e.status_code >= 500:
            return False, f"Worker API server error: {e.message}"
        else:
            return False, f"Worker API error: {e.message}"
    except asyncio.TimeoutError:
        return False, "Worker API connection timed out"
    except Exception as e:
        return False, f"Worker API connectivity check failed: {e}"
    finally:
        await client.close()


async def main() -> int:
    """
    Run all health checks.

    Returns:
        Exit code: 0 for healthy, 1 for unhealthy
    """
    all_checks_passed = True
    warnings = []

    # Check 1: FFmpeg availability (critical)
    success, error = check_ffmpeg()
    if not success:
        print(f"UNHEALTHY: FFmpeg check failed: {error}", file=sys.stderr)
        all_checks_passed = False
    else:
        print("✓ FFmpeg is available")

    # Check 2: Worker API connectivity (critical)
    success, error = await check_api_connectivity()
    if not success:
        print(f"UNHEALTHY: Worker API connectivity failed: {error}", file=sys.stderr)
        all_checks_passed = False
    else:
        print("✓ Worker API is reachable")

    # Check 3: GPU availability (non-critical)
    success, warning = check_gpu_optional()
    if warning:
        warnings.append(warning)
        print(f"⚠ {warning}")
    elif success:
        hwaccel_type = os.getenv("VLOG_HWACCEL_TYPE", "auto")
        if hwaccel_type != "none":
            print("✓ GPU hardware acceleration is available")

    # Print warnings if any
    if warnings and all_checks_passed:
        print("\nHealth check passed with warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    # Return appropriate exit code
    if all_checks_passed:
        print("\nWorker is HEALTHY")
        return 0
    else:
        print("\nWorker is UNHEALTHY", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nHealth check interrupted", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Health check failed with exception: {e}", file=sys.stderr)
        sys.exit(1)
