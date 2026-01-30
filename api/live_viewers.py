"""Live stream viewer tracking.

This module handles:
- Viewer join (server-generated session ID)
- Heartbeat updates (keep-alive)
- Explicit leave
- Viewer count management
- Privacy-preserving IP hashing

Security decisions per Bruce's review:
- Session IDs are SERVER-GENERATED (not client-provided)
- IP addresses are hashed with HMAC-SHA256 using per-instance secret
- Rate limiting on all public endpoints
"""

import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from api.database import live_stream_viewers, live_streams
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.pubsub import Publisher
from config import VIEWER_IP_HASH_SECRET

logger = logging.getLogger(__name__)


def generate_session_id() -> str:
    """
    Generate a cryptographically secure session ID.

    Returns 256 bits of entropy encoded as URL-safe base64.
    """
    return secrets.token_urlsafe(32)


def hash_ip_address(ip_address: str) -> Optional[str]:
    """
    Hash an IP address using HMAC-SHA256 for privacy-preserving tracking.

    Args:
        ip_address: The client IP address

    Returns:
        HMAC-SHA256 hash of the IP, or None if secret not configured
    """
    if not VIEWER_IP_HASH_SECRET:
        return None

    return hmac.new(
        key=VIEWER_IP_HASH_SECRET.encode("utf-8"),
        msg=ip_address.encode("utf-8"),
        digestmod="sha256",
    ).hexdigest()


