"""Exercise conditional stream updates against PostgreSQL's RETURNING semantics."""
from datetime import datetime, timedelta, timezone

import pytest

from api import database as database_module
from api import live_auth, live_tasks
from api.database import live_streams


@pytest.mark.asyncio
async def test_revocation_reports_only_changed_stream(test_database, monkeypatch):
    monkeypatch.setattr(live_auth, 'database', test_database)
    stream_id = await test_database.execute(live_streams.insert().values(
        title='Test', slug='revoke-test', stream_key_hash='unused', stream_key_prefix='unused', status='live',
    ))
    assert await live_auth.revoke_stream_key(stream_id) is True
    assert await live_auth.revoke_stream_key(stream_id) is False
    assert await live_auth.revoke_stream_key(-1) is False


@pytest.mark.asyncio
async def test_stale_transitions_count_only_updated_rows(test_database, monkeypatch):
    monkeypatch.setattr(database_module, 'database', test_database)
    now = datetime.now(timezone.utc)
    stream_id = await test_database.execute(live_streams.insert().values(
        title='Test', slug='stale-test', stream_key_hash='unused', stream_key_prefix='unused',
        status='live', auto_record_vod=False,
        last_segment_at=now - timedelta(seconds=live_tasks.LIVE_STALE_THRESHOLD + 1),
    ))
    assert await live_tasks.detect_stale_streams() == 1
    assert await live_tasks.detect_stale_streams() == 0
    assert await test_database.fetch_val(live_streams.select().with_only_columns(live_streams.c.status)) == 'ending'
    await test_database.execute(live_streams.update().where(live_streams.c.id == stream_id).values(
        last_segment_at=now - timedelta(seconds=live_tasks.LIVE_STALE_THRESHOLD * live_tasks.LIVE_STALE_GRACE_MULTIPLIER + 1),
    ))
    assert await live_tasks.detect_stale_streams() == 1
    assert await live_tasks.detect_stale_streams() == 0
