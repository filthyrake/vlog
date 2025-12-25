"""
Tests for the database-backed settings service.
See: https://github.com/filthyrake/vlog/issues/400
"""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from api.settings_service import (
    SettingsService,
    SettingsValidationError,
    get_settings_service,
)


class TestSettingsServiceValidation:
    """Test suite for settings validation logic."""

    def test_validate_string_type(self):
        """Test string type validation."""
        service = SettingsService()

        # Valid string
        service._validate_value("hello", "string")

        # Invalid - not a string
        with pytest.raises(SettingsValidationError, match="Expected string"):
            service._validate_value(123, "string")

    def test_validate_integer_type(self):
        """Test integer type validation."""
        service = SettingsService()

        # Valid integer
        service._validate_value(42, "integer")

        # Invalid - float
        with pytest.raises(SettingsValidationError, match="Expected integer"):
            service._validate_value(3.14, "integer")

        # Invalid - string
        with pytest.raises(SettingsValidationError, match="Expected integer"):
            service._validate_value("42", "integer")

        # Invalid - boolean (Python bools are ints, but we reject them)
        with pytest.raises(SettingsValidationError, match="Expected integer"):
            service._validate_value(True, "integer")

    def test_validate_float_type(self):
        """Test float type validation."""
        service = SettingsService()

        # Valid float
        service._validate_value(3.14, "float")

        # Valid int (acceptable as float)
        service._validate_value(42, "float")

        # Invalid - string
        with pytest.raises(SettingsValidationError, match="Expected float"):
            service._validate_value("3.14", "float")

        # Invalid - boolean
        with pytest.raises(SettingsValidationError, match="Expected float"):
            service._validate_value(True, "float")

    def test_validate_boolean_type(self):
        """Test boolean type validation."""
        service = SettingsService()

        # Valid booleans
        service._validate_value(True, "boolean")
        service._validate_value(False, "boolean")

        # Invalid - integer
        with pytest.raises(SettingsValidationError, match="Expected boolean"):
            service._validate_value(1, "boolean")

        # Invalid - string
        with pytest.raises(SettingsValidationError, match="Expected boolean"):
            service._validate_value("true", "boolean")

    def test_validate_enum_type(self):
        """Test enum type validation (must be string)."""
        service = SettingsService()

        # Valid enum value (string)
        service._validate_value("option1", "enum")

        # Invalid - not a string
        with pytest.raises(SettingsValidationError, match="Expected string for enum"):
            service._validate_value(1, "enum")

    def test_validate_min_constraint(self):
        """Test minimum value constraint."""
        service = SettingsService()

        # Valid - at minimum
        service._validate_value(10, "integer", {"min": 10})

        # Valid - above minimum
        service._validate_value(20, "integer", {"min": 10})

        # Invalid - below minimum
        with pytest.raises(SettingsValidationError, match="below minimum"):
            service._validate_value(5, "integer", {"min": 10})

    def test_validate_max_constraint(self):
        """Test maximum value constraint."""
        service = SettingsService()

        # Valid - at maximum
        service._validate_value(100, "integer", {"max": 100})

        # Valid - below maximum
        service._validate_value(50, "integer", {"max": 100})

        # Invalid - above maximum
        with pytest.raises(SettingsValidationError, match="above maximum"):
            service._validate_value(150, "integer", {"max": 100})

    def test_validate_enum_values_constraint(self):
        """Test enum_values constraint."""
        service = SettingsService()
        constraints = {"enum_values": ["a", "b", "c"]}

        # Valid - in allowed values
        service._validate_value("a", "enum", constraints)
        service._validate_value("b", "enum", constraints)

        # Invalid - not in allowed values
        with pytest.raises(SettingsValidationError, match="not in allowed values"):
            service._validate_value("d", "enum", constraints)

    def test_validate_pattern_constraint(self):
        """Test regex pattern constraint."""
        service = SettingsService()
        constraints = {"pattern": r"^\d{3}-\d{4}$"}

        # Valid - matches pattern
        service._validate_value("123-4567", "string", constraints)

        # Invalid - doesn't match pattern
        with pytest.raises(SettingsValidationError, match="does not match pattern"):
            service._validate_value("invalid", "string", constraints)

    def test_validate_min_length_constraint(self):
        """Test minimum length constraint."""
        service = SettingsService()
        constraints = {"min_length": 5}

        # Valid - at minimum length
        service._validate_value("hello", "string", constraints)

        # Valid - above minimum length
        service._validate_value("hello world", "string", constraints)

        # Invalid - below minimum length
        with pytest.raises(SettingsValidationError, match="below minimum"):
            service._validate_value("hi", "string", constraints)

    def test_validate_max_length_constraint(self):
        """Test maximum length constraint."""
        service = SettingsService()
        constraints = {"max_length": 10}

        # Valid - at maximum length
        service._validate_value("0123456789", "string", constraints)

        # Valid - below maximum length
        service._validate_value("hello", "string", constraints)

        # Invalid - above maximum length
        with pytest.raises(SettingsValidationError, match="above maximum"):
            service._validate_value("this is too long", "string", constraints)

    def test_validate_none_value_skips_validation(self):
        """Test that None values skip type validation."""
        service = SettingsService()

        # None should pass any type validation
        service._validate_value(None, "string")
        service._validate_value(None, "integer")
        service._validate_value(None, "boolean")


