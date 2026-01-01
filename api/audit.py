"""
Audit logging for administrative actions.

Provides structured audit logging for security and operational tracking.
Logs to a file with JSON-formatted entries for easy parsing and analysis.

Related Issue: #38
"""

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from api.errors import truncate_string
from config import (
    AUDIT_LOG_BACKUP_COUNT,
    AUDIT_LOG_ENABLED,
    AUDIT_LOG_LEVEL,
    AUDIT_LOG_MAX_BYTES,
    AUDIT_LOG_PATH,
    ERROR_DETAIL_MAX_LENGTH,
)

# Ensure log directory exists (skip in test mode)
if not os.environ.get("VLOG_TEST_MODE") and AUDIT_LOG_ENABLED:
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # Will fall back to console logging


class AuditAction(str, Enum):
    """Audit action types for categorization."""

    # Video actions
    VIDEO_UPLOAD = "video_upload"
    VIDEO_UPDATE = "video_update"
    VIDEO_DELETE = "video_delete"
    VIDEO_RESTORE = "video_restore"
    VIDEO_RETRY = "video_retry"
    VIDEO_REUPLOAD = "video_reupload"
    VIDEO_RETRANSCODE = "video_retranscode"

    # Bulk video actions
    VIDEO_BULK_DELETE = "video_bulk_delete"
    VIDEO_BULK_UPDATE = "video_bulk_update"
    VIDEO_BULK_RETRANSCODE = "video_bulk_retranscode"
    VIDEO_BULK_RESTORE = "video_bulk_restore"
    VIDEO_EXPORT = "video_export"

    # Category actions
    CATEGORY_CREATE = "category_create"
    CATEGORY_DELETE = "category_delete"

    # Tag actions
    TAG_CREATE = "tag_create"
    TAG_UPDATE = "tag_update"
    TAG_DELETE = "tag_delete"
    VIDEO_TAGS_UPDATE = "video_tags_update"

    # Custom field actions
    CUSTOM_FIELD_CREATE = "custom_field_create"
    CUSTOM_FIELD_UPDATE = "custom_field_update"
    CUSTOM_FIELD_DELETE = "custom_field_delete"
    VIDEO_CUSTOM_FIELDS_UPDATE = "video_custom_fields_update"
    VIDEO_CUSTOM_FIELDS_BULK_UPDATE = "video_custom_fields_bulk_update"

    # Transcription actions
    TRANSCRIPTION_TRIGGER = "transcription_trigger"
    TRANSCRIPTION_UPDATE = "transcription_update"
    TRANSCRIPTION_DELETE = "transcription_delete"

    # Transcoding events
    TRANSCODING_START = "transcoding_start"
    TRANSCODING_COMPLETE = "transcoding_complete"
    TRANSCODING_FAILED = "transcoding_failed"

    # Worker actions
    WORKER_REGISTER = "worker_register"
    WORKER_REVOKE = "worker_revoke"
    WORKER_DISABLE = "worker_disable"
    WORKER_ENABLE = "worker_enable"
    WORKER_DELETE = "worker_delete"

    # Settings actions
    SETTINGS_CHANGE = "settings_change"

    # Playlist actions
    PLAYLIST_CREATE = "playlist_create"
    PLAYLIST_UPDATE = "playlist_update"
    PLAYLIST_DELETE = "playlist_delete"
    PLAYLIST_VIDEO_ADD = "playlist_video_add"
    PLAYLIST_VIDEO_REMOVE = "playlist_video_remove"
    PLAYLIST_REORDER = "playlist_reorder"


class AuditLogger:
    """
    Structured audit logger for administrative actions.

    Logs events in JSON format for easy parsing and analysis.
    Falls back to console logging if file logging is unavailable.
    """

    def __init__(self):
        self.logger = logging.getLogger("vlog.audit")
        self.logger.setLevel(getattr(logging, AUDIT_LOG_LEVEL, logging.INFO))
        self.logger.propagate = False  # Don't propagate to root logger

        if not self.logger.handlers:
            self._setup_handlers()

    def _setup_handlers(self):
        """Set up logging handlers with rotation support."""
        formatter = logging.Formatter("%(message)s")  # Raw JSON output

        if AUDIT_LOG_ENABLED:
            try:
                # Use RotatingFileHandler for automatic log rotation
                # Rotates when file reaches AUDIT_LOG_MAX_BYTES (default 10MB)
                # Keeps AUDIT_LOG_BACKUP_COUNT backup files (default 5)
                file_handler = RotatingFileHandler(
                    AUDIT_LOG_PATH,
                    maxBytes=AUDIT_LOG_MAX_BYTES,
                    backupCount=AUDIT_LOG_BACKUP_COUNT,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except (PermissionError, OSError):
                # Fall back to console logging
                console_handler = logging.StreamHandler()
                console_handler.setFormatter(formatter)
                self.logger.addHandler(console_handler)
        else:
            # Logging disabled, use null handler
            self.logger.addHandler(logging.NullHandler())

    def log(
        self,
        action: AuditAction,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[Any] = None,
        resource_name: Optional[str] = None,
        details: Optional[dict] = None,
        success: bool = True,
        error: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """
        Log an audit event.

        Args:
            action: The type of action being performed
            client_ip: IP address of the client making the request
            user_agent: User-Agent header from the request
            resource_type: Type of resource (video, category, etc.)
            resource_id: ID of the affected resource
            resource_name: Human-readable name of the resource (slug, title, etc.)
            details: Additional action-specific details
            success: Whether the action succeeded
            error: Error message if action failed
            request_id: Unique request ID for tracing across services
        """
        if not AUDIT_LOG_ENABLED:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action.value,
            "success": success,
        }

        if request_id:
            entry["request_id"] = request_id
        if client_ip:
            entry["client_ip"] = client_ip
        if user_agent:
            entry["user_agent"] = truncate_string(user_agent, ERROR_DETAIL_MAX_LENGTH)  # Truncate long user agents
        if resource_type:
            entry["resource_type"] = resource_type
        if resource_id is not None:
            entry["resource_id"] = resource_id
        if resource_name:
            entry["resource_name"] = resource_name
        if details:
            entry["details"] = details
        if error:
            entry["error"] = error[:500]  # Truncate long errors

        try:
            self.logger.info(json.dumps(entry, default=str))
        except Exception:
            # Never let audit logging break the application
            pass


# Singleton instance for use across the application
audit_logger = AuditLogger()


def log_audit(
    action: AuditAction,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[Any] = None,
    resource_name: Optional[str] = None,
    details: Optional[dict] = None,
    success: bool = True,
    error: Optional[str] = None,
    request_id: Optional[str] = None,
):
    """
    Convenience function for logging audit events.

    Example usage:
        log_audit(
            AuditAction.VIDEO_UPLOAD,
            client_ip=request.client.host,
            resource_type="video",
            resource_id=video_id,
            resource_name=slug,
            details={"title": title, "category_id": category_id},
            request_id=get_request_id(request)
        )
    """
    audit_logger.log(
        action=action,
        client_ip=client_ip,
        user_agent=user_agent,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        details=details,
        success=success,
        error=error,
        request_id=request_id,
    )
