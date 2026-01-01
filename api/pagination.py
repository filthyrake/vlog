"""
Cursor-based pagination utilities for efficient large dataset traversal.

Implements keyset pagination using (timestamp, id) tuples as cursors,
avoiding the performance issues of OFFSET-based pagination at high offsets.

See: https://github.com/filthyrake/vlog/issues/463
"""

import base64
from datetime import datetime, timezone
from typing import Optional, Tuple

# Cursor format version for future compatibility
CURSOR_VERSION = "1"


def encode_cursor(timestamp: datetime, record_id: int) -> str:
    """
    Encode a (timestamp, id) tuple into an opaque cursor string.

    Args:
        timestamp: The timestamp value (e.g., published_at, created_at)
        record_id: The unique record ID

    Returns:
        Base64-encoded cursor string
    """
    # Ensure timestamp is timezone-aware
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    # Format: version|iso_timestamp|id (using pipe delimiter to avoid
    # conflict with colons in ISO timestamps)
    ts_str = timestamp.isoformat()
    cursor_data = f"{CURSOR_VERSION}|{ts_str}|{record_id}"

    # Base64 encode for URL safety
    return base64.urlsafe_b64encode(cursor_data.encode()).decode()


def decode_cursor(cursor: str) -> Optional[Tuple[datetime, int]]:
    """
    Decode an opaque cursor string into a (timestamp, id) tuple.

    Args:
        cursor: Base64-encoded cursor string

    Returns:
        Tuple of (timestamp, id) or None if invalid
    """
    try:
        # Decode base64
        cursor_data = base64.urlsafe_b64decode(cursor.encode()).decode()

        # Parse format: version|iso_timestamp|id
        parts = cursor_data.split("|", 2)
        if len(parts) != 3:
            return None

        version, ts_str, id_str = parts

        # Check version compatibility
        if version != CURSOR_VERSION:
            return None

        # Parse timestamp
        timestamp = datetime.fromisoformat(ts_str)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # Parse ID
        record_id = int(id_str)

        return (timestamp, record_id)

    except (ValueError, TypeError):
        return None


def validate_cursor(cursor: Optional[str]) -> Optional[Tuple[datetime, int]]:
    """
    Validate and decode a cursor, returning None for invalid/missing cursors.

    Args:
        cursor: Optional cursor string from query parameter

    Returns:
        Tuple of (timestamp, id) or None if cursor is missing/invalid
    """
    if not cursor:
        return None

    return decode_cursor(cursor)
