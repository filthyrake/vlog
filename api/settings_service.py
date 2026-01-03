"""
Database-backed settings service with caching and environment variable fallback.

Provides runtime configuration management via the settings database table,
replacing 100+ environment variables with a centralized, UI-manageable system.

Key features:
- In-memory caching with configurable TTL (default: 60s)
- Falls back to environment variables during migration period
- Type coercion and validation based on value_type and constraints
- Thread-safe cache operations

See: https://github.com/filthyrake/vlog/issues/400
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.db_retry import (
    db_execute_with_retry,
    fetch_all_with_retry,
    fetch_one_with_retry,
    fetch_val_with_retry,
)
from api.errors import is_unique_violation

logger = logging.getLogger(__name__)


class UniqueConstraintError(Exception):
    """Raised when attempting to create a setting with a duplicate key."""

    pass


class SettingsValidationError(Exception):
    """Raised when a setting value fails validation."""

    pass


class SettingsService:
    """
    Database-backed settings service with caching and env var fallback.

    Usage:
        # Get a setting (with fallback to env var and default)
        segment_duration = await settings_service.get("transcoding.hls_segment_duration", 6)

        # Set a setting
        await settings_service.set("transcoding.hls_segment_duration", 10, updated_by="admin")

        # Get all settings in a category
        transcoding_settings = await settings_service.get_category("transcoding")

        # Invalidate cache (e.g., after bulk update)
        settings_service.invalidate_cache()
    """

    # Cache configuration
    DEFAULT_CACHE_TTL = 60  # seconds

    def __init__(self, cache_ttl: int = DEFAULT_CACHE_TTL):
        """
        Initialize the settings service.

        Args:
            cache_ttl: Time-to-live for cached settings in seconds
        """
        self._cache: Dict[str, Any] = {}
        self._cache_metadata: Dict[str, Dict[str, Any]] = {}  # For type info
        self._cache_ttl = cache_ttl
        self._cache_updated: float = 0
        self._cache_loaded: bool = False
        self._refresh_lock: Optional[asyncio.Lock] = None  # Lazy init for async context
        # Thread-safe lock for initializing _refresh_lock (Issue #429)
        self._init_lock = threading.Lock()

    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid (not expired)."""
        if not self._cache_loaded:
            return False
        return (time.time() - self._cache_updated) < self._cache_ttl

    async def _refresh_cache(self) -> None:
        """Refresh the cache from the database."""
        from api.database import settings as settings_table

        try:
            query = settings_table.select()
            rows = await fetch_all_with_retry(query)

            new_cache: Dict[str, Any] = {}
            new_metadata: Dict[str, Dict[str, Any]] = {}

            for row in rows:
                key = row["key"]
                value_type = row["value_type"]
                raw_value = row["value"]

                # Parse JSON-encoded value
                try:
                    parsed_value = json.loads(raw_value)
                except json.JSONDecodeError:
                    # Fallback to raw string for legacy data
                    parsed_value = raw_value

                # Coerce to expected type
                coerced_value = self._coerce_value(parsed_value, value_type)
                new_cache[key] = coerced_value

                # Store metadata for validation
                new_metadata[key] = {
                    "value_type": value_type,
                    "category": row["category"],
                    "description": row["description"],
                    "constraints": json.loads(row["constraints"]) if row["constraints"] else None,
                }

            self._cache = new_cache
            self._cache_metadata = new_metadata
            self._cache_updated = time.time()
            self._cache_loaded = True

            logger.debug(f"Settings cache refreshed: {len(new_cache)} settings loaded")

        except Exception as e:
            logger.warning(f"Failed to refresh settings cache: {e}")
            # Keep using stale cache if available
            if not self._cache_loaded:
                self._cache = {}
                self._cache_metadata = {}

    async def _refresh_cache_if_needed(self) -> None:
        """Refresh cache if expired or not loaded, with lock to prevent concurrent refreshes."""
        if self._is_cache_valid():
            return

        # Thread-safe lazy initialization of async lock (Issue #429)
        # Uses double-checked locking pattern to avoid race condition
        if self._refresh_lock is None:
            with self._init_lock:
                if self._refresh_lock is None:
                    self._refresh_lock = asyncio.Lock()

        # Use lock to prevent multiple concurrent refreshes
        async with self._refresh_lock:
            # Double-check after acquiring lock (another coroutine may have refreshed)
            if self._is_cache_valid():
                return
            await self._refresh_cache()

    def _coerce_value(self, value: Any, value_type: str) -> Any:
        """
        Coerce a value to the expected type.

        Args:
            value: The value to coerce
            value_type: Target type (string, integer, float, boolean, enum, json)

        Returns:
            Coerced value
        """
        if value is None:
            return None

        if value_type == "string":
            return str(value)
        elif value_type == "integer":
            return int(value)
        elif value_type == "float":
            return float(value)
        elif value_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)
        elif value_type == "enum":
            return str(value)
        elif value_type == "json":
            return value  # Already parsed from JSON
        else:
            return value

    def _parse_env_value(self, env_value: str, value_type: str = "string") -> Any:
        """
        Parse an environment variable value to the appropriate type.

        Args:
            env_value: Raw environment variable string
            value_type: Expected type

        Returns:
            Parsed value
        """
        if value_type == "integer":
            try:
                return int(env_value)
            except ValueError:
                return None
        elif value_type == "float":
            try:
                return float(env_value)
            except ValueError:
                return None
        elif value_type == "boolean":
            return env_value.lower() in ("true", "1", "yes", "on")
        elif value_type == "json":
            try:
                return json.loads(env_value)
            except json.JSONDecodeError:
                return None
        else:
            return env_value

    def _get_env_key(self, setting_key: str) -> str:
        """
        Convert a setting key to its environment variable name.

        Args:
            setting_key: Dot-notation key (e.g., "transcoding.hls_segment_duration")

        Returns:
            Environment variable name (e.g., "VLOG_HLS_SEGMENT_DURATION")
        """
        # Check for explicit mapping first (for non-standard env var names)
        if setting_key in SETTING_TO_ENV_MAP:
            return SETTING_TO_ENV_MAP[setting_key]

        # Remove category prefix for env var lookup
        # e.g., "transcoding.hls_segment_duration" -> "HLS_SEGMENT_DURATION"
        if "." in setting_key:
            _, name = setting_key.rsplit(".", 1)
        else:
            name = setting_key

        return f"VLOG_{name.upper()}"

    async def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value with caching and env var fallback.

        Lookup order:
        1. Check in-memory cache (if valid)
        2. Refresh cache from database if needed
        3. Fall back to environment variable
        4. Return default value

        Args:
            key: Setting key (e.g., "transcoding.hls_segment_duration")
            default: Default value if setting not found

        Returns:
            Setting value
        """
        # 1. Check cache (refresh if needed)
        await self._refresh_cache_if_needed()

        if key in self._cache:
            return self._cache[key]

        # 2. Fall back to environment variable
        env_key = self._get_env_key(key)
        env_value = os.getenv(env_key)
        if env_value is not None:
            # Determine type from metadata if available
            value_type = "string"
            if key in self._cache_metadata:
                value_type = self._cache_metadata[key].get("value_type", "string")

            parsed = self._parse_env_value(env_value, value_type)
            if parsed is not None:
                logger.debug(f"Setting '{key}' using env var fallback: {env_key}")
                return parsed

        # 3. Return default
        return default

    async def get_typed(
        self,
        key: str,
        default: Any = None,
        value_type: str = "string",
    ) -> Any:
        """
        Get a setting with explicit type specification for env var fallback.

        Use this when the setting might not exist in the database yet
        (during migration period).

        Args:
            key: Setting key
            default: Default value
            value_type: Expected type for parsing env var

        Returns:
            Setting value coerced to the specified type
        """
        await self._refresh_cache_if_needed()

        if key in self._cache:
            return self._cache[key]

        # Fall back to environment variable with specified type
        env_key = self._get_env_key(key)
        env_value = os.getenv(env_key)
        if env_value is not None:
            parsed = self._parse_env_value(env_value, value_type)
            if parsed is not None:
                return parsed

        return default

    async def set(
        self,
        key: str,
        value: Any,
        updated_by: Optional[str] = None,
    ) -> None:
        """
        Update a setting value in the database.

        Args:
            key: Setting key
            value: New value
            updated_by: Who made the change (for audit trail)

        Raises:
            SettingsValidationError: If validation fails
            KeyError: If setting doesn't exist
        """
        from api.database import settings as settings_table

        # Validate the value
        if key in self._cache_metadata:
            metadata = self._cache_metadata[key]
            self._validate_value(value, metadata["value_type"], metadata.get("constraints"))

        # Serialize value to JSON
        json_value = json.dumps(value)

        # Update in database
        query = (
            settings_table.update()
            .where(settings_table.c.key == key)
            .values(
                value=json_value,
                updated_at=datetime.now(timezone.utc),
                updated_by=updated_by,
            )
        )

        result = await db_execute_with_retry(query)

        if result == 0:
            raise KeyError(f"Setting not found: {key}")

        # Update cache immediately
        self._cache[key] = value
        logger.info(f"Setting updated: {key} (by {updated_by or 'unknown'})")

    async def create(
        self,
        key: str,
        value: Any,
        category: str,
        value_type: str = "string",
        description: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
        updated_by: Optional[str] = None,
    ) -> None:
        """
        Create a new setting in the database.

        Args:
            key: Setting key (must be unique)
            value: Initial value
            category: Category for UI grouping
            value_type: Value type (string, integer, float, boolean, enum, json)
            description: Help text for UI
            constraints: Validation constraints
            updated_by: Who created the setting
        """
        from api.database import settings as settings_table

        # Validate the value
        self._validate_value(value, value_type, constraints)

        # Serialize values
        json_value = json.dumps(value)
        json_constraints = json.dumps(constraints) if constraints else None

        query = settings_table.insert().values(
            key=key,
            value=json_value,
            category=category,
            value_type=value_type,
            description=description,
            constraints=json_constraints,
            updated_at=datetime.now(timezone.utc),
            updated_by=updated_by,
        )

        try:
            await db_execute_with_retry(query)
        except Exception as e:
            if is_unique_violation(e, column="key"):
                raise UniqueConstraintError(f"Setting already exists: {key}")
            raise

        # Update cache
        self._cache[key] = value
        self._cache_metadata[key] = {
            "value_type": value_type,
            "category": category,
            "description": description,
            "constraints": constraints,
        }

        logger.info(f"Setting created: {key} in category {category}")

    async def delete(self, key: str) -> None:
        """
        Delete a setting from the database.

        Args:
            key: Setting key to delete
        """
        from api.database import settings as settings_table

        query = settings_table.delete().where(settings_table.c.key == key)
        await db_execute_with_retry(query)

        # Remove from cache
        self._cache.pop(key, None)
        self._cache_metadata.pop(key, None)

        logger.info(f"Setting deleted: {key}")

    async def get_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Get all settings in a category for UI display.

        Args:
            category: Category name

        Returns:
            List of setting dicts with key, value, type, description, constraints
        """
        from api.database import settings as settings_table

        query = settings_table.select().where(settings_table.c.category == category)
        rows = await fetch_all_with_retry(query)

        result = []
        for row in rows:
            try:
                value = json.loads(row["value"])
            except json.JSONDecodeError:
                value = row["value"]

            try:
                constraints = json.loads(row["constraints"]) if row["constraints"] else None
            except json.JSONDecodeError:
                constraints = None

            result.append(
                {
                    "key": row["key"],
                    "value": value,
                    "value_type": row["value_type"],
                    "description": row["description"],
                    "constraints": constraints,
                    "updated_at": row["updated_at"],
                    "updated_by": row["updated_by"],
                }
            )

        return result

    async def count(self) -> int:
        """
        Get the count of settings in the database.

        Returns:
            Number of settings stored in the database
        """
        import sqlalchemy as sa

        from api.database import settings as settings_table

        query = sa.select(sa.func.count()).select_from(settings_table)
        result = await fetch_val_with_retry(query)
        return result or 0

    async def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all settings grouped by category.

        Returns:
            Dict mapping category name to list of settings
        """
        from api.database import settings as settings_table

        query = settings_table.select().order_by(settings_table.c.category, settings_table.c.key)
        rows = await fetch_all_with_retry(query)

        result: Dict[str, List[Dict[str, Any]]] = {}

        for row in rows:
            category = row["category"]
            if category not in result:
                result[category] = []

            try:
                value = json.loads(row["value"])
            except json.JSONDecodeError:
                value = row["value"]

            try:
                constraints = json.loads(row["constraints"]) if row["constraints"] else None
            except json.JSONDecodeError:
                constraints = None

            result[category].append(
                {
                    "key": row["key"],
                    "value": value,
                    "value_type": row["value_type"],
                    "description": row["description"],
                    "constraints": constraints,
                    "updated_at": row["updated_at"],
                    "updated_by": row["updated_by"],
                }
            )

        return result

    async def get_single(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get a single setting with full metadata.

        Args:
            key: Setting key

        Returns:
            Setting dict or None if not found
        """
        from api.database import settings as settings_table

        query = settings_table.select().where(settings_table.c.key == key)
        row = await fetch_one_with_retry(query)

        if row is None:
            return None

        try:
            value = json.loads(row["value"])
        except json.JSONDecodeError:
            value = row["value"]

        try:
            constraints = json.loads(row["constraints"]) if row["constraints"] else None
        except json.JSONDecodeError:
            constraints = None

        return {
            "key": row["key"],
            "value": value,
            "category": row["category"],
            "value_type": row["value_type"],
            "description": row["description"],
            "constraints": constraints,
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    async def get_categories(self) -> List[str]:
        """
        Get list of all setting categories.

        Returns:
            List of unique category names
        """
        import sqlalchemy as sa

        query = sa.text("SELECT DISTINCT category FROM settings ORDER BY category")
        rows = await fetch_all_with_retry(query)

        return [row["category"] for row in rows]

    def _validate_value(
        self,
        value: Any,
        value_type: str,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Validate a value against type and constraints.

        Args:
            value: Value to validate
            value_type: Expected type
            constraints: Optional validation constraints

        Raises:
            SettingsValidationError: If validation fails
        """
        # Skip validation for None values
        if value is None:
            return

        # Type validation
        if value_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise SettingsValidationError(f"Expected integer, got {type(value).__name__}")
        elif value_type == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SettingsValidationError(f"Expected float, got {type(value).__name__}")
        elif value_type == "boolean":
            if not isinstance(value, bool):
                raise SettingsValidationError(f"Expected boolean, got {type(value).__name__}")
        elif value_type == "string":
            if not isinstance(value, str):
                raise SettingsValidationError(f"Expected string, got {type(value).__name__}")
        elif value_type == "enum":
            if not isinstance(value, str):
                raise SettingsValidationError(f"Expected string for enum, got {type(value).__name__}")

        # Constraint validation
        if constraints:
            # min/max only apply to numeric types
            if "min" in constraints and constraints["min"] is not None and value_type in ("integer", "float"):
                if value < constraints["min"]:
                    raise SettingsValidationError(f"Value {value} is below minimum {constraints['min']}")
            if "max" in constraints and constraints["max"] is not None and value_type in ("integer", "float"):
                if value > constraints["max"]:
                    raise SettingsValidationError(f"Value {value} is above maximum {constraints['max']}")
            if "enum_values" in constraints and constraints["enum_values"] is not None:
                if value not in constraints["enum_values"]:
                    raise SettingsValidationError(f"Value '{value}' not in allowed values: {constraints['enum_values']}")
            if "pattern" in constraints and constraints["pattern"] is not None:
                import re

                if not re.match(constraints["pattern"], str(value)):
                    raise SettingsValidationError(f"Value '{value}' does not match pattern: {constraints['pattern']}")
            if "min_length" in constraints and constraints["min_length"] is not None:
                if len(str(value)) < constraints["min_length"]:
                    raise SettingsValidationError(
                        f"Value length {len(str(value))} is below minimum {constraints['min_length']}"
                    )
            if "max_length" in constraints and constraints["max_length"] is not None:
                if len(str(value)) > constraints["max_length"]:
                    raise SettingsValidationError(
                        f"Value length {len(str(value))} is above maximum {constraints['max_length']}"
                    )

    def invalidate_cache(self) -> None:
        """Force cache refresh on next access."""
        self._cache_updated = 0
        self._cache_loaded = False
        logger.debug("Settings cache invalidated")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats
        """
        return {
            "loaded": self._cache_loaded,
            "entry_count": len(self._cache),
            "ttl_seconds": self._cache_ttl,
            "age_seconds": time.time() - self._cache_updated if self._cache_loaded else None,
            "is_valid": self._is_cache_valid(),
        }


# Global singleton instance
_settings_service: Optional[SettingsService] = None


def get_settings_service() -> SettingsService:
    """
    Get the global settings service instance.

    Returns:
        SettingsService singleton
    """
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService()
    return _settings_service


# Convenience functions for common patterns
async def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value (convenience wrapper)."""
    return await get_settings_service().get(key, default)


async def set_setting(key: str, value: Any, updated_by: Optional[str] = None) -> None:
    """Set a setting value (convenience wrapper)."""
    await get_settings_service().set(key, value, updated_by)


# Known settings that can be seeded from environment variables
# Format: (key, category, value_type, description, constraints, env_var_override)
# env_var_override is optional - if None, the env var is derived from the key
KNOWN_SETTINGS = [
    # Transcoding settings
    (
        "transcoding.hls_segment_duration",
        "transcoding",
        "integer",
        "HLS segment duration in seconds",
        {"min": 2, "max": 30},
    ),
    (
        "transcoding.checkpoint_interval",
        "transcoding",
        "integer",
        "Checkpoint interval in seconds",
        {"min": 1, "max": 300},
    ),
    (
        "transcoding.max_retries",
        "transcoding",
        "integer",
        "Maximum retry attempts for failed jobs",
        {"min": 0, "max": 10},
    ),
    (
        "transcoding.retry_backoff_base",
        "transcoding",
        "integer",
        "Base retry backoff in seconds",
        {"min": 0, "max": 600},
    ),
    (
        "transcoding.job_stale_timeout",
        "transcoding",
        "integer",
        "Job stale timeout in seconds",
        {"min": 60, "max": 7200},
    ),
    ("transcoding.cleanup_partial_on_failure", "transcoding", "boolean", "Clean up partial files on failure", None),
    ("transcoding.keep_completed_qualities", "transcoding", "boolean", "Keep completed qualities on resume", None),
    (
        "transcoding.ffmpeg_timeout_multiplier",
        "transcoding",
        "float",
        "FFmpeg timeout base multiplier",
        {"min": 0.1, "max": 10.0},
    ),
    (
        "transcoding.ffmpeg_timeout_minimum",
        "transcoding",
        "integer",
        "Minimum FFmpeg timeout in seconds",
        {"min": 60, "max": 3600},
    ),
    (
        "transcoding.ffmpeg_timeout_maximum",
        "transcoding",
        "integer",
        "Maximum FFmpeg timeout in seconds",
        {"min": 300, "max": 86400},
    ),
    # Watermark settings (client-side overlay)
    ("watermark.enabled", "watermark", "boolean", "Enable watermark overlay on video player", None),
    ("watermark.type", "watermark", "enum", "Watermark type", {"enum_values": ["image", "text"]}),
    ("watermark.image", "watermark", "string", "Path to watermark image (relative to storage)", None),
    ("watermark.text", "watermark", "string", "Text to display as watermark", None),
    ("watermark.text_size", "watermark", "integer", "Text watermark font size in pixels", {"min": 8, "max": 72}),
    ("watermark.text_color", "watermark", "string", "Text watermark color (CSS color)", None),
    (
        "watermark.position",
        "watermark",
        "enum",
        "Watermark position",
        {"enum_values": ["top-left", "top-right", "bottom-left", "bottom-right", "center"]},
    ),
    ("watermark.opacity", "watermark", "float", "Watermark opacity (0.0-1.0)", {"min": 0.0, "max": 1.0}),
    ("watermark.padding", "watermark", "integer", "Padding from edge in pixels", {"min": 0, "max": 200}),
    (
        "watermark.max_width_percent",
        "watermark",
        "integer",
        "Max width as percentage of video player",
        {"min": 1, "max": 50},
    ),
    # Worker settings
    (
        "workers.heartbeat_interval",
        "workers",
        "integer",
        "Worker heartbeat interval in seconds",
        {"min": 5, "max": 300},
    ),
    ("workers.claim_duration_minutes", "workers", "integer", "Job claim duration in minutes", {"min": 1, "max": 120}),
    ("workers.poll_interval", "workers", "integer", "Job polling interval in seconds", {"min": 1, "max": 120}),
    (
        "workers.offline_threshold_minutes",
        "workers",
        "integer",
        "Minutes before worker is considered offline",
        {"min": 1, "max": 60},
    ),
    (
        "workers.stale_job_check_interval",
        "workers",
        "integer",
        "Stale job check interval in seconds",
        {"min": 10, "max": 600},
    ),
    (
        "workers.progress_update_interval",
        "workers",
        "float",
        "Progress update interval in seconds",
        {"min": 0.1, "max": 60.0},
    ),
    (
        "workers.fallback_poll_interval",
        "workers",
        "integer",
        "Fallback polling interval when filesystem watcher unavailable (seconds)",
        {"min": 1, "max": 600},
    ),
    (
        "workers.debounce_delay",
        "workers",
        "float",
        "Debounce delay for filesystem events (seconds)",
        {"min": 0.0, "max": 60.0},
    ),
    # Analytics settings
    ("analytics.cache_enabled", "analytics", "boolean", "Enable analytics caching", None),
    ("analytics.cache_ttl", "analytics", "integer", "Analytics cache TTL in seconds", {"min": 1, "max": 3600}),
    (
        "analytics.client_cache_max_age",
        "analytics",
        "integer",
        "Client-side cache max-age in seconds",
        {"min": 0, "max": 3600},
    ),
    # Alert settings
    ("alerts.webhook_url", "alerts", "string", "Webhook URL for alerts", None),
    ("alerts.webhook_timeout", "alerts", "integer", "Webhook request timeout in seconds", {"min": 1, "max": 60}),
    (
        "alerts.rate_limit_seconds",
        "alerts",
        "integer",
        "Minimum seconds between alerts of same type",
        {"min": 0, "max": 3600},
    ),
    # Transcription settings
    ("transcription.enabled", "transcription", "boolean", "Enable automatic transcription", None),
    (
        "transcription.whisper_model",
        "transcription",
        "enum",
        "Whisper model size",
        {"enum_values": ["tiny", "base", "small", "medium", "large"]},
    ),
    ("transcription.language", "transcription", "string", "Transcription language (or null for auto)", None),
    ("transcription.on_upload", "transcription", "boolean", "Transcribe automatically on upload", None),
    (
        "transcription.compute_type",
        "transcription",
        "enum",
        "Compute type for transcription",
        {"enum_values": ["int8", "float16", "float32"]},
    ),
    (
        "transcription.timeout",
        "transcription",
        "integer",
        "Transcription timeout in seconds",
        {"min": 60, "max": 14400},
    ),
    # Storage settings
    ("storage.archive_retention_days", "storage", "integer", "Days to retain archived videos", {"min": 0, "max": 365}),
    ("storage.max_upload_size_mb", "storage", "integer", "Maximum upload size in MB", {"min": 1, "max": 102400}),
    (
        "storage.max_thumbnail_size_mb",
        "storage",
        "integer",
        "Maximum thumbnail upload size in MB",
        {"min": 1, "max": 100},
    ),
    ("storage.thumbnail_width", "storage", "integer", "Thumbnail width in pixels", {"min": 100, "max": 1920}),
    # Streaming format settings (Issue #212)
    (
        "streaming.default_format",
        "streaming",
        "enum",
        "Default streaming format for new encodes (hls_ts=legacy, cmaf=modern fMP4)",
        {"enum_values": ["hls_ts", "cmaf"]},
    ),
    (
        "streaming.default_codec",
        "streaming",
        "enum",
        "Default video codec for new encodes",
        {"enum_values": ["h264", "hevc", "av1"]},
    ),
    (
        "streaming.enable_dash",
        "streaming",
        "boolean",
        "Generate DASH manifest alongside HLS (requires CMAF format)",
        None,
    ),
    (
        "streaming.segment_duration",
        "streaming",
        "integer",
        "Segment duration in seconds (applies to both HLS and DASH)",
        {"min": 2, "max": 10},
    ),
    (
        "streaming.h264_fallback",
        "streaming",
        "boolean",
        "Also generate H.264 version for older device compatibility",
        None,
    ),
    # CDN settings (Issue #222)
    (
        "cdn.enabled",
        "cdn",
        "boolean",
        "Enable CDN for video streaming content",
        None,
    ),
    (
        "cdn.base_url",
        "cdn",
        "string",
        "CDN base URL (e.g., https://cdn.example.com) - video paths appended to this",
        {"pattern": r"^https?://[a-zA-Z0-9][\w\-\.]*\w(:[0-9]+)?/?$"},
    ),
    # Re-encoding settings (Issue #212)
    (
        "reencode.batch_size",
        "reencode",
        "integer",
        "Number of videos to process concurrently in re-encode queue",
        {"min": 1, "max": 10},
    ),
    (
        "reencode.enabled",
        "reencode",
        "boolean",
        "Enable background re-encoding worker",
        None,
    ),
    (
        "reencode.priority_threshold_views",
        "reencode",
        "integer",
        "Videos with more views than this get high priority re-encoding",
        {"min": 0, "max": 100000},
    ),
    # Streaming segment upload settings (Issue #478)
    (
        "workers.streaming_upload",
        "workers",
        "boolean",
        "Upload segments individually during transcoding (eliminates tar.gz blocking)",
        None,
    ),
    # Display settings
    (
        "display.show_view_counts",
        "display",
        "boolean",
        "Show view counts on video cards in the public UI",
        None,
    ),
    (
        "display.show_tagline",
        "display",
        "boolean",
        "Show tagline in the footer",
        None,
    ),
    (
        "display.tagline",
        "display",
        "string",
        "Footer tagline text",
        {"max_length": 100},
    ),
    # Metrics settings (Issue #436)
    (
        "metrics.enabled",
        "metrics",
        "boolean",
        "Enable Prometheus metrics endpoint (/metrics)",
        None,
    ),
    (
        "metrics.auth_required",
        "metrics",
        "boolean",
        "Require authentication for metrics endpoint (recommended for public deployments)",
        None,
    ),
]

# Mapping from setting key to environment variable name (for non-standard mappings)
SETTING_TO_ENV_MAP = {
    "transcoding.max_retries": "VLOG_MAX_RETRY_ATTEMPTS",
    "transcoding.ffmpeg_timeout_multiplier": "VLOG_FFMPEG_TIMEOUT_MULTIPLIER",
    # Watermark settings - need explicit mapping since category is part of env var name
    "watermark.enabled": "VLOG_WATERMARK_ENABLED",
    "watermark.type": "VLOG_WATERMARK_TYPE",
    "watermark.image": "VLOG_WATERMARK_IMAGE",
    "watermark.text": "VLOG_WATERMARK_TEXT",
    "watermark.text_size": "VLOG_WATERMARK_TEXT_SIZE",
    "watermark.text_color": "VLOG_WATERMARK_TEXT_COLOR",
    "watermark.position": "VLOG_WATERMARK_POSITION",
    "watermark.opacity": "VLOG_WATERMARK_OPACITY",
    "watermark.padding": "VLOG_WATERMARK_PADDING",
    "watermark.max_width_percent": "VLOG_WATERMARK_MAX_WIDTH_PERCENT",
    # Worker settings
    "workers.claim_duration_minutes": "VLOG_WORKER_CLAIM_DURATION",
    "workers.offline_threshold_minutes": "VLOG_WORKER_OFFLINE_THRESHOLD",
    "workers.fallback_poll_interval": "VLOG_WORKER_FALLBACK_POLL_INTERVAL",
    "workers.debounce_delay": "VLOG_WORKER_DEBOUNCE_DELAY",
    # Analytics settings
    "analytics.cache_enabled": "VLOG_ANALYTICS_CACHE_ENABLED",
    "analytics.cache_ttl": "VLOG_ANALYTICS_CACHE_TTL",
    "analytics.client_cache_max_age": "VLOG_ANALYTICS_CLIENT_CACHE_MAX_AGE",
    # Alert settings
    "alerts.webhook_url": "VLOG_ALERT_WEBHOOK_URL",
    "alerts.webhook_timeout": "VLOG_ALERT_WEBHOOK_TIMEOUT",
    "alerts.rate_limit_seconds": "VLOG_ALERT_RATE_LIMIT_SECONDS",
    # Transcription settings
    "transcription.enabled": "VLOG_TRANSCRIPTION_ENABLED",
    "transcription.whisper_model": "VLOG_WHISPER_MODEL",
    "transcription.language": "VLOG_TRANSCRIPTION_LANGUAGE",
    "transcription.on_upload": "VLOG_TRANSCRIPTION_ON_UPLOAD",
    "transcription.compute_type": "VLOG_TRANSCRIPTION_COMPUTE_TYPE",
    "transcription.timeout": "VLOG_TRANSCRIPTION_TIMEOUT",
    # Storage settings
    "storage.archive_retention_days": "VLOG_ARCHIVE_RETENTION_DAYS",
    "storage.max_upload_size_mb": "VLOG_MAX_UPLOAD_SIZE",
    "storage.max_thumbnail_size_mb": "VLOG_MAX_THUMBNAIL_SIZE",
    "storage.thumbnail_width": "VLOG_THUMBNAIL_WIDTH",
    # Streaming settings (Issue #212)
    "streaming.default_format": "VLOG_STREAMING_FORMAT",
    "streaming.default_codec": "VLOG_STREAMING_CODEC",
    "streaming.enable_dash": "VLOG_STREAMING_ENABLE_DASH",
    "streaming.segment_duration": "VLOG_STREAMING_SEGMENT_DURATION",
    "streaming.h264_fallback": "VLOG_STREAMING_H264_FALLBACK",
    # Re-encoding settings (Issue #212)
    "reencode.batch_size": "VLOG_REENCODE_BATCH_SIZE",
    "reencode.enabled": "VLOG_REENCODE_ENABLED",
    "reencode.priority_threshold_views": "VLOG_REENCODE_PRIORITY_THRESHOLD",
    # Streaming segment upload (Issue #478)
    "workers.streaming_upload": "VLOG_WORKER_STREAMING_UPLOAD",
    # Display settings
    "display.show_view_counts": "VLOG_DISPLAY_SHOW_VIEW_COUNTS",
    "display.show_tagline": "VLOG_DISPLAY_SHOW_TAGLINE",
    "display.tagline": "VLOG_DISPLAY_TAGLINE",
    # Metrics settings (Issue #436)
    "metrics.enabled": "VLOG_METRICS_ENABLED",
    "metrics.auth_required": "VLOG_METRICS_AUTH_REQUIRED",
}


async def seed_settings_from_env(updated_by: str = "migration") -> Dict[str, Any]:
    """
    Seed database settings from environment variables.

    This function checks for known settings in environment variables and
    creates them in the database if they don't already exist. Useful for
    initial migration from env var based config to database-backed settings.

    Args:
        updated_by: Attribution for the seeded settings

    Returns:
        Dict with 'seeded' (count), 'skipped' (count), and 'details' (list)
    """
    service = get_settings_service()
    seeded = 0
    skipped = 0
    details = []

    for key, category, value_type, description, constraints in KNOWN_SETTINGS:
        # Check if already exists in DB
        existing = await service.get_single(key)
        if existing:
            skipped += 1
            details.append({"key": key, "status": "skipped", "reason": "already exists"})
            continue

        # Check for env var value
        env_key = service._get_env_key(key)
        env_value = os.getenv(env_key)

        if env_value is not None:
            # Parse env value to correct type
            parsed = service._parse_env_value(env_value, value_type)
            if parsed is not None:
                try:
                    await service.create(
                        key=key,
                        value=parsed,
                        category=category,
                        value_type=value_type,
                        description=description,
                        constraints=constraints,
                        updated_by=updated_by,
                    )
                    seeded += 1
                    details.append({"key": key, "status": "seeded", "from_env": env_key, "value": parsed})
                    logger.info(f"Seeded setting {key} from {env_key}")
                except Exception as e:
                    details.append({"key": key, "status": "error", "error": str(e)})
            else:
                details.append({"key": key, "status": "skipped", "reason": f"failed to parse {env_key}"})
                skipped += 1
        else:
            details.append({"key": key, "status": "skipped", "reason": f"no env var {env_key}"})
            skipped += 1

    return {"seeded": seeded, "skipped": skipped, "details": details}
