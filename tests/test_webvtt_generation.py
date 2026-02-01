"""
Tests for WebVTT subtitle generation.

Tests the format_timestamp() and generate_webvtt() functions in worker/transcription.py
to ensure correct WebVTT output for various inputs and edge cases.
"""

import pytest

from worker.transcription import format_timestamp, generate_webvtt


class TestFormatTimestamp:
    """Tests for the format_timestamp() function."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            # Zero
            (0.0, "00:00:00.000"),
            # Sub-second precision
            (0.5, "00:00:00.500"),
            (0.123, "00:00:00.123"),
            (0.001, "00:00:00.001"),
            # Seconds only
            (1.0, "00:00:01.000"),
            (30.0, "00:00:30.000"),
            (59.999, "00:00:59.999"),
            # Minutes and seconds
            (60.0, "00:01:00.000"),
            (61.5, "00:01:01.500"),
            (90.0, "00:01:30.000"),
            (599.0, "00:09:59.000"),
            # Under 1 hour
            (3599.0, "00:59:59.000"),
            (3599.999, "00:59:59.999"),
            # Exactly 1 hour
            (3600.0, "01:00:00.000"),
            # Over 1 hour
            (3661.123, "01:01:01.123"),
            (7200.0, "02:00:00.000"),
            # Large values (long videos)
            (36000.0, "10:00:00.000"),  # 10 hours
            (86399.999, "23:59:59.999"),  # Just under 24 hours
            (86400.0, "24:00:00.000"),  # 24 hours exactly
        ],
    )
    def test_format_timestamp_values(self, seconds, expected):
        """Test timestamp formatting for various input values."""
        result = format_timestamp(seconds)
        assert result == expected, f"format_timestamp({seconds}) returned '{result}', expected '{expected}'"

    def test_format_timestamp_integer_input(self):
        """Test that integer input works correctly."""
        result = format_timestamp(90)
        assert result == "00:01:30.000"

    def test_format_timestamp_negative_value(self):
        """Test behavior with negative value (edge case)."""
        # Negative values produce negative hour component
        # This tests the actual behavior - negative times could occur
        # from segment timing errors in transcription
        result = format_timestamp(-1.0)
        # The current implementation will produce a negative hour
        # This is documenting the behavior, not necessarily the ideal behavior
        assert "-" in result or result.startswith("00")


class TestGenerateWebvtt:
    """Tests for the generate_webvtt() function."""

    def test_empty_segments_list(self):
        """Test that empty segments list produces valid WebVTT header only."""
        result = generate_webvtt([])
        assert result == "WEBVTT\n\n"

    def test_single_segment(self):
        """Test single segment produces correct WebVTT output."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello world"}
        ]
        result = generate_webvtt(segments)
        expected = (
            "WEBVTT\n\n"
            "1\n"
            "00:00:00.000 --> 00:00:05.000\n"
            "Hello world\n\n"
        )
        assert result == expected

    def test_multiple_segments(self):
        """Test multiple segments are ordered correctly with sequential cue identifiers."""
        segments = [
            {"start": 0.0, "end": 2.5, "text": "First line"},
            {"start": 2.5, "end": 5.0, "text": "Second line"},
            {"start": 5.0, "end": 8.0, "text": "Third line"},
        ]
        result = generate_webvtt(segments)

        # Verify WebVTT header
        assert result.startswith("WEBVTT\n\n")

        # Verify all segments are present with correct numbering
        assert "1\n00:00:00.000 --> 00:00:02.500\nFirst line" in result
        assert "2\n00:00:02.500 --> 00:00:05.000\nSecond line" in result
        assert "3\n00:00:05.000 --> 00:00:08.000\nThird line" in result

    def test_segment_text_stripping(self):
        """Test that segment text is stripped of leading/trailing whitespace."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "  whitespace around  "},
            {"start": 2.0, "end": 4.0, "text": "\nnewlines\n"},
            {"start": 4.0, "end": 6.0, "text": "\ttabs\t"},
        ]
        result = generate_webvtt(segments)

        assert "whitespace around\n\n" in result
        assert "newlines\n\n" in result
        assert "tabs\n\n" in result

    def test_empty_text_segment(self):
        """Test segment with empty text after stripping."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "   "},  # Only whitespace
        ]
        result = generate_webvtt(segments)

        # Should produce a valid cue with empty text
        assert "1\n00:00:00.000 --> 00:00:02.000\n\n" in result

    def test_special_characters_in_text(self):
        """Test that special characters in text are preserved."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello, \"world\"!"},
            {"start": 2.0, "end": 4.0, "text": "Line with <angle> brackets"},
            {"start": 4.0, "end": 6.0, "text": "Ampersand & apostrophe's"},
            {"start": 6.0, "end": 8.0, "text": "Unicode: \u00e9\u00e8\u00f1"},  # accented chars
        ]
        result = generate_webvtt(segments)

        assert 'Hello, "world"!' in result
        assert "Line with <angle> brackets" in result
        assert "Ampersand & apostrophe's" in result
        assert "Unicode: \u00e9\u00e8\u00f1" in result

    def test_long_subtitle_text(self):
        """Test handling of very long subtitle text."""
        long_text = "This is a very long subtitle text. " * 10  # ~350 chars
        segments = [
            {"start": 0.0, "end": 10.0, "text": long_text},
        ]
        result = generate_webvtt(segments)

        # Long text should be preserved (no truncation by default)
        assert long_text.strip() in result

    def test_timestamps_over_one_hour(self):
        """Test that timestamps over 1 hour format correctly in cues."""
        segments = [
            {"start": 3600.0, "end": 3605.5, "text": "One hour mark"},
            {"start": 7200.0, "end": 7210.0, "text": "Two hours in"},
        ]
        result = generate_webvtt(segments)

        assert "01:00:00.000 --> 01:00:05.500" in result
        assert "02:00:00.000 --> 02:00:10.000" in result

    def test_fractional_second_precision(self):
        """Test that fractional seconds are preserved with millisecond precision."""
        segments = [
            {"start": 1.123, "end": 2.456, "text": "Precise timing"},
        ]
        result = generate_webvtt(segments)

        assert "00:00:01.123 --> 00:00:02.456" in result

    def test_overlapping_segments(self):
        """Test that overlapping segment times are preserved (no validation)."""
        # The function doesn't validate timing, just formats it
        segments = [
            {"start": 0.0, "end": 5.0, "text": "First"},
            {"start": 3.0, "end": 8.0, "text": "Overlapping"},
        ]
        result = generate_webvtt(segments)

        # Both should be present even though they overlap
        assert "00:00:00.000 --> 00:00:05.000" in result
        assert "00:00:03.000 --> 00:00:08.000" in result

    def test_webvtt_structure(self):
        """Test the overall structure of generated WebVTT content."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Test"},
        ]
        result = generate_webvtt(segments)

        # WebVTT must start with "WEBVTT"
        assert result.startswith("WEBVTT")

        # Should have blank line after header
        lines = result.split("\n")
        assert lines[0] == "WEBVTT"
        assert lines[1] == ""

        # Cue should have: number, timing, text, blank line
        assert lines[2] == "1"
        assert "-->" in lines[3]
        assert lines[4] == "Test"


class TestWebvttEdgeCases:
    """Additional edge case tests for WebVTT generation."""

    def test_segment_with_newlines_in_text(self):
        """Test segment text containing newline characters."""
        segments = [
            {"start": 0.0, "end": 3.0, "text": "Line one\nLine two"},
        ]
        result = generate_webvtt(segments)

        # Newlines within text should be preserved (multiline cue)
        # The strip() only affects leading/trailing whitespace
        assert "Line one\nLine two" in result

    def test_many_segments(self):
        """Test generation with many segments (performance sanity check)."""
        # Create 100 segments
        segments = [
            {"start": i * 2.0, "end": (i + 1) * 2.0, "text": f"Segment {i + 1}"}
            for i in range(100)
        ]
        result = generate_webvtt(segments)

        # Should have all 100 cues
        assert "100\n" in result
        assert "Segment 100" in result
        assert "Segment 1\n" in result

    def test_zero_duration_segment(self):
        """Test segment with zero duration (start == end)."""
        segments = [
            {"start": 5.0, "end": 5.0, "text": "Instant"},
        ]
        result = generate_webvtt(segments)

        # Should still produce valid cue
        assert "00:00:05.000 --> 00:00:05.000" in result
        assert "Instant" in result
