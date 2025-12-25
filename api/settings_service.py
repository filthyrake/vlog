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

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.db_retry import (
    db_execute_with_retry,
    fetch_all_with_retry,
    fetch_one_with_retry,
)

logger = logging.getLogger(__name__)


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
        """Refresh cache if expired or not loaded."""
        if not self._is_cache_valid():
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

        await db_execute_with_retry(query)

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
            if "min" in constraints and value < constraints["min"]:
                raise SettingsValidationError(f"Value {value} is below minimum {constraints['min']}")
            if "max" in constraints and value > constraints["max"]:
                raise SettingsValidationError(f"Value {value} is above maximum {constraints['max']}")
            if "enum_values" in constraints and value not in constraints["enum_values"]:
                raise SettingsValidationError(f"Value '{value}' not in allowed values: {constraints['enum_values']}")
            if "pattern" in constraints:
                import re

                if not re.match(constraints["pattern"], str(value)):
                    raise SettingsValidationError(f"Value '{value}' does not match pattern: {constraints['pattern']}")
            if "min_length" in constraints and len(str(value)) < constraints["min_length"]:
                raise SettingsValidationError(
                    f"Value length {len(str(value))} is below minimum {constraints['min_length']}"
                )
            if "max_length" in constraints and len(str(value)) > constraints["max_length"]:
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
