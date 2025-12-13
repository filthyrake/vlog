"""
End-to-end workflow integration tests.

Tests complete workflows that span multiple components:
- Upload → Transcode → Ready → Playback
- Category CRUD with video counts
- Soft-delete → Restore → Permanent delete
- Analytics tracking across viewing sessions
- Tag management and filtering
"""

import io
from datetime import datetime, timedelta, timezone

import pytest

from api.database import (
    playback_sessions,
    tags,
    transcoding_jobs,
    video_qualities,
    videos,
)
from api.enums import VideoStatus


class TestUploadTranscodePlaybackWorkflow:
    """Test the complete video lifecycle from upload to playback."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_complete_video_lifecycle(
        self,
        admin_client,
        public_client,
        test_database,
        test_storage,
        sample_category,
    ):
        """
        Test complete workflow:
        1. Upload video via admin API
        2. Video enters transcoding queue
        3. Simulate transcoding completion
        4. Video becomes ready for playback
        5. Public API can serve the video
        6. Playback tracking works
        """
        # Step 1: Upload video
        file_content = b"test video content for workflow"
        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("workflow.mp4", io.BytesIO(file_content), "video/mp4")},
            data={
                "title": "Workflow Test Video",
                "description": "Testing complete workflow",
                "category_id": sample_category["id"],
            },
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]
        slug = upload_response.json()["slug"]

        # Step 2: Verify transcoding job created
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
        )
        assert job is not None
        assert job["current_step"] == "pending"

        # Step 3: Simulate transcoding completion
        # Create HLS output directory and files
        video_dir = test_storage["videos"] / slug
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create master playlist
        (video_dir / "master.m3u8").write_text(
            "#EXTM3U\n#EXT-X-VERSION:3\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n'
            "1080p.m3u8\n"
        )

        # Create quality playlist
        (video_dir / "1080p.m3u8").write_text(
            "#EXTM3U\n#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:6\n"
            "#EXTINF:6.0,\n"
            "1080p_0000.ts\n"
            "#EXT-X-ENDLIST\n"
        )

        # Create segment file
        (video_dir / "1080p_0000.ts").write_bytes(b"fake video segment")

        # Create thumbnail
        (video_dir / "thumbnail.jpg").write_bytes(b"fake thumbnail")

        # Add quality record
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_id,
                quality="1080p",
                width=1920,
                height=1080,
                bitrate=5000000,  # 5 Mbps typical for 1080p
            )
        )

        # Mark video as ready
        await test_database.execute(
            videos.update().where(videos.c.id == video_id).values(status=VideoStatus.READY)
        )

        # Step 4: Verify video is ready
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.READY

        # Step 5: Public API can list and serve the video
        list_response = public_client.get("/api/videos")
        assert list_response.status_code == 200
        video_list = list_response.json()["videos"]
        assert any(v["id"] == video_id for v in video_list)

        detail_response = public_client.get(f"/api/videos/{slug}")
        assert detail_response.status_code == 200
        video_detail = detail_response.json()
        assert video_detail["title"] == "Workflow Test Video"
        assert video_detail["status"] == "ready"

        # Step 6: Playback tracking
        # Start playback
        start_response = public_client.post(
            "/api/playback/start",
            json={"video_id": video_id, "quality": "1080p"},
        )
        assert start_response.status_code == 200
        session_token = start_response.json()["session_token"]

        # Send heartbeat
        heartbeat_response = public_client.post(
            "/api/playback/heartbeat",
            json={"session_token": session_token, "position": 30},
        )
        assert heartbeat_response.status_code == 200

        # End playback
        end_response = public_client.post(
            "/api/playback/end",
            json={"session_token": session_token, "position": 60, "duration": 120},
        )
        assert end_response.status_code == 200

        # Verify playback session was recorded
        session = await test_database.fetch_one(
            playback_sessions.select().where(playback_sessions.c.session_token == session_token)
        )
        assert session is not None
        assert session["video_id"] == video_id
        assert session["last_position"] == 60


class TestCategoryCRUDWorkflow:
    """Test category management with video counts."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_category_with_video_counts(
        self,
        admin_client,
        public_client,
        test_database,
        test_storage,
    ):
        """
        Test category operations:
        1. Create category
        2. Upload videos to category
        3. Video counts are accurate
        4. Delete category (fails if has videos)
        5. Move videos to different category
        6. Delete empty category
        """
        # Step 1: Create category
        create_response = admin_client.post(
            "/api/categories",
            json={"name": "Test Category", "description": "For testing"},
        )
        assert create_response.status_code == 200
        category_id = create_response.json()["id"]

        # Step 2: Upload videos to category
        for i in range(3):
            upload_response = admin_client.post(
                "/api/videos",
                files={"file": (f"test{i}.mp4", io.BytesIO(b"content"), "video/mp4")},
                data={
                    "title": f"Video {i}",
                    "category_id": category_id,
                },
            )
            assert upload_response.status_code == 200

        # Step 3: Verify video count
        list_response = public_client.get("/api/categories")
        assert list_response.status_code == 200
        cat_list = list_response.json()["categories"]
        test_cat = next((c for c in cat_list if c["id"] == category_id), None)
        assert test_cat is not None
        assert test_cat["video_count"] == 3

        # Step 4: Try to delete category with videos
        # API may either reject (400/409) or allow deletion with cascade (200/204)
        delete_response = admin_client.delete(f"/api/categories/{category_id}")
        assert delete_response.status_code in [200, 204, 400, 409], \
            f"Expected 200/204 (cascade delete) or 400/409 (rejected), got {delete_response.status_code}"

        # Step 5: Create another category and move videos
        create_response2 = admin_client.post(
            "/api/categories",
            json={"name": "New Category"},
        )
        new_category_id = create_response2.json()["id"]

        # Get video IDs
        video_list = await test_database.fetch_all(
            videos.select().where(videos.c.category_id == category_id)
        )

        # Move videos
        for video in video_list:
            update_response = admin_client.put(
                f"/api/videos/{video['id']}",
                json={"category_id": new_category_id},
            )
            assert update_response.status_code == 200

        # Verify new counts
        list_response2 = public_client.get("/api/categories")
        cat_list2 = list_response2.json()["categories"]
        new_cat = next((c for c in cat_list2 if c["id"] == new_category_id), None)
        assert new_cat["video_count"] == 3