class TestSettingsServiceCoercion:
    """Test suite for value coercion logic."""

    def test_coerce_string(self):
        """Test string coercion."""
        service = SettingsService()

        assert service._coerce_value("hello", "string") == "hello"
        assert service._coerce_value(123, "string") == "123"
        assert service._coerce_value(True, "string") == "True"

    def test_coerce_integer(self):
        """Test integer coercion."""
        service = SettingsService()

        assert service._coerce_value(42, "integer") == 42
        assert service._coerce_value("42", "integer") == 42
        assert service._coerce_value(3.7, "integer") == 3

    def test_coerce_float(self):
        """Test float coercion."""
        service = SettingsService()

        assert service._coerce_value(3.14, "float") == 3.14
        assert service._coerce_value("3.14", "float") == 3.14
        assert service._coerce_value(42, "float") == 42.0

    def test_coerce_boolean(self):
        """Test boolean coercion."""
        service = SettingsService()

        # Already boolean
        assert service._coerce_value(True, "boolean") is True
        assert service._coerce_value(False, "boolean") is False

        # String truthy values
        assert service._coerce_value("true", "boolean") is True
        assert service._coerce_value("TRUE", "boolean") is True
        assert service._coerce_value("1", "boolean") is True
        assert service._coerce_value("yes", "boolean") is True
        assert service._coerce_value("on", "boolean") is True

        # String falsy values
        assert service._coerce_value("false", "boolean") is False
        assert service._coerce_value("0", "boolean") is False
        assert service._coerce_value("no", "boolean") is False

    def test_coerce_json(self):
        """Test JSON type (no coercion, return as-is)."""
        service = SettingsService()

        data = {"key": "value", "list": [1, 2, 3]}
        assert service._coerce_value(data, "json") == data

    def test_coerce_none(self):
        """Test None value coercion."""
        service = SettingsService()

        assert service._coerce_value(None, "string") is None
        assert service._coerce_value(None, "integer") is None


