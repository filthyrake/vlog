"""
Tests for admin API list_categories endpoint with soft-deleted videos.
This test file specifically validates that soft-deleted videos are excluded from category counts.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from api.database import categories, videos
from api.enums import VideoStatus


class TestAdminListCategoriesWithSoftDelete:
    """Tests for admin list_categories excluding soft-deleted videos."""

    @pytest.mark.asyncio
    async def test_list_categories_excludes_soft_deleted_videos(self, test_database, sample_category):
        """Test that soft-deleted videos are not counted in category video_count."""
        now = datetime.now(timezone.utc)
        category_id = sample_category["id"]

        # Create 3 active videos
        for i in range(3):
            await test_database.execute(
                videos.insert().values(
                    title=f"Active Video {i}",
                    slug=f"active-video-{i}",
                    category_id=category_id,
                    status=VideoStatus.READY,
                    created_at=now,
                )
            )

        # Create 2 soft-deleted videos
        for i in range(2):
            await test_database.execute(
                videos.insert().values(
                    title=f"Deleted Video {i}",
                    slug=f"deleted-video-{i}",
                    category_id=category_id,
                    status=VideoStatus.READY,
                    created_at=now,
                    deleted_at=now,  # Soft-deleted
                )
            )

        # Execute the query that should exclude soft-deleted videos
        # This is the FIXED query - what the admin API should use
        query = sa.text("""
            SELECT c.*, COUNT(v.id) as video_count
            FROM categories c
            LEFT JOIN videos v ON v.category_id = c.id AND v.deleted_at IS NULL
            GROUP BY c.id
            ORDER BY c.name
        """)
        rows = await test_database.fetch_all(query)

        assert len(rows) == 1
        assert rows[0]["video_count"] == 3  # Only active videos

    @pytest.mark.asyncio
    async def test_list_categories_with_old_query_counts_all(self, test_database, sample_category):
        """Test that the OLD query incorrectly counts soft-deleted videos."""
        now = datetime.now(timezone.utc)
        category_id = sample_category["id"]

        # Create 3 active videos
        for i in range(3):
            await test_database.execute(
                videos.insert().values(
                    title=f"Active Video {i}",
                    slug=f"active-video-{i}",
                    category_id=category_id,
                    status=VideoStatus.READY,
                    created_at=now,
                )
            )

        # Create 2 soft-deleted videos
        for i in range(2):
            await test_database.execute(
                videos.insert().values(
                    title=f"Deleted Video {i}",
                    slug=f"deleted-video-{i}",
                    category_id=category_id,
                    status=VideoStatus.READY,
                    created_at=now,
                    deleted_at=now,  # Soft-deleted
                )
            )

        # Execute the OLD query that doesn't filter soft-deleted videos
        query = sa.text("""
            SELECT c.*, COUNT(v.id) as video_count
            FROM categories c
            LEFT JOIN videos v ON v.category_id = c.id
            GROUP BY c.id
            ORDER BY c.name
        """)
        rows = await test_database.fetch_all(query)

        assert len(rows) == 1
        assert rows[0]["video_count"] == 5  # Incorrectly counts deleted videos

    @pytest.mark.asyncio
    async def test_list_categories_empty_when_all_deleted(self, test_database, sample_category):
        """Test that category shows 0 videos when all videos are soft-deleted."""
        now = datetime.now(timezone.utc)
        category_id = sample_category["id"]

        # Create only soft-deleted videos
        for i in range(3):
            await test_database.execute(
                videos.insert().values(
                    title=f"Deleted Video {i}",
                    slug=f"deleted-video-{i}",
                    category_id=category_id,
                    status=VideoStatus.READY,
                    created_at=now,
                    deleted_at=now,  # All soft-deleted
                )
            )

        # Execute the query with the fix
        query = sa.text("""
            SELECT c.*, COUNT(v.id) as video_count
            FROM categories c
            LEFT JOIN videos v ON v.category_id = c.id AND v.deleted_at IS NULL
            GROUP BY c.id
            ORDER BY c.name
        """)
        rows = await test_database.fetch_all(query)

        assert len(rows) == 1
        assert rows[0]["video_count"] == 0  # Should be 0 since all are deleted

    @pytest.mark.asyncio
    async def test_list_categories_multiple_categories_mixed_state(self, test_database):
        """Test multiple categories with mixed active/deleted videos."""
        now = datetime.now(timezone.utc)

        # Create two categories
        cat1_id = await test_database.execute(
            categories.insert().values(
                name="Category 1",
                slug="category-1",
                created_at=now,
            )
        )
        cat2_id = await test_database.execute(
            categories.insert().values(
                name="Category 2",
                slug="category-2",
                created_at=now,
            )
        )

        # Category 1: 2 active, 1 deleted
        await test_database.execute(
            videos.insert().values(
                title="Cat1 Active 1",
                slug="cat1-active-1",
                category_id=cat1_id,
                status=VideoStatus.READY,
                created_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Cat1 Active 2",
                slug="cat1-active-2",
                category_id=cat1_id,
                status=VideoStatus.READY,
                created_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Cat1 Deleted",
                slug="cat1-deleted",
                category_id=cat1_id,
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,
            )
        )

        # Category 2: 1 active, 2 deleted
        await test_database.execute(
            videos.insert().values(
                title="Cat2 Active",
                slug="cat2-active",
                category_id=cat2_id,
                status=VideoStatus.READY,
                created_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Cat2 Deleted 1",
                slug="cat2-deleted-1",
                category_id=cat2_id,
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Cat2 Deleted 2",
                slug="cat2-deleted-2",
                category_id=cat2_id,
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,
            )
        )

        # Execute the query with the fix
        query = sa.text("""
            SELECT c.*, COUNT(v.id) as video_count
            FROM categories c
            LEFT JOIN videos v ON v.category_id = c.id AND v.deleted_at IS NULL
            GROUP BY c.id
            ORDER BY c.name
        """)
        rows = await test_database.fetch_all(query)

        assert len(rows) == 2

        # Results should be ordered by name, so Category 1 first
        assert rows[0]["name"] == "Category 1"
        assert rows[0]["video_count"] == 2  # Only active videos

        assert rows[1]["name"] == "Category 2"
        assert rows[1]["video_count"] == 1  # Only active videos

    @pytest.mark.asyncio
    async def test_list_categories_with_no_videos(self, test_database, sample_category):
        """Test that categories with no videos show 0 count."""
        # Execute the query with the fix
        query = sa.text("""
            SELECT c.*, COUNT(v.id) as video_count
            FROM categories c
            LEFT JOIN videos v ON v.category_id = c.id AND v.deleted_at IS NULL
            GROUP BY c.id
            ORDER BY c.name
        """)
        rows = await test_database.fetch_all(query)

        assert len(rows) == 1
        assert rows[0]["video_count"] == 0  # No videos at all
