"""
End-to-end workflow integration tests.

Tests complete workflows that span multiple components:
- Upload → Transcode → Ready → Playback
- Category CRUD with video counts
- Soft-delete → Restore → Permanent delete
- Tag management and filtering

NOTE: These tests use only the admin API client to avoid asyncpg connection
conflicts that occur when using multiple FastAPI TestClient instances.
Public API verification is done through admin client where possible.

NOTE: Video status cannot be changed via the API - it's managed by the
transcoding workflow. These tests verify upload and metadata updates work,
but status transitions require the actual transcoding worker.
"""

import io

import pytest


class TestUploadTranscodePlaybackWorkflow:
    """Test the complete video lifecycle from upload to playback."""

    @pytest.mark.e2e
    def test_complete_video_lifecycle(
        self,
        admin_client,
        test_storage,
    ):
        """
        Test complete workflow:
        1. Create category via API
        2. Upload video via admin API
        3. Simulate transcoding completion (create HLS files)
        4. Verify video is accessible and in pending state
           (status changes require actual transcoding worker)
        """
        # Step 1: Create category via API
        cat_response = admin_client.post(
            "/api/categories",
            json={"name": "Workflow Category", "description": "For workflow testing"},
        )
        assert cat_response.status_code == 200
        category_id = cat_response.json()["id"]

        # Step 2: Upload video
        file_content = b"test video content for workflow"
        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("workflow.mp4", io.BytesIO(file_content), "video/mp4")},
            data={
                "title": "Workflow Test Video",
                "description": "Testing complete workflow",
                "category_id": category_id,
            },
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]
        slug = upload_response.json()["slug"]

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

        # Step 4: Verify video is accessible via admin API
        # Note: Status remains "pending" because we can't change it via API
        # (that's done by the transcoding worker)
        detail_response = admin_client.get(f"/api/videos/{video_id}")
        assert detail_response.status_code == 200
        video_detail = detail_response.json()
        assert video_detail["title"] == "Workflow Test Video"
        assert video_detail["status"] == "pending"  # Cannot change via API
        assert video_detail["category_id"] == category_id


class TestCategoryCRUDWorkflow:
    """Test category management with video counts."""

    @pytest.mark.e2e
    def test_category_with_video_counts(
        self,
        admin_client,
        test_storage,
    ):
        """
        Test category operations:
        1. Create category
        2. Upload videos to category
        3. Verify videos are associated
        4. Create another category and move videos
        5. Verify videos moved
        """
        # Step 1: Create category
        create_response = admin_client.post(
            "/api/categories",
            json={"name": "Test Category", "description": "For testing"},
        )
        assert create_response.status_code == 200
        category_id = create_response.json()["id"]

        # Step 2: Upload videos to category
        video_ids = []
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
            video_ids.append(upload_response.json()["video_id"])

        # Step 3: Verify videos are in category
        for video_id in video_ids:
            detail_response = admin_client.get(f"/api/videos/{video_id}")
            assert detail_response.status_code == 200
            assert detail_response.json()["category_id"] == category_id

        # Step 4: Create another category and move videos
        create_response2 = admin_client.post(
            "/api/categories",
            json={"name": "New Category"},
        )
        assert create_response2.status_code == 200
        new_category_id = create_response2.json()["id"]

        # Move videos to new category using Form data (not JSON)
        for video_id in video_ids:
            update_response = admin_client.put(
                f"/api/videos/{video_id}",
                data={"category_id": new_category_id},  # Form data, not JSON
            )
            assert update_response.status_code == 200

        # Step 5: Verify videos moved
        for video_id in video_ids:
            detail_response = admin_client.get(f"/api/videos/{video_id}")
            assert detail_response.status_code == 200
            assert detail_response.json()["category_id"] == new_category_id


