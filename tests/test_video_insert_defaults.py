"""Uploads must obey the NOT NULL aggregate columns from migration 031."""
import pytest

from api.database import videos


@pytest.mark.asyncio
async def test_async_insert_uses_database_engagement_defaults(test_database):
    video_id = await test_database.execute(
        videos.insert().values(title="Default counter regression", slug="default-counter-regression")
    )
    row = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
    assert row["comment_count"] == 0
    assert row["rating_count"] == 0
    assert row["likes_count"] == 0
    assert row["dislikes_count"] == 0
    assert row["rating_distribution"] == "{}"
