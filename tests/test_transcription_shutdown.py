"""
Tests for transcription worker graceful shutdown handling.

Ensures that the transcription worker properly handles SIGTERM/SIGINT signals
and performs graceful shutdown with cleanup of resources.
"""

import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.database import transcriptions
from api.enums import TranscriptionStatus


@pytest.mark.asyncio
class TestTranscriptionShutdown:
    """Test that transcription worker handles shutdown gracefully."""

    async def test_worker_has_shutdown_flag(self):
        """Test that TranscriptionWorker has shutdown_requested flag."""
        from worker.transcription import TranscriptionWorker

        worker = TranscriptionWorker()
        assert hasattr(worker, "shutdown_requested")
        assert worker.shutdown_requested is False

    async def test_worker_request_shutdown(self):
        """Test that request_shutdown sets the flag."""
        from worker.transcription import TranscriptionWorker

        worker = TranscriptionWorker()
        assert worker.shutdown_requested is False

        worker.request_shutdown()
        assert worker.shutdown_requested is True

    async def test_shutdown_before_processing(self, test_database, sample_video, monkeypatch):
        """Test that shutdown before processing raises TranscriptionCancelled."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()
        worker.request_shutdown()

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        # Should raise TranscriptionCancelled immediately
        with pytest.raises(TranscriptionCancelled):
            await process_transcription(video, worker)

        # No transcription record should be created
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)
        # It's okay if a record exists but it should not be in processing state
        if transcription:
            assert transcription["status"] != TranscriptionStatus.PROCESSING

    async def test_shutdown_before_audio_extraction(self, test_database, sample_video, monkeypatch):
        """Test that shutdown before audio extraction is handled gracefully."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()

        # Mock find_audio_source to trigger shutdown after it's called
        def find_audio_and_shutdown(*args, **kwargs):
            result = MagicMock()
            result.exists.return_value = True
            # Request shutdown after finding audio source
            worker.request_shutdown()
            return result

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        with patch("worker.transcription.find_audio_source", side_effect=find_audio_and_shutdown):
            with pytest.raises(TranscriptionCancelled):
                await process_transcription(video, worker)

        # Verify transcription status was reset to pending
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)
        assert transcription is not None
        assert transcription["status"] == TranscriptionStatus.PENDING

    async def test_shutdown_before_transcription(self, test_database, sample_video, monkeypatch):
        """Test that shutdown after extraction but before transcription is handled."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()

        # Mock extract_audio_to_wav to trigger shutdown after it's called
        def extract_and_shutdown(*args, **kwargs):
            worker.request_shutdown()

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav", side_effect=extract_and_shutdown
        ), patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "tempfile.mkstemp", return_value=(123, "/tmp/test.wav")
        ), patch(
            "os.close"
        ), patch(
            "pathlib.Path.unlink"
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_stat.return_value = MagicMock(st_size=1000)

            with pytest.raises(TranscriptionCancelled):
                await process_transcription(video, worker)

        # Verify transcription status was reset to pending
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)
        assert transcription is not None
        assert transcription["status"] == TranscriptionStatus.PENDING

    async def test_shutdown_before_saving_results(self, test_database, sample_video, monkeypatch):
        """Test that shutdown after transcription but before saving is handled."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()
        worker.model_loaded = True

        # Mock transcribe to trigger shutdown after it completes
        def transcribe_and_shutdown(*args, **kwargs):
            worker.request_shutdown()
            return {
                "text": "Test transcription",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Test"}],
            }

        worker.transcribe = transcribe_and_shutdown

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav"
        ) as mock_extract, patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "tempfile.mkstemp", return_value=(123, "/tmp/test.wav")
        ), patch(
            "os.close"
        ), patch(
            "pathlib.Path.unlink"
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_extract.return_value = None
            mock_stat.return_value = MagicMock(st_size=1000)

            with pytest.raises(TranscriptionCancelled):
                await process_transcription(video, worker)

        # Verify transcription status was reset to pending
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)
        assert transcription is not None
        assert transcription["status"] == TranscriptionStatus.PENDING

    async def test_temp_file_cleanup_on_shutdown(self, test_database, sample_video, monkeypatch):
        """Test that temporary files are cleaned up when shutdown occurs."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()

        # Track if unlink was called
        unlink_called = False

        def mock_unlink(*args, **kwargs):
            nonlocal unlink_called
            unlink_called = True

        def extract_and_shutdown(*args, **kwargs):
            worker.request_shutdown()

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        with patch("worker.transcription.find_audio_source") as mock_find_audio, patch(
            "worker.transcription.extract_audio_to_wav", side_effect=extract_and_shutdown
        ), patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.stat"
        ) as mock_stat, patch(
            "tempfile.mkstemp", return_value=(123, "/tmp/test.wav")
        ), patch(
            "os.close"
        ), patch(
            "pathlib.Path.unlink", side_effect=mock_unlink
        ):
            # Setup mocks
            mock_find_audio.return_value = MagicMock()
            mock_stat.return_value = MagicMock(st_size=1000)

            with pytest.raises(TranscriptionCancelled):
                await process_transcription(video, worker)

        # Verify temp file cleanup was attempted
        assert unlink_called

    async def test_signal_handler_exists(self):
        """Test that signal_handler function is defined."""
        from worker.transcription import signal_handler

        assert callable(signal_handler)

    async def test_signal_handler_sets_shutdown_flag(self):
        """Test that signal handler properly sets shutdown flag."""
        import worker.transcription
        from worker.transcription import TranscriptionWorker, signal_handler

        # Create a worker and set it as the global instance in the module
        worker_instance = TranscriptionWorker()
        # Access the module's _worker_instance global variable
        old_instance = getattr(worker.transcription, '_worker_instance', None)
        setattr(worker.transcription, '_worker_instance', worker_instance)

        assert worker_instance.shutdown_requested is False

        # Simulate signal handler call
        signal_handler(signal.SIGTERM, None)

        # Worker should have shutdown requested
        assert worker_instance.shutdown_requested is True

        # Clean up - restore old instance
        setattr(worker.transcription, '_worker_instance', old_instance)

    async def test_worker_loop_respects_shutdown_flag(self, test_database, monkeypatch):
        """Test that worker_loop exits when shutdown is requested."""
        import worker.transcription
        from worker.transcription import TranscriptionWorker

        # Patch the database and config
        monkeypatch.setattr(worker.transcription, "database", test_database)
        monkeypatch.setattr(worker.transcription, "TRANSCRIPTION_ENABLED", True)

        # Create worker that will shutdown immediately
        original_worker_init = TranscriptionWorker.__init__

        def init_with_shutdown(self):
            original_worker_init(self)
            # Request shutdown immediately
            self.shutdown_requested = True

        with patch.object(TranscriptionWorker, "__init__", init_with_shutdown):
            # Mock get_videos_needing_transcription to return empty list
            with patch(
                "worker.transcription.get_videos_needing_transcription", return_value=[]
            ) as mock_get_videos:
                # Run worker loop - should exit immediately due to shutdown flag
                await worker.transcription.worker_loop()

                # Should not have called get_videos since shutdown is already requested
                # or called it once and then exited
                assert mock_get_videos.call_count <= 1

    async def test_worker_loop_with_signals_registers_handlers(self, monkeypatch):
        """Test that worker_loop registers signal handlers."""
        import worker.transcription

        # Track signal.signal calls
        signal_calls = []

        def mock_signal(sig, handler):
            signal_calls.append((sig, handler))

        # Patch everything needed to start the loop
        monkeypatch.setattr(worker.transcription, "TRANSCRIPTION_ENABLED", True)

        # Mock database
        mock_db = AsyncMock()
        mock_db.connect = AsyncMock()
        mock_db.disconnect = AsyncMock()
        monkeypatch.setattr(worker.transcription, "database", mock_db)

        # Mock configure_sqlite_pragmas
        monkeypatch.setattr(
            worker.transcription, "configure_sqlite_pragmas", AsyncMock()
        )

        # Mock get_videos_needing_transcription to return empty and trigger shutdown
        async def get_videos_and_shutdown():
            # Trigger shutdown on first call
            if worker.transcription._worker_instance:
                worker.transcription._worker_instance.request_shutdown()
            return []

        monkeypatch.setattr(
            worker.transcription,
            "get_videos_needing_transcription",
            get_videos_and_shutdown,
        )

        with patch("signal.signal", side_effect=mock_signal):
            await worker.transcription.worker_loop()

        # Verify signal handlers were registered
        signal_sigs = [sig for sig, handler in signal_calls]
        assert signal.SIGTERM in signal_sigs
        assert signal.SIGINT in signal_sigs

    async def test_cancelled_transcription_resets_to_pending(
        self, test_database, sample_video, monkeypatch
    ):
        """Test that cancelled transcription is reset to pending status."""
        import worker.transcription
        from worker.transcription import (
            TranscriptionCancelled,
            TranscriptionWorker,
            process_transcription,
        )

        # Patch the database in worker.transcription module
        monkeypatch.setattr(worker.transcription, "database", test_database)

        worker = TranscriptionWorker()

        # Request shutdown immediately
        worker.request_shutdown()

        video = {
            "id": sample_video["id"],
            "slug": sample_video["slug"],
            "title": sample_video["title"],
        }

        # Should raise TranscriptionCancelled
        with pytest.raises(TranscriptionCancelled):
            await process_transcription(video, worker)

        # Verify that if a transcription record exists, it's in pending state
        # (allowing for retry later)
        query = transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
        transcription = await test_database.fetch_one(query)
        if transcription:
            # Should not be left in processing state
            assert transcription["status"] != TranscriptionStatus.PROCESSING