class TestSettingsServiceEnvFallback:
    """Test suite for environment variable fallback logic."""

    def test_get_env_key_simple(self):
        """Test simple env key conversion."""
        service = SettingsService()

        assert service._get_env_key("hls_segment_duration") == "VLOG_HLS_SEGMENT_DURATION"
        assert service._get_env_key("max_retries") == "VLOG_MAX_RETRIES"

    def test_get_env_key_with_category(self):
        """Test env key conversion strips category prefix."""
        service = SettingsService()

        assert service._get_env_key("transcoding.hls_segment_duration") == "VLOG_HLS_SEGMENT_DURATION"
        assert service._get_env_key("workers.heartbeat_interval") == "VLOG_HEARTBEAT_INTERVAL"

    def test_parse_env_value_string(self):
        """Test parsing string from env var."""
        service = SettingsService()

        assert service._parse_env_value("hello", "string") == "hello"

    def test_parse_env_value_integer(self):
        """Test parsing integer from env var."""
        service = SettingsService()

        assert service._parse_env_value("42", "integer") == 42
        assert service._parse_env_value("invalid", "integer") is None

    def test_parse_env_value_float(self):
        """Test parsing float from env var."""
        service = SettingsService()

        assert service._parse_env_value("3.14", "float") == 3.14
        assert service._parse_env_value("invalid", "float") is None

    def test_parse_env_value_boolean(self):
        """Test parsing boolean from env var."""
        service = SettingsService()

        assert service._parse_env_value("true", "boolean") is True
        assert service._parse_env_value("false", "boolean") is False
        assert service._parse_env_value("1", "boolean") is True
        assert service._parse_env_value("0", "boolean") is False

    def test_parse_env_value_json(self):
        """Test parsing JSON from env var."""
        service = SettingsService()

        result = service._parse_env_value('{"key": "value"}', "json")
        assert result == {"key": "value"}

        assert service._parse_env_value("invalid json", "json") is None


class TestSettingsServiceCache:
    """Test suite for caching behavior."""

    def test_cache_initially_invalid(self):
        """Test cache is invalid when service is first created."""
        service = SettingsService()

        assert service._is_cache_valid() is False
        assert service._cache_loaded is False

    def test_invalidate_cache(self):
        """Test invalidate_cache method."""
        service = SettingsService()

        # Manually set cache state
        service._cache_loaded = True
        service._cache_updated = time.time()
        service._cache = {"test": "value"}

        # Invalidate
        service.invalidate_cache()

        assert service._is_cache_valid() is False
        assert service._cache_loaded is False

    def test_cache_stats(self):
        """Test get_cache_stats method."""
        service = SettingsService(cache_ttl=120)

        stats = service.get_cache_stats()
        assert stats["loaded"] is False
        assert stats["entry_count"] == 0
        assert stats["ttl_seconds"] == 120
        assert stats["is_valid"] is False

    def test_cache_ttl_expiration(self):
        """Test cache expires after TTL."""
        service = SettingsService(cache_ttl=1)

        # Manually set cache as valid
        service._cache_loaded = True
        service._cache_updated = time.time()

        assert service._is_cache_valid() is True

        # Wait for TTL to expire
        time.sleep(1.1)

        assert service._is_cache_valid() is False


