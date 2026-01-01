"""
Tests for cursor-based pagination utilities.
"""

from datetime import datetime, timezone

from api.pagination import (
    CURSOR_VERSION,
    decode_cursor,
    encode_cursor,
    validate_cursor,
)


class TestEncodeCursor:
    """Test suite for encode_cursor function."""

    def test_encode_basic_cursor(self):
        """Test encoding a basic cursor."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        record_id = 123

        cursor = encode_cursor(timestamp, record_id)

        assert cursor is not None
        assert isinstance(cursor, str)
        assert len(cursor) > 0

    def test_encode_cursor_without_timezone(self):
        """Test encoding a cursor with naive datetime adds UTC timezone."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0)  # No timezone
        record_id = 456

        cursor = encode_cursor(timestamp, record_id)

        # Should not raise, should handle gracefully
        assert cursor is not None

        # Decode and verify UTC was added
        decoded = decode_cursor(cursor)
        assert decoded is not None
        assert decoded[0].tzinfo == timezone.utc

    def test_encode_cursor_deterministic(self):
        """Test that encoding the same data produces the same cursor."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        record_id = 789

        cursor1 = encode_cursor(timestamp, record_id)
        cursor2 = encode_cursor(timestamp, record_id)

        assert cursor1 == cursor2

    def test_encode_different_data_different_cursor(self):
        """Test that different data produces different cursors."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        cursor1 = encode_cursor(timestamp, 100)
        cursor2 = encode_cursor(timestamp, 101)

        assert cursor1 != cursor2


class TestDecodeCursor:
    """Test suite for decode_cursor function."""

    def test_decode_valid_cursor(self):
        """Test decoding a valid cursor."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        record_id = 123

        cursor = encode_cursor(timestamp, record_id)
        decoded = decode_cursor(cursor)

        assert decoded is not None
        assert decoded[0] == timestamp
        assert decoded[1] == record_id

    def test_decode_invalid_base64(self):
        """Test decoding invalid base64 returns None."""
        result = decode_cursor("not-valid-base64!!!")
        assert result is None

    def test_decode_wrong_version(self):
        """Test decoding cursor with wrong version returns None."""
        import base64

        # Create a cursor with wrong version
        wrong_version = "999|2025-01-15T12:30:00+00:00|123"
        bad_cursor = base64.urlsafe_b64encode(wrong_version.encode()).decode()

        result = decode_cursor(bad_cursor)
        assert result is None

    def test_decode_malformed_cursor_missing_parts(self):
        """Test decoding cursor with missing parts returns None."""
        import base64

        # Only two parts instead of three
        malformed = f"{CURSOR_VERSION}|2025-01-15T12:30:00+00:00"
        bad_cursor = base64.urlsafe_b64encode(malformed.encode()).decode()

        result = decode_cursor(bad_cursor)
        assert result is None

    def test_decode_invalid_timestamp(self):
        """Test decoding cursor with invalid timestamp returns None."""
        import base64

        invalid = f"{CURSOR_VERSION}|not-a-timestamp|123"
        bad_cursor = base64.urlsafe_b64encode(invalid.encode()).decode()

        result = decode_cursor(bad_cursor)
        assert result is None

    def test_decode_invalid_id(self):
        """Test decoding cursor with non-integer ID returns None."""
        import base64

        invalid = f"{CURSOR_VERSION}|2025-01-15T12:30:00+00:00|not-an-id"
        bad_cursor = base64.urlsafe_b64encode(invalid.encode()).decode()

        result = decode_cursor(bad_cursor)
        assert result is None

    def test_decode_empty_string(self):
        """Test decoding empty string returns None."""
        result = decode_cursor("")
        assert result is None


class TestValidateCursor:
    """Test suite for validate_cursor function."""

    def test_validate_none_cursor(self):
        """Test validating None cursor returns None."""
        result = validate_cursor(None)
        assert result is None

    def test_validate_empty_cursor(self):
        """Test validating empty string cursor returns None."""
        result = validate_cursor("")
        assert result is None

    def test_validate_valid_cursor(self):
        """Test validating a valid cursor."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        record_id = 123

        cursor = encode_cursor(timestamp, record_id)
        result = validate_cursor(cursor)

        assert result is not None
        assert result[0] == timestamp
        assert result[1] == record_id

    def test_validate_invalid_cursor(self):
        """Test validating an invalid cursor returns None."""
        result = validate_cursor("invalid-cursor-data")
        assert result is None


class TestCursorRoundtrip:
    """Test suite for cursor encode/decode roundtrip."""

    def test_roundtrip_with_microseconds(self):
        """Test cursor roundtrip preserves microseconds."""
        timestamp = datetime(2025, 1, 15, 12, 30, 45, 123456, tzinfo=timezone.utc)
        record_id = 999

        cursor = encode_cursor(timestamp, record_id)
        decoded = decode_cursor(cursor)

        assert decoded is not None
        assert decoded[0] == timestamp
        assert decoded[1] == record_id

    def test_roundtrip_large_id(self):
        """Test cursor roundtrip with large ID."""
        timestamp = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        record_id = 9999999999

        cursor = encode_cursor(timestamp, record_id)
        decoded = decode_cursor(cursor)

        assert decoded is not None
        assert decoded[1] == record_id

    def test_roundtrip_different_timezones(self):
        """Test cursor roundtrip normalizes to UTC."""
        from datetime import timedelta

        # Create a non-UTC timezone
        pst = timezone(timedelta(hours=-8))
        timestamp = datetime(2025, 1, 15, 4, 30, 0, tzinfo=pst)

        cursor = encode_cursor(timestamp, 123)
        decoded = decode_cursor(cursor)

        assert decoded is not None
        # The decoded timestamp should be equivalent (same instant in time)
        assert decoded[0] == timestamp
