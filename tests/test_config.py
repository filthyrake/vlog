"""Tests for config.py environment variable parsing helpers."""

import logging
import os
from unittest import mock


class TestGetIntEnv:
    """Tests for get_int_env helper function."""

    def test_returns_default_when_env_not_set(self):
        """Should return default value when environment variable is not set."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {}, clear=True):
            result = get_int_env("NONEXISTENT_VAR", 42)
            assert result == 42

    def test_parses_valid_integer(self):
        """Should parse valid integer from environment variable."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "123"}):
            result = get_int_env("TEST_INT", 0)
            assert result == 123

    def test_returns_default_on_invalid_value(self, caplog):
        """Should return default and log warning when value is not a valid integer."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "abc"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INT", 42)
                assert result == 42
                assert "Invalid TEST_INT='abc'" in caplog.text
                assert "using default 42" in caplog.text

    def test_returns_default_on_float_value(self, caplog):
        """Should return default when value contains a decimal point."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "3.14"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INT", 42)
                assert result == 42

    def test_min_validation_enforced(self, caplog):
        """Should return default when value is below minimum."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "0"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INT", 10, min_val=1)
                assert result == 10
                assert "below minimum" in caplog.text

    def test_max_validation_enforced(self, caplog):
        """Should return default when value is above maximum."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "70000"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INT", 9000, max_val=65535)
                assert result == 9000
                assert "above maximum" in caplog.text

    def test_value_within_range_accepted(self):
        """Should accept value that is within min/max range."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "8080"}):
            result = get_int_env("TEST_INT", 9000, min_val=1, max_val=65535)
            assert result == 8080

    def test_negative_values_allowed_when_no_min(self):
        """Should accept negative values when min_val is not specified."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": "-5"}):
            result = get_int_env("TEST_INT", 0)
            assert result == -5

    def test_returns_default_on_empty_string(self, caplog):
        """Should return default and log warning when value is empty string."""
        from config import get_int_env

        with mock.patch.dict(os.environ, {"TEST_INT": ""}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INT", 42)
                assert result == 42
                assert "Invalid TEST_INT=''" in caplog.text

    def test_no_warning_when_env_not_set_with_validation(self, caplog):
        """Should not log warning when env is not set, even if default would fail validation."""
        from config import get_int_env

        # Default of 0 would fail min_val=1 validation, but since env is not set,
        # we should return default without any warning
        with mock.patch.dict(os.environ, {}, clear=True):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("NONEXISTENT_VAR", 0, min_val=1)
                assert result == 0
                assert caplog.text == ""  # No warning should be logged


class TestGetFloatEnv:
    """Tests for get_float_env helper function."""

    def test_returns_default_when_env_not_set(self):
        """Should return default value when environment variable is not set."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {}, clear=True):
            result = get_float_env("NONEXISTENT_VAR", 3.14)
            assert result == 3.14

    def test_parses_valid_float(self):
        """Should parse valid float from environment variable."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "2.5"}):
            result = get_float_env("TEST_FLOAT", 0.0)
            assert result == 2.5

    def test_parses_integer_as_float(self):
        """Should accept integer value and return as float."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "5"}):
            result = get_float_env("TEST_FLOAT", 0.0)
            assert result == 5.0

    def test_returns_default_on_invalid_value(self, caplog):
        """Should return default and log warning when value is not a valid float."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "not_a_number"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0)
                assert result == 1.0
                assert "Invalid TEST_FLOAT='not_a_number'" in caplog.text

    def test_min_validation_enforced(self, caplog):
        """Should return default when value is below minimum."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "0.01"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0, min_val=0.1)
                assert result == 1.0
                assert "below minimum" in caplog.text

    def test_max_validation_enforced(self, caplog):
        """Should return default when value is above maximum."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "100.0"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 5.0, max_val=10.0)
                assert result == 5.0
                assert "above maximum" in caplog.text

    def test_value_within_range_accepted(self):
        """Should accept value that is within min/max range."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "5.5"}):
            result = get_float_env("TEST_FLOAT", 1.0, min_val=0.1, max_val=10.0)
            assert result == 5.5

    def test_returns_default_on_empty_string(self, caplog):
        """Should return default and log warning when value is empty string."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": ""}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0)
                assert result == 1.0
                assert "Invalid TEST_FLOAT=''" in caplog.text

    def test_no_warning_when_env_not_set_with_validation(self, caplog):
        """Should not log warning when env is not set, even if default would fail validation."""
        from config import get_float_env

        # Default of 0.0 would fail min_val=1.0 validation, but since env is not set,
        # we should return default without any warning
        with mock.patch.dict(os.environ, {}, clear=True):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("NONEXISTENT_VAR", 0.0, min_val=1.0)
                assert result == 0.0
                assert caplog.text == ""  # No warning should be logged

    def test_rejects_infinity(self, caplog):
        """Should return default and log warning when value is infinity."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "inf"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0)
                assert result == 1.0
                assert "special float" in caplog.text

        caplog.clear()

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "-inf"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0)
                assert result == 1.0
                assert "special float" in caplog.text

    def test_rejects_nan(self, caplog):
        """Should return default and log warning when value is NaN."""
        from config import get_float_env

        with mock.patch.dict(os.environ, {"TEST_FLOAT": "nan"}):
            with caplog.at_level(logging.WARNING):
                result = get_float_env("TEST_FLOAT", 1.0)
                assert result == 1.0
                assert "special float" in caplog.text


class TestPortValidation:
    """Tests for port number validation in config."""

    def test_public_port_validates_range(self, caplog):
        """PUBLIC_PORT should reject invalid port numbers."""
        from config import get_int_env

        # Port 0 is below minimum
        with mock.patch.dict(os.environ, {"VLOG_PUBLIC_PORT": "0"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("VLOG_PUBLIC_PORT", 9000, min_val=1, max_val=65535)
                assert result == 9000

        caplog.clear()

        # Port above 65535 is invalid
        with mock.patch.dict(os.environ, {"VLOG_PUBLIC_PORT": "70000"}):
            with caplog.at_level(logging.WARNING):
                result = get_int_env("VLOG_PUBLIC_PORT", 9000, min_val=1, max_val=65535)
                assert result == 9000