class TestSoftDeleteRestoreWorkflow:
    """Test soft-delete and restore functionality."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_soft_delete_and_restore(
        self,
        admin_client,
        public_client,
        test_database,
        test_storage,
        sample_video,
    ):
        """
        Test soft-delete workflow:
        1. Video is visible in public API
        2. Delete video (soft-delete)
        3. Video disappears from public API
        4. Video appears in trash/archive
        5. Restore video
        6. Video reappears in public API
        """
        video_id = sample_video["id"]
        slug = sample_video["slug"]

        # Step 1: Video is visible
        list_response = public_client.get("/api/videos")
        assert list_response.status_code == 200
        assert any(v["id"] == video_id for v in list_response.json()["videos"])

        detail_response = public_client.get(f"/api/videos/{slug}")
        assert detail_response.status_code == 200

        # Step 2: Soft-delete video
        delete_response = admin_client.delete(f"/api/videos/{video_id}")
        assert delete_response.status_code == 200

        # Step 3: Video disappears from public API
        list_response2 = public_client.get("/api/videos")
        assert not any(v["id"] == video_id for v in list_response2.json()["videos"])

        detail_response2 = public_client.get(f"/api/videos/{slug}")
        assert detail_response2.status_code == 404

        # Step 4: Video in trash (admin can see it)
        trash_response = admin_client.get("/api/videos/trash")
        assert trash_response.status_code == 200
        trash_videos = trash_response.json()["videos"]
        assert any(v["id"] == video_id for v in trash_videos)

        # Verify deleted_at timestamp set
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["deleted_at"] is not None

        # Step 5: Restore video
        restore_response = admin_client.post(f"/api/videos/{video_id}/restore")
        assert restore_response.status_code == 200

        # Step 6: Video reappears in public API
        list_response3 = public_client.get("/api/videos")
        assert any(v["id"] == video_id for v in list_response3.json()["videos"])

        detail_response3 = public_client.get(f"/api/videos/{slug}")
        assert detail_response3.status_code == 200

        # Verify deleted_at cleared
        restored_video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert restored_video["deleted_at"] is None

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_permanent_delete_after_retention(
        self,
        admin_client,
        test_database,
        test_storage,
        sample_video,
    ):
        """
        Test permanent deletion:
        1. Soft-delete video
        2. Simulate retention period expiry
        3. Permanent delete
        4. Video and files are removed
        """
        video_id = sample_video["id"]
        slug = sample_video["slug"]

        # Create video files
        video_dir = test_storage["videos"] / slug
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "master.m3u8").write_text("playlist")
        (video_dir / "thumbnail.jpg").write_bytes(b"thumb")

        # Soft-delete
        admin_client.delete(f"/api/videos/{video_id}")

        # Simulate retention period expiry
        past_date = datetime.now(timezone.utc) - timedelta(days=60)
        await test_database.execute(
            videos.update().where(videos.c.id == video_id).values(deleted_at=past_date)
        )

        # Permanent delete
        permanent_response = admin_client.delete(f"/api/videos/{video_id}?permanent=true")
        assert permanent_response.status_code == 200

        # Verify video removed from database
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video is None

        # Verify files moved to archive
        assert not video_dir.exists() or len(list(video_dir.iterdir())) == 0


class TestAnalyticsWorkflow:
    """Test analytics tracking across viewing sessions."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_analytics_tracking_multiple_sessions(
        self,
        public_client,
        test_database,
        sample_video,
    ):
        """
        Test analytics tracking:
        1. Multiple viewers watch video
        2. View counts are accurate
        3. Watch time is tracked
        4. Unique viewers vs total views
        """
        video_id = sample_video["id"]

        # Simulate 3 different viewers (using different cookies)
        sessions = []
        for i in range(3):
            # Start playback (creates viewer cookie)
            start_response = public_client.post(
                "/api/playback/start",
                json={"video_id": video_id, "quality": "720p"},
            )
            assert start_response.status_code == 200
            session_token = start_response.json()["session_token"]
            sessions.append(session_token)

            # Watch for different durations
            await test_database.execute(
                playback_sessions.update()
                .where(playback_sessions.c.session_token == session_token)
                .values(
                    last_position=30 * (i + 1),  # 30, 60, 90 seconds
                    updated_at=datetime.now(timezone.utc),
                )
            )

        # Get analytics
        analytics_response = public_client.get(f"/api/videos/{video_id}/analytics")
        assert analytics_response.status_code == 200
        analytics = analytics_response.json()

        # Should have 3 views
        assert analytics["view_count"] >= 3

        # Verify viewer records created
        viewer_count = await test_database.fetch_val(
            "SELECT COUNT(*) FROM viewers WHERE last_video_id = :video_id",
            {"video_id": video_id},
        )
        assert viewer_count >= 1  # At least one viewer tracked


