"""
Tests for partition manager utilities.
"""


import pytest

from api.partition_manager import (
    MAX_PARTITION_YEAR,
    MIN_PARTITION_YEAR,
    PARTITION_PREFIX,
    _validate_partition_name,
    _validate_partition_params,
)


class TestValidatePartitionParams:
    """Test suite for _validate_partition_params function."""

    def test_valid_params(self):
        """Test valid year and month pass validation."""
        # Should not raise
        _validate_partition_params(2025, 1)
        _validate_partition_params(2025, 12)
        _validate_partition_params(MIN_PARTITION_YEAR, 6)
        _validate_partition_params(MAX_PARTITION_YEAR, 6)

    def test_invalid_year_type(self):
        """Test non-integer year raises ValueError."""
        with pytest.raises(ValueError, match="Year must be an integer"):
            _validate_partition_params("2025", 1)

        with pytest.raises(ValueError, match="Year must be an integer"):
            _validate_partition_params(2025.5, 1)

    def test_invalid_month_type(self):
        """Test non-integer month raises ValueError."""
        with pytest.raises(ValueError, match="Month must be an integer"):
            _validate_partition_params(2025, "1")

        with pytest.raises(ValueError, match="Month must be an integer"):
            _validate_partition_params(2025, 1.5)

    def test_year_below_minimum(self):
        """Test year below minimum raises ValueError."""
        with pytest.raises(ValueError, match=f"Year must be between {MIN_PARTITION_YEAR}"):
            _validate_partition_params(MIN_PARTITION_YEAR - 1, 1)

    def test_year_above_maximum(self):
        """Test year above maximum raises ValueError."""
        with pytest.raises(ValueError, match="Year must be between"):
            _validate_partition_params(MAX_PARTITION_YEAR + 1, 1)

    def test_month_below_minimum(self):
        """Test month below 1 raises ValueError."""
        with pytest.raises(ValueError, match="Month must be between 1 and 12"):
            _validate_partition_params(2025, 0)

    def test_month_above_maximum(self):
        """Test month above 12 raises ValueError."""
        with pytest.raises(ValueError, match="Month must be between 1 and 12"):
            _validate_partition_params(2025, 13)

    def test_negative_values(self):
        """Test negative values raise ValueError."""
        with pytest.raises(ValueError):
            _validate_partition_params(-2025, 1)

        with pytest.raises(ValueError):
            _validate_partition_params(2025, -1)


class TestValidatePartitionName:
    """Test suite for _validate_partition_name function."""

    def test_valid_partition_names(self):
        """Test valid partition names pass validation."""
        # Should not raise
        _validate_partition_name(f"{PARTITION_PREFIX}202501")
        _validate_partition_name(f"{PARTITION_PREFIX}202512")
        _validate_partition_name(f"{PARTITION_PREFIX}209912")

    def test_invalid_prefix(self):
        """Test invalid prefix raises ValueError."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name("wrong_prefix_202501")

        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name("playback_session_202501")  # Missing 's'

    def test_wrong_number_of_digits(self):
        """Test wrong number of digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}20251")  # 5 digits

        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}2025012")  # 7 digits

    def test_non_numeric_suffix(self):
        """Test non-numeric suffix raises ValueError."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}2025ab")

        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}abcdef")

    def test_sql_injection_attempt(self):
        """Test SQL injection attempts are rejected."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}202501; DROP TABLE users;")

        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}202501--")

        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(f"{PARTITION_PREFIX}202501' OR '1'='1")

    def test_empty_string(self):
        """Test empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name("")

    def test_only_prefix(self):
        """Test prefix without digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid partition name format"):
            _validate_partition_name(PARTITION_PREFIX)


class TestPartitionNamingConvention:
    """Test partition naming convention."""

    def test_partition_name_format(self):
        """Test expected partition name format."""
        year = 2025
        month = 1
        expected_name = f"{PARTITION_PREFIX}{year:04d}{month:02d}"

        assert expected_name == "playback_sessions_202501"

    def test_partition_name_zero_padded(self):
        """Test partition names are properly zero-padded."""
        assert f"{PARTITION_PREFIX}{2025:04d}{1:02d}" == "playback_sessions_202501"
        assert f"{PARTITION_PREFIX}{2025:04d}{12:02d}" == "playback_sessions_202512"

    def test_min_max_year_constants(self):
        """Test year constants are reasonable."""
        assert MIN_PARTITION_YEAR >= 2000  # Reasonable minimum
        assert MAX_PARTITION_YEAR <= 2200  # Reasonable maximum
        assert MIN_PARTITION_YEAR < MAX_PARTITION_YEAR