class TestSoftDeleteRestoreWorkflow:
    """Test soft-delete and restore functionality."""

    @pytest.mark.e2e
    def test_soft_delete_and_restore(
        self,
        admin_client,
        test_storage,
    ):
        """
        Test soft-delete workflow:
        1. Create and upload video
        2. Delete video (soft-delete)
        3. Video appears in archived list
        4. Restore video
        5. Video is accessible again
        """
        # Step 1: Create category and upload video
        cat_response = admin_client.post(
            "/api/categories",
            json={"name": "Delete Test Category"},
        )
        assert cat_response.status_code == 200
        category_id = cat_response.json()["id"]

        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("delete_test.mp4", io.BytesIO(b"content"), "video/mp4")},
            data={
                "title": "Delete Test Video",
                "category_id": category_id,
            },
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]
        slug = upload_response.json()["slug"]

        # Create video files for playback
        video_dir = test_storage["videos"] / slug
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "master.m3u8").write_text("#EXTM3U\n")
        (video_dir / "thumbnail.jpg").write_bytes(b"thumb")

        # Step 2: Soft-delete video
        delete_response = admin_client.delete(f"/api/videos/{video_id}")
        assert delete_response.status_code == 200

        # Step 3: Video in archived list (not trash)
        archived_response = admin_client.get("/api/videos/archived")
        assert archived_response.status_code == 200
        archived_videos = archived_response.json()["videos"]
        assert any(v["id"] == video_id for v in archived_videos)

        # Step 4: Restore video
        restore_response = admin_client.post(f"/api/videos/{video_id}/restore")
        assert restore_response.status_code == 200

        # Step 5: Video is accessible again
        detail_response = admin_client.get(f"/api/videos/{video_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["title"] == "Delete Test Video"


class TestTagManagementWorkflow:
    """Test tag creation and video filtering."""

    @pytest.mark.e2e
    def test_tag_creation_and_filtering(
        self,
        admin_client,
        test_storage,
    ):
        """
        Test tag workflow:
        1. Create tags
        2. Upload video
        3. Set tags on video via dedicated endpoint
        4. Verify tags associated
        5. Update video tags
        6. Delete unused tag
        """
        # Step 1: Create tags
        tag1_response = admin_client.post("/api/tags", json={"name": "tutorial"})
        assert tag1_response.status_code == 200
        tag1_id = tag1_response.json()["id"]

        tag2_response = admin_client.post("/api/tags", json={"name": "beginner"})
        assert tag2_response.status_code == 200
        tag2_id = tag2_response.json()["id"]

        # Step 2: Upload video (tags are set separately, not during upload)
        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("tagged.mp4", io.BytesIO(b"content"), "video/mp4")},
            data={
                "title": "Tagged Video",
            },
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]
        slug = upload_response.json()["slug"]

        # Create video files
        video_dir = test_storage["videos"] / slug
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "master.m3u8").write_text("#EXTM3U\n")
        (video_dir / "thumbnail.jpg").write_bytes(b"thumb")

        # Step 3: Set tags on video via dedicated endpoint
        tags_response = admin_client.put(
            f"/api/videos/{video_id}/tags",
            json={"tag_ids": [tag1_id, tag2_id]},
        )
        assert tags_response.status_code == 200

        # Step 4: Verify tags are associated (use dedicated tags endpoint)
        tags_check_response = admin_client.get(f"/api/videos/{video_id}/tags")
        assert tags_check_response.status_code == 200
        video_tags = tags_check_response.json()
        tag_ids = [t["id"] for t in video_tags]
        assert tag1_id in tag_ids
        assert tag2_id in tag_ids

        # Step 5: Update video tags (remove one, keep one)
        update_tags_response = admin_client.put(
            f"/api/videos/{video_id}/tags",
            json={"tag_ids": [tag1_id]},
        )
        assert update_tags_response.status_code == 200

        # Verify only one tag remains (use dedicated tags endpoint)
        tags_check_response2 = admin_client.get(f"/api/videos/{video_id}/tags")
        assert tags_check_response2.status_code == 200
        video_tags2 = tags_check_response2.json()
        tag_ids2 = [t["id"] for t in video_tags2]
        assert tag1_id in tag_ids2
        assert tag2_id not in tag_ids2

        # Step 6: Delete unused tag
        delete_response = admin_client.delete(f"/api/tags/{tag2_id}")
        assert delete_response.status_code == 200

        # Verify tag no longer exists
        tags_response = admin_client.get("/api/tags")
        assert tags_response.status_code == 200
        tag_list = tags_response.json()
        # Handle both list and dict response formats
        if isinstance(tag_list, dict):
            tag_list = tag_list.get("tags", [])
        assert not any(t["id"] == tag2_id for t in tag_list)


class TestBatchOperationsWorkflow:
    """Test batch operations on multiple videos."""

    @pytest.mark.e2e
    def test_batch_category_update(
        self,
        admin_client,
        test_storage,
    ):
        """
        Test batch operations:
        1. Create category
        2. Upload multiple videos
        3. Create new category
        4. Batch update category using bulk endpoint
        5. Verify all updated via API
        """
        # Step 1: Create initial category
        cat_response = admin_client.post(
            "/api/categories",
            json={"name": "Initial Category"},
        )
        assert cat_response.status_code == 200
        initial_category_id = cat_response.json()["id"]

        # Step 2: Upload 5 videos
        video_ids = []
        for i in range(5):
            upload_response = admin_client.post(
                "/api/videos",
                files={"file": (f"batch{i}.mp4", io.BytesIO(b"content"), "video/mp4")},
                data={
                    "title": f"Batch Video {i}",
                    "category_id": initial_category_id,
                },
            )
            assert upload_response.status_code == 200
            video_ids.append(upload_response.json()["video_id"])

        # Step 3: Create new category
        cat_response2 = admin_client.post(
            "/api/categories",
            json={"name": "Batch Category"},
        )
        assert cat_response2.status_code == 200
        new_category_id = cat_response2.json()["id"]

        # Step 4: Batch update using bulk endpoint (JSON)
        batch_response = admin_client.post(
            "/api/videos/bulk/update",
            json={
                "video_ids": video_ids,
                "category_id": new_category_id,
            },
        )
        assert batch_response.status_code == 200
        result = batch_response.json()
        assert result["updated"] == 5
        assert result["failed"] == 0

        # Step 5: Verify all videos updated via API
        for video_id in video_ids:
            detail_response = admin_client.get(f"/api/videos/{video_id}")
            assert detail_response.status_code == 200
            video_detail = detail_response.json()
            assert video_detail["category_id"] == new_category_id
