"""
Tests for the tags feature.

Tests tag CRUD operations, video-tag associations, and filtering videos by tags.
"""

import pytest

# ============================================================================
# Admin API Tests - Tag Management
# ============================================================================


class TestTagManagementHTTP:
    """HTTP-level tests for tag management endpoints."""

    def test_list_tags_empty(self, admin_client):
        """Test listing tags when empty."""
        response = admin_client.get("/api/tags")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_tags_with_data(self, admin_client, sample_tag):
        """Test listing tags with data."""
        response = admin_client.get("/api/tags")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(t["slug"] == "test-tag" for t in data)

    def test_create_tag(self, admin_client):
        """Test creating a new tag."""
        response = admin_client.post(
            "/api/tags",
            json={"name": "New Tag"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Tag"
        assert data["slug"] == "new-tag"
        assert data["video_count"] == 0

    @pytest.mark.asyncio
    async def test_create_tag_duplicate_fails(self, admin_client, sample_tag):
        """Test creating tag with duplicate name fails."""
        response = admin_client.post(
            "/api/tags",
            json={"name": "Test Tag"},
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_create_tag_empty_name_fails(self, admin_client):
        """Test creating tag with empty name fails."""
        response = admin_client.post(
            "/api/tags",
            json={"name": ""},
        )
        assert response.status_code == 422  # Pydantic validation error

    def test_create_tag_name_too_long_fails(self, admin_client):
        """Test creating tag with name too long fails."""
        response = admin_client.post(
            "/api/tags",
            json={"name": "x" * 60},
        )
        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_update_tag(self, admin_client, sample_tag):
        """Test updating a tag name."""
        response = admin_client.put(
            f"/api/tags/{sample_tag['id']}",
            json={"name": "Updated Tag"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Tag"
        assert data["slug"] == "updated-tag"

    def test_update_tag_not_found(self, admin_client):
        """Test updating non-existent tag returns 404."""
        response = admin_client.put(
            "/api/tags/99999",
            json={"name": "Updated Tag"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_tag(self, admin_client, sample_tag):
        """Test deleting a tag."""
        response = admin_client.delete(f"/api/tags/{sample_tag['id']}")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify tag is deleted
        response = admin_client.get("/api/tags")
        assert not any(t["slug"] == "test-tag" for t in response.json())

    def test_delete_tag_not_found(self, admin_client):
        """Test deleting non-existent tag returns 404."""
        response = admin_client.delete("/api/tags/99999")
        assert response.status_code == 404


# ============================================================================
# Admin API Tests - Video Tag Management
# ============================================================================


class TestVideoTagManagementHTTP:
    """HTTP-level tests for video tag management endpoints."""

    @pytest.mark.asyncio
    async def test_get_video_tags_empty(self, admin_client, sample_video):
        """Test getting tags for a video with no tags."""
        response = admin_client.get(f"/api/videos/{sample_video['id']}/tags")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_get_video_tags_with_data(self, admin_client, sample_video_with_tag):
        """Test getting tags for a video with tags."""
        response = admin_client.get(f"/api/videos/{sample_video_with_tag['id']}/tags")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "test-tag"

    def test_get_video_tags_video_not_found(self, admin_client):
        """Test getting tags for non-existent video returns 404."""
        response = admin_client.get("/api/videos/99999/tags")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_set_video_tags(self, admin_client, sample_video, sample_tag):
        """Test setting tags on a video."""
        response = admin_client.put(
            f"/api/videos/{sample_video['id']}/tags",
            json={"tag_ids": [sample_tag["id"]]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_tag["id"]

    @pytest.mark.asyncio
    async def test_set_video_tags_replaces_existing(self, admin_client, sample_video_with_tag):
        """Test setting tags replaces existing tags."""
        # Create a new tag
        create_response = admin_client.post("/api/tags", json={"name": "New Tag"})
        new_tag_id = create_response.json()["id"]

        # Set only the new tag
        response = admin_client.put(
            f"/api/videos/{sample_video_with_tag['id']}/tags",
            json={"tag_ids": [new_tag_id]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "new-tag"

    @pytest.mark.asyncio
    async def test_set_video_tags_empty_removes_all(self, admin_client, sample_video_with_tag):
        """Test setting empty tags removes all tags."""
        response = admin_client.put(
            f"/api/videos/{sample_video_with_tag['id']}/tags",
            json={"tag_ids": []},
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_set_video_tags_invalid_tag_id_fails(self, admin_client, sample_video):
        """Test setting invalid tag IDs fails."""
        response = admin_client.put(
            f"/api/videos/{sample_video['id']}/tags",
            json={"tag_ids": [99999]},
        )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    def test_set_video_tags_video_not_found(self, admin_client):
        """Test setting tags on non-existent video returns 404."""
        response = admin_client.put(
            "/api/videos/99999/tags",
            json={"tag_ids": []},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_set_video_tags_exceeds_limit(self, admin_client, sample_video):
        """Test that setting more than 20 tags fails validation."""
        # Create 21 tags
        tag_ids = []
        for i in range(21):
            response = admin_client.post("/api/tags", json={"name": f"Limit Tag {i}"})
            assert response.status_code == 200
            tag_ids.append(response.json()["id"])

        # Try to set all 21 tags on video - should fail validation
        response = admin_client.put(
            f"/api/videos/{sample_video['id']}/tags",
            json={"tag_ids": tag_ids},
        )
        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_remove_video_tag(self, admin_client, sample_video_with_tag, sample_tag):
        """Test removing a single tag from a video."""
        response = admin_client.delete(f"/api/videos/{sample_video_with_tag['id']}/tags/{sample_tag['id']}")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify tag is removed
        response = admin_client.get(f"/api/videos/{sample_video_with_tag['id']}/tags")
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_remove_video_tag_not_found(self, admin_client, sample_video):
        """Test removing non-existent tag from video succeeds (idempotent)."""
        response = admin_client.delete(f"/api/videos/{sample_video['id']}/tags/99999")
        # Returns 404 for non-existent tag
        assert response.status_code == 404


# ============================================================================
# Public API Tests - Tag Browsing
# ============================================================================


class TestPublicTagsHTTP:
    """HTTP-level tests for public tag browsing endpoints."""

    def test_list_public_tags_empty(self, public_client):
        """Test listing tags when empty."""
        response = public_client.get("/api/tags")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_public_tags_with_data(self, public_client, sample_tag):
        """Test listing tags with data."""
        response = public_client.get("/api/tags")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(t["slug"] == "test-tag" for t in data)

    @pytest.mark.asyncio
    async def test_get_public_tag(self, public_client, sample_tag):
        """Test getting a single tag by slug."""
        response = public_client.get("/api/tags/test-tag")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Tag"
        assert data["slug"] == "test-tag"

    def test_get_public_tag_not_found(self, public_client):
        """Test getting non-existent tag returns 404."""
        response = public_client.get("/api/tags/nonexistent-tag")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_public_tag_video_count(self, public_client, sample_video_with_tag):
        """Test tag video count includes only ready, non-deleted videos."""
        response = public_client.get("/api/tags/test-tag")
        assert response.status_code == 200
        data = response.json()
        # sample_video_with_tag has status=READY and is not deleted
        assert data["video_count"] == 1


# ============================================================================
# Public API Tests - Video Tag Filtering
# ============================================================================


class TestVideoTagFilteringHTTP:
    """HTTP-level tests for filtering videos by tags."""

    @pytest.mark.asyncio
    async def test_filter_videos_by_tag(self, public_client, sample_video_with_tag):
        """Test filtering videos by tag."""
        response = public_client.get("/api/videos", params={"tag": "test-tag"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(v["slug"] == "test-video" for v in data)

    @pytest.mark.asyncio
    async def test_filter_videos_by_nonexistent_tag(self, public_client, sample_video):
        """Test filtering by non-existent tag returns empty list."""
        response = public_client.get("/api/videos", params={"tag": "nonexistent-tag"})
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_video_response_includes_tags(self, public_client, sample_video_with_tag):
        """Test video response includes tags."""
        response = public_client.get(f"/api/videos/{sample_video_with_tag['slug']}")
        assert response.status_code == 200
        data = response.json()
        assert "tags" in data
        assert len(data["tags"]) == 1
        assert data["tags"][0]["slug"] == "test-tag"

    @pytest.mark.asyncio
    async def test_video_list_includes_tags(self, public_client, sample_video_with_tag):
        """Test video list includes tags."""
        response = public_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        video = next((v for v in data if v["slug"] == "test-video"), None)
        assert video is not None
        assert "tags" in video
        assert len(video["tags"]) == 1
        assert video["tags"][0]["slug"] == "test-tag"

    @pytest.mark.asyncio
    async def test_video_without_tags(self, public_client, sample_video):
        """Test video without tags returns empty tags list."""
        response = public_client.get(f"/api/videos/{sample_video['slug']}")
        assert response.status_code == 200
        data = response.json()
        assert "tags" in data
        assert data["tags"] == []