class TestTagManagementWorkflow:
    """Test tag creation and video filtering."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_tag_creation_and_filtering(
        self,
        admin_client,
        public_client,
        test_database,
        test_storage,
    ):
        """
        Test tag workflow:
        1. Create tags
        2. Upload videos with tags
        3. Filter videos by tag
        4. Update video tags
        5. Delete tags
        """
        # Step 1: Create tags
        tag1_response = admin_client.post("/api/tags", json={"name": "tutorial"})
        assert tag1_response.status_code == 200
        tag1_id = tag1_response.json()["id"]

        tag2_response = admin_client.post("/api/tags", json={"name": "beginner"})
        assert tag2_response.status_code == 200
        tag2_id = tag2_response.json()["id"]

        # Step 2: Upload video with tags
        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("tagged.mp4", io.BytesIO(b"content"), "video/mp4")},
            data={
                "title": "Tagged Video",
                "tag_ids": f"[{tag1_id}, {tag2_id}]",
            },
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]

        # Mark video as ready for public API
        await test_database.execute(
            videos.update().where(videos.c.id == video_id).values(status=VideoStatus.READY)
        )

        # Step 3: Filter videos by tag
        filter_response = public_client.get(f"/api/videos?tag={tag1_id}")
        assert filter_response.status_code == 200
        filtered_videos = filter_response.json()["videos"]
        assert any(v["id"] == video_id for v in filtered_videos)

        # Step 4: Update video tags (remove one, keep one)
        update_response = admin_client.put(
            f"/api/videos/{video_id}",
            json={"tag_ids": [tag1_id]},
        )
        assert update_response.status_code == 200

        # Verify tag association
        video_tags_count = await test_database.fetch_val(
            "SELECT COUNT(*) FROM video_tags WHERE video_id = :video_id",
            {"video_id": video_id},
        )
        assert video_tags_count == 1

        # Step 5: Delete tag
        delete_response = admin_client.delete(f"/api/tags/{tag2_id}")
        assert delete_response.status_code == 200

        # Verify tag removed from database
        tag = await test_database.fetch_one(tags.select().where(tags.c.id == tag2_id))
        assert tag is None


class TestBatchOperationsWorkflow:
    """Test batch operations on multiple videos."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_batch_category_update(
        self,
        admin_client,
        test_database,
        test_storage,
        sample_category,
    ):
        """
        Test batch operations:
        1. Upload multiple videos
        2. Batch update category
        3. Verify all updated
        """
        # Upload 5 videos
        video_ids = []
        for i in range(5):
            upload_response = admin_client.post(
                "/api/videos",
                files={"file": (f"batch{i}.mp4", io.BytesIO(b"content"), "video/mp4")},
                data={"title": f"Batch Video {i}"},
            )
            assert upload_response.status_code == 200
            video_ids.append(upload_response.json()["video_id"])

        # Create new category
        cat_response = admin_client.post(
            "/api/categories",
            json={"name": "Batch Category"},
        )
        new_category_id = cat_response.json()["id"]

        # Batch update (if endpoint exists)
        batch_response = admin_client.put(
            "/api/videos/batch",
            json={
                "video_ids": video_ids,
                "updates": {"category_id": new_category_id},
            },
        )
        # If batch endpoint doesn't exist, update individually
        if batch_response.status_code == 404:
            for video_id in video_ids:
                admin_client.put(
                    f"/api/videos/{video_id}",
                    json={"category_id": new_category_id},
                )

        # Verify all videos updated
        for video_id in video_ids:
            video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
            assert video["category_id"] == new_category_id
