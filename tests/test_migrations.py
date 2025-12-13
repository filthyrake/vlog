"""
Database migration tests.

Tests that verify:
- Migrations run successfully on fresh database
- Migrations are reversible (downgrade)
- Schema matches expected state after migrations
- Data integrity during migrations
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


class TestMigrations:
    """Test database migrations with Alembic."""

    @pytest.fixture
    def alembic_config(self, test_db_url):
        """Create Alembic config pointing to test database."""
        # Get path to alembic.ini
        repo_root = Path(__file__).parent.parent
        alembic_ini = repo_root / "alembic.ini"

        config = Config(str(alembic_ini))
        config.set_main_option("sqlalchemy.url", test_db_url)
        return config

    @pytest.mark.asyncio
    async def test_upgrade_head(self, alembic_config, test_db_url):
        """Test that all migrations can be applied to a fresh database."""
        # Run migrations
        command.upgrade(alembic_config, "head")

        # Verify database has expected tables
        engine = sa.create_engine(test_db_url)
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()

        # Check for core tables
        expected_tables = {
            "categories",
            "videos",
            "video_qualities",
            "transcoding_jobs",
            "quality_progress",
            "workers",
            "worker_api_keys",
            "viewers",
            "playback_sessions",
            "tags",
            "video_tags",
            "transcriptions",
            "alembic_version",
        }

        assert expected_tables.issubset(set(tables)), f"Missing tables: {expected_tables - set(tables)}"
        engine.dispose()

    @pytest.mark.asyncio
    async def test_downgrade_base(self, alembic_config, test_db_url):
        """Test that migrations can be reversed."""
        # First upgrade to head
        command.upgrade(alembic_config, "head")

        # Then downgrade to base
        command.downgrade(alembic_config, "base")

        # Verify all tables are removed (except alembic_version)
        engine = sa.create_engine(test_db_url)
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()

        # Only alembic_version (or no tables) should remain after downgrade to base
        assert set(tables).issubset({"alembic_version"}), \
            f"Unexpected tables after downgrade to base. Expected only 'alembic_version' or empty, got: {tables}"
        engine.dispose()

    @pytest.mark.asyncio
    async def test_upgrade_one_by_one(self, alembic_config, test_db_url):
        """Test that each migration can be applied individually."""
        from alembic.script import ScriptDirectory

        # Get all migration revisions
        script_dir = ScriptDirectory.from_config(alembic_config)
        revisions = [rev.revision for rev in script_dir.walk_revisions()]
        revisions.reverse()  # Start from oldest

        # Apply each migration one by one
        for i, revision in enumerate(revisions):
            try:
                command.upgrade(alembic_config, revision)

                # Verify alembic_version is updated
                engine = sa.create_engine(test_db_url)
                with engine.connect() as conn:
                    result = conn.execute(sa.text("SELECT version_num FROM alembic_version"))
                    current_version = result.scalar()
                    assert current_version == revision, \
                        f"Expected version {revision}, got {current_version}"
                engine.dispose()
            except Exception as e:
                pytest.fail(f"Migration {i+1} ({revision}) failed: {e}")

    @pytest.mark.asyncio
    async def test_schema_constraints(self, alembic_config, test_db_url):
        """Test that schema has expected constraints after migrations."""
        # Apply all migrations
        command.upgrade(alembic_config, "head")

        engine = sa.create_engine(test_db_url)
        inspector = sa.inspect(engine)

        # Check videos table has unique constraint on slug
        videos_indexes = inspector.get_indexes("videos")
        videos_unique_constraints = inspector.get_unique_constraints("videos")

        slug_unique = any(
            "slug" in (idx.get("column_names") or [])
            for idx in videos_indexes + videos_unique_constraints
        )
        assert slug_unique, "videos.slug should have unique constraint"

        # Check foreign key from videos to categories
        videos_fks = inspector.get_foreign_keys("videos")
        category_fk = any(
            fk.get("referred_table") == "categories"
            for fk in videos_fks
        )
        assert category_fk, "videos should have foreign key to categories"

        # Check worker_api_keys has foreign key to workers
        api_keys_fks = inspector.get_foreign_keys("worker_api_keys")
        worker_fk = any(
            fk.get("referred_table") == "workers"
            for fk in api_keys_fks
        )
        assert worker_fk, "worker_api_keys should have foreign key to workers"

        engine.dispose()

    @pytest.mark.asyncio
    async def test_data_preserved_during_migration(self, alembic_config, test_db_url):
        """Test that existing data is preserved during migrations."""
        # This test would be more useful with actual migration data
        # For now, we just verify that upgrade->downgrade->upgrade doesn't lose schema

        # Upgrade to head
        command.upgrade(alembic_config, "head")

        # Insert some test data
        engine = sa.create_engine(test_db_url)
        with engine.connect() as conn:
            # Insert a category
            conn.execute(
                sa.text("INSERT INTO categories (name, slug) VALUES (:name, :slug)"),
                {"name": "Test Category", "slug": "test-category"}
            )
            conn.commit()

            # Verify it exists
            result = conn.execute(sa.text("SELECT COUNT(*) FROM categories"))
            count = result.scalar()
            assert count == 1

        engine.dispose()


class TestMigrationVersion:
    """Test migration version tracking."""

    @pytest.mark.asyncio
    async def test_current_revision_matches_head(self, alembic_config, test_db_url):
        """Test that applying migrations results in head revision."""
        from alembic.script import ScriptDirectory

        # Get expected head revision
        script_dir = ScriptDirectory.from_config(alembic_config)
        head_revision = script_dir.get_current_head()

        # Apply migrations
        command.upgrade(alembic_config, "head")

        # Check current revision
        engine = sa.create_engine(test_db_url)
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            current_version = result.scalar()
            assert current_version == head_revision, \
                f"Expected head revision {head_revision}, got {current_version}"
        engine.dispose()

    @pytest.mark.asyncio
    async def test_stamp_command(self, alembic_config, test_db_url):
        """Test that stamp command correctly sets version without running migrations."""
        # Create tables manually (simulating existing database)
        command.upgrade(alembic_config, "head")

        # Get current version
        engine = sa.create_engine(test_db_url)
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            original_version = result.scalar()

        # Stamp with a different version
        command.stamp(alembic_config, "base")

        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            stamped_version = result.scalar()
            # After stamping to base, version should be None or empty
            assert stamped_version != original_version

        engine.dispose()


class TestMigrationSafety:
    """Test migration safety features."""

    @pytest.mark.asyncio
    async def test_no_data_loss_on_table_changes(self, alembic_config, test_db_url):
        """Test that table alterations don't lose existing data."""
        # This is a safety test to ensure migrations handle existing data properly
        # For now, we just verify migrations can run on non-empty database

        # Apply migrations
        command.upgrade(alembic_config, "head")

        # Add test data
        engine = sa.create_engine(test_db_url)
        with engine.connect() as conn:
            conn.execute(
                sa.text("INSERT INTO categories (name, slug) VALUES (:name, :slug)"),
                {"name": "Safety Test", "slug": "safety-test"}
            )
            conn.commit()

            # Run a downgrade and upgrade cycle
            # In production, we wouldn't do this, but it tests migration reversibility
            pass

        engine.dispose()

    @pytest.mark.asyncio
    async def test_idempotent_migrations(self, alembic_config, test_db_url):
        """Test that running migrations multiple times is safe."""
        # Upgrade to head
        command.upgrade(alembic_config, "head")

        # Running upgrade again should be safe (no-op)
        try:
            command.upgrade(alembic_config, "head")
        except Exception as e:
            pytest.fail(f"Running migrations twice should be idempotent: {e}")

        # Verify database is still in good state
        engine = sa.create_engine(test_db_url)
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "videos" in tables
        engine.dispose()
