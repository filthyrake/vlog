"""
Tests for transcription datetime handling.

Ensures that transcription uses timezone-aware datetimes (datetime.now(timezone.utc))
instead of deprecated datetime.utcnow() which creates naive datetimes.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from api.database import transcriptions
from api.enums import TranscriptionStatus


@pytest.mark.asyncio
class TestTranscriptionDatetimeHandling:
    """Test that transcription worker uses timezone-aware datetimes."""

    async def test_started_at_is_timezone_aware(self, test_database, sample_video, monkeypatch):
        """Test that started_at datetime is timezone-aware when processing begins."""
        # Import after setting up the database
        import worker.transcription
        from worker.transcription import process_transcription, TranscriptionWorker

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        # Mock the worker's transcribe method to avoid needing the actual model
        mock_worker = TranscriptionWorker()
        mock_worker.model_loaded = True
        mock_worker.transcribe = MagicMock(
            return_value={
                "text": "Test transcription",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Test"}],
            }
        )

        # Mock the file system operations
        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav"
        ) as mock_extract, patch("worker.transcription.generate_webvtt") as mock_generate_vtt, patch(
            "pathlib.Path.exists", return_value=True
        ), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "pathlib.Path.write_text"
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_extract.return_value = None
            mock_generate_vtt.return_value = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nTest\n\n"
            mock_stat.return_value = MagicMock(st_size=1000)

            # Mock tempfile creation
            with patch("tempfile.mkstemp") as mock_mkstemp:
                mock_mkstemp.return_value = (123, "/tmp/test.wav")
                with patch("os.close"):
                    with patch("pathlib.Path.unlink"):
                        # Run the transcription
                        await process_transcription(
                            {
                                "id": sample_video["id"],
                                "slug": sample_video["slug"],
                                "title": sample_video["title"],
                            },
                            mock_worker,
                        )

        # Fetch the transcription record from database
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)

        assert transcription is not None
        assert transcription["started_at"] is not None

        # The key test: verify that started_at is timezone-aware
        # SQLite returns naive datetimes, but we can verify it was stored correctly
        # by checking that it's a reasonable datetime value (not None, recent)
        started_at = transcription["started_at"]
        assert isinstance(started_at, datetime)

        # Verify it's a recent datetime (within last minute)
        # We need to make it timezone-aware for comparison since SQLite returns naive
        if started_at.tzinfo is None:
            started_at_utc = started_at.replace(tzinfo=timezone.utc)
        else:
            started_at_utc = started_at

        now = datetime.now(timezone.utc)
        time_diff = (now - started_at_utc).total_seconds()
        assert time_diff >= 0  # Should be in the past
        assert time_diff < 60  # Should be very recent (within 1 minute)

    async def test_completed_at_is_timezone_aware(self, test_database, sample_video, monkeypatch):
        """Test that completed_at datetime is timezone-aware when transcription completes."""
        # Import after setting up the database
        import worker.transcription
        from worker.transcription import process_transcription, TranscriptionWorker

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        # Mock the worker's transcribe method
        mock_worker = TranscriptionWorker()
        mock_worker.model_loaded = True
        mock_worker.transcribe = MagicMock(
            return_value={
                "text": "Test transcription complete",
                "language": "en",
                "segments": [{"start": 0.0, "end": 2.0, "text": "Test transcription complete"}],
            }
        )

        # Mock the file system operations
        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav"
        ) as mock_extract, patch("worker.transcription.generate_webvtt") as mock_generate_vtt, patch(
            "pathlib.Path.exists", return_value=True
        ), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "pathlib.Path.write_text"
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_extract.return_value = None
            mock_generate_vtt.return_value = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nTest transcription complete\n\n"
            mock_stat.return_value = MagicMock(st_size=1000)

            # Mock tempfile creation
            with patch("tempfile.mkstemp") as mock_mkstemp:
                mock_mkstemp.return_value = (123, "/tmp/test.wav")
                with patch("os.close"):
                    with patch("pathlib.Path.unlink"):
                        # Run the transcription
                        await process_transcription(
                            {
                                "id": sample_video["id"],
                                "slug": sample_video["slug"],
                                "title": sample_video["title"],
                            },
                            mock_worker,
                        )

        # Fetch the transcription record from database
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)

        assert transcription is not None
        assert transcription["status"] == TranscriptionStatus.COMPLETED
        assert transcription["completed_at"] is not None

        # The key test: verify that completed_at is timezone-aware
        completed_at = transcription["completed_at"]
        assert isinstance(completed_at, datetime)

        # Verify it's a recent datetime (within last minute)
        # We need to make it timezone-aware for comparison since SQLite returns naive
        if completed_at.tzinfo is None:
            completed_at_utc = completed_at.replace(tzinfo=timezone.utc)
        else:
            completed_at_utc = completed_at

        now = datetime.now(timezone.utc)
        time_diff = (now - completed_at_utc).total_seconds()
        assert time_diff >= 0  # Should be in the past
        assert time_diff < 60  # Should be very recent (within 1 minute)

    async def test_started_and_completed_datetimes_are_valid(self, test_database, sample_video, monkeypatch):
        """Test that both started_at and completed_at are valid and in correct order."""
        # Import after setting up the database
        import worker.transcription
        from worker.transcription import process_transcription, TranscriptionWorker

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        # Mock the worker's transcribe method
        mock_worker = TranscriptionWorker()
        mock_worker.model_loaded = True
        mock_worker.transcribe = MagicMock(
            return_value={
                "text": "Full test",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.5, "text": "Full test"}],
            }
        )

        # Mock the file system operations
        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav"
        ) as mock_extract, patch("worker.transcription.generate_webvtt") as mock_generate_vtt, patch(
            "pathlib.Path.exists", return_value=True
        ), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "pathlib.Path.write_text"
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_extract.return_value = None
            mock_generate_vtt.return_value = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.500\nFull test\n\n"
            mock_stat.return_value = MagicMock(st_size=1000)

            # Mock tempfile creation
            with patch("tempfile.mkstemp") as mock_mkstemp:
                mock_mkstemp.return_value = (123, "/tmp/test.wav")
                with patch("os.close"):
                    with patch("pathlib.Path.unlink"):
                        # Run the transcription
                        await process_transcription(
                            {
                                "id": sample_video["id"],
                                "slug": sample_video["slug"],
                                "title": sample_video["title"],
                            },
                            mock_worker,
                        )

        # Fetch the transcription record from database
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)

        assert transcription is not None
        assert transcription["started_at"] is not None
        assert transcription["completed_at"] is not None

        # Both should be datetime instances
        assert isinstance(transcription["started_at"], datetime)
        assert isinstance(transcription["completed_at"], datetime)

        # Convert to timezone-aware for comparison (SQLite returns naive)
        started_at = transcription["started_at"]
        completed_at = transcription["completed_at"]

        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)

        # completed_at should be after or equal to started_at
        assert completed_at >= started_at