async def viewer_join(
    stream_id: int,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    quality: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Register a new viewer for a stream.

    Generates a server-side session ID and tracks the viewer.
    Updates viewer counts on the stream.

    Args:
        stream_id: Stream being watched
        user_id: Optional authenticated user ID
        ip_address: Client IP for privacy-preserving hash
        quality: Quality being watched

    Returns:
        Dict with session_id and stream info
    """
    now = datetime.now(timezone.utc)
    session_id = generate_session_id()
    ip_hash = hash_ip_address(ip_address) if ip_address else None

    # Insert viewer record
    await db_execute_with_retry(
        live_stream_viewers.insert().values(
            stream_id=stream_id,
            session_id=session_id,
            user_id=user_id,
            joined_at=now,
            last_heartbeat=now,
            quality_watched=quality,
            ip_hash=ip_hash,
        )
    )

    # Update stream viewer counts
    # Increment current and total, update peak if needed
    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == stream_id)
        .values(
            viewer_count_current=live_streams.c.viewer_count_current + 1,
            viewer_count_total=live_streams.c.viewer_count_total + 1,
            viewer_count_peak=sa.func.greatest(
                live_streams.c.viewer_count_peak,
                live_streams.c.viewer_count_current + 1,
            ),
        )
    )

    # Fetch updated counts and publish to pub/sub (per Ada's review)
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    if stream:
        await Publisher.publish_viewer_count(
            stream_id=stream_id,
            current=stream["viewer_count_current"],
            peak=stream["viewer_count_peak"],
            total=stream["viewer_count_total"],
        )

    logger.debug(f"Viewer joined stream {stream_id} with session {session_id[:8]}...")

    return {
        "session_id": session_id,
        "joined_at": now.isoformat(),
    }


async def viewer_heartbeat(
    stream_id: int,
    session_id: str,
    quality: Optional[str] = None,
) -> bool:
    """
    Update viewer heartbeat (keep-alive).

    Uses upsert pattern to handle race conditions gracefully.

    Args:
        stream_id: Stream being watched
        session_id: Server-generated session ID
        quality: Current quality being watched

    Returns:
        True if heartbeat was recorded, False if session not found
    """
    now = datetime.now(timezone.utc)

    # Use INSERT ... ON CONFLICT UPDATE for race-condition safety
    # This handles the case where cleanup runs between check and update
    stmt = insert(live_stream_viewers).values(
        stream_id=stream_id,
        session_id=session_id,
        joined_at=now,
        last_heartbeat=now,
        quality_watched=quality,
    ).on_conflict_do_update(
        index_elements=["stream_id", "session_id"],
        set_={
            "last_heartbeat": now,
            "quality_watched": quality,
            # Clear left_at if viewer is back (rejoin scenario)
            "left_at": None,
        },
        where=live_stream_viewers.c.left_at.is_(None),  # Only update if not already left
    )

    try:
        await db_execute_with_retry(stmt)
        return True
    except Exception as e:
        logger.warning(f"Heartbeat failed for session {session_id[:8]}...: {e}")
        return False


async def viewer_leave(
    stream_id: int,
    session_id: str,
) -> bool:
    """
    Explicitly mark a viewer as having left.

    Updates viewer counts on the stream.

    Args:
        stream_id: Stream being watched
        session_id: Server-generated session ID

    Returns:
        True if viewer was marked as left, False if not found
    """
    now = datetime.now(timezone.utc)

    # Only mark as left if not already left
    result = await db_execute_with_retry(
        live_stream_viewers.update()
        .where(live_stream_viewers.c.stream_id == stream_id)
        .where(live_stream_viewers.c.session_id == session_id)
        .where(live_stream_viewers.c.left_at.is_(None))
        .values(left_at=now)
    )

    if result:
        # Decrement viewer count
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream_id)
            .values(
                viewer_count_current=sa.func.greatest(
                    0, live_streams.c.viewer_count_current - 1
                )
            )
        )

        # Fetch updated counts and publish to pub/sub (per Ada's review)
        stream = await fetch_one_with_retry(
            live_streams.select().where(live_streams.c.id == stream_id)
        )
        if stream:
            await Publisher.publish_viewer_count(
                stream_id=stream_id,
                current=stream["viewer_count_current"],
                peak=stream["viewer_count_peak"],
                total=stream["viewer_count_total"],
            )

        logger.debug(f"Viewer left stream {stream_id} session {session_id[:8]}...")
        return True

    return False


async def get_stream_viewer_stats(stream_id: int) -> Dict[str, Any]:
    """
    Get viewer statistics for a stream.

    Args:
        stream_id: Stream ID

    Returns:
        Dict with current, peak, total counts and quality distribution
    """
    # Get stream counts
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        return {
            "current": 0,
            "peak": 0,
            "total": 0,
            "quality_distribution": {},
        }

    # Get quality distribution for active viewers
    active_viewers = await fetch_all_with_retry(
        live_stream_viewers.select()
        .where(live_stream_viewers.c.stream_id == stream_id)
        .where(live_stream_viewers.c.left_at.is_(None))
    )

    quality_counts = {}
    for viewer in active_viewers:
        quality = viewer["quality_watched"] or "unknown"
        quality_counts[quality] = quality_counts.get(quality, 0) + 1

    return {
        "current": stream["viewer_count_current"],
        "peak": stream["viewer_count_peak"],
        "total": stream["viewer_count_total"],
        "quality_distribution": quality_counts,
    }


async def get_active_viewers(
    stream_id: int,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Get list of active viewers for a stream (for broadcaster dashboard).

    Args:
        stream_id: Stream ID
        limit: Maximum number of viewers to return

    Returns:
        List of viewer records (with user_id if authenticated, else anonymous)
    """
    viewers = await fetch_all_with_retry(
        live_stream_viewers.select()
        .where(live_stream_viewers.c.stream_id == stream_id)
        .where(live_stream_viewers.c.left_at.is_(None))
        .order_by(live_stream_viewers.c.joined_at.desc())
        .limit(limit)
    )

    return [
        {
            "session_id_prefix": viewer["session_id"][:8],  # Only show prefix for privacy
            "user_id": viewer["user_id"],
            "joined_at": viewer["joined_at"].isoformat() if viewer["joined_at"] else None,
            "quality": viewer["quality_watched"],
        }
        for viewer in viewers
    ]