class TestSettingsServiceIntegration:
    """Integration tests requiring database fixtures."""

    @pytest.mark.asyncio
    async def test_get_returns_default_when_setting_not_found(self, test_database):
        """Test get returns default when setting doesn't exist."""
        service = SettingsService()
        service._cache_loaded = True  # Prevent cache refresh
        service._cache_updated = time.time()

        result = await service.get("nonexistent.key", default="default_value")
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_get_typed_with_env_fallback(self, monkeypatch):
        """Test get_typed falls back to environment variable."""
        service = SettingsService()
        service._cache_loaded = True
        service._cache_updated = time.time()
        service._cache = {}

        # Set environment variable
        monkeypatch.setenv("VLOG_TEST_VALUE", "42")

        result = await service.get_typed("test.test_value", default=0, value_type="integer")
        assert result == 42

    @pytest.mark.asyncio
    async def test_create_and_get_setting(self, test_database):
        """Test creating and retrieving a setting."""
        # Patch database import to use test database
        with (
            patch("api.settings_service.fetch_all_with_retry"),
            patch("api.settings_service.fetch_one_with_retry"),
            patch("api.settings_service.db_execute_with_retry") as mock_execute,
        ):
            service = SettingsService()

            # Mock execute for create
            mock_execute.return_value = 1

            # Create a setting
            await service.create(
                key="test.setting",
                value=42,
                category="test",
                value_type="integer",
                description="A test setting",
            )

            # Verify the setting was added to cache
            assert service._cache.get("test.setting") == 42

    @pytest.mark.asyncio
    async def test_set_validates_value(self, test_database):
        """Test that set validates value against constraints."""
        service = SettingsService()

        # Set up cache metadata with constraints
        service._cache_metadata = {
            "test.constrained": {
                "value_type": "integer",
                "category": "test",
                "constraints": {"min": 0, "max": 100},
            }
        }
        service._cache = {"test.constrained": 50}
        service._cache_loaded = True
        service._cache_updated = time.time()

        # Try to set invalid value
        with pytest.raises(SettingsValidationError, match="above maximum"):
            await service.set("test.constrained", 150)

    @pytest.mark.asyncio
    async def test_delete_removes_from_cache(self, test_database):
        """Test that delete removes setting from cache."""
        with patch("api.settings_service.db_execute_with_retry") as mock_execute:
            service = SettingsService()

            # Set up cache
            service._cache = {"test.setting": 42}
            service._cache_metadata = {"test.setting": {"value_type": "integer"}}

            mock_execute.return_value = 1

            await service.delete("test.setting")

            assert "test.setting" not in service._cache
            assert "test.setting" not in service._cache_metadata


class TestSettingsServiceSingleton:
    """Test suite for singleton behavior."""

    def test_get_settings_service_returns_same_instance(self):
        """Test get_settings_service returns the same instance."""
        # Reset the global singleton
        import api.settings_service

        api.settings_service._settings_service = None

        service1 = get_settings_service()
        service2 = get_settings_service()

        assert service1 is service2


class TestSettingsSchemas:
    """Test Pydantic schema validation for settings."""

    def test_setting_create_key_pattern(self):
        """Test SettingCreate key must match dot notation pattern."""
        from api.schemas import SettingCreate

        # Valid keys
        valid = SettingCreate(key="transcoding.quality", value="1080p", category="transcoding")
        assert valid.key == "transcoding.quality"

        valid_single = SettingCreate(key="simple", value="value", category="test")
        assert valid_single.key == "simple"

        # Invalid keys (from Pydantic validation)
        with pytest.raises(Exception):  # Pydantic ValidationError
            SettingCreate(key="Invalid-Key", value="value", category="test")

        with pytest.raises(Exception):
            SettingCreate(key="123starts_with_number", value="value", category="test")

    def test_setting_create_value_type(self):
        """Test SettingCreate value_type must be valid."""
        from api.schemas import SettingCreate

        # Valid types
        for vtype in ["string", "integer", "float", "boolean", "enum", "json"]:
            setting = SettingCreate(key="test", value="x", category="test", value_type=vtype)
            assert setting.value_type == vtype

        # Invalid type
        with pytest.raises(Exception):
            SettingCreate(key="test", value="x", category="test", value_type="invalid")

    def test_setting_response_model(self):
        """Test SettingResponse model."""
        from api.schemas import SettingResponse

        response = SettingResponse(
            key="test.setting",
            value=42,
            category="test",
            value_type="integer",
            description="A test setting",
            constraints=None,
            updated_at=datetime.now(timezone.utc),
            updated_by="admin",
        )

        assert response.key == "test.setting"
        assert response.value == 42
        assert response.value_type == "integer"

    def test_settings_export_model(self):
        """Test SettingsExport model."""
        from api.schemas import SettingResponse, SettingsExport

        now = datetime.now(timezone.utc)
        export = SettingsExport(
            version="1.0",
            exported_at=now,
            settings=[
                SettingResponse(
                    key="test.setting",
                    value=42,
                    category="test",
                    value_type="integer",
                    updated_at=now,
                )
            ],
        )

        assert export.version == "1.0"
        assert len(export.settings) == 1
