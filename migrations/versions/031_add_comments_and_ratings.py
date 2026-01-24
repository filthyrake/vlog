"""add_comments_and_ratings

Revision ID: 031
Revises: 030
Create Date: 2026-01-24

Adds comments and ratings system (Issue #213):
- Enables ltree extension for materialized path threading
- Creates comments table with ltree path for threading (max depth: 5)
- Creates ratings table with composite primary key
- Adds per-video toggle columns (NULL = inherit from global settings)
- Adds denormalized aggregate columns to videos table
- Creates triggers to maintain aggregate counts/averages
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "031"
down_revision: Union[str, Sequence[str], None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add comments and ratings tables with denormalized aggregates on videos."""

    # Enable ltree extension for hierarchical path queries
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree;")

    # Add per-video toggle columns (NULL = inherit from global settings)
    op.add_column(
        "videos",
        sa.Column("comments_enabled", sa.Boolean, nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("ratings_enabled", sa.Boolean, nullable=True),
    )

    # Add denormalized aggregate columns to videos table
    op.add_column(
        "videos",
        sa.Column("comment_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "videos",
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("rating_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "videos",
        sa.Column(
            "rating_distribution",
            sa.Text,
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "videos",
        sa.Column("likes_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "videos",
        sa.Column("dislikes_count", sa.Integer, nullable=False, server_default="0"),
    )

    # Create comments table with ltree materialized path
    op.execute(
        """
        CREATE TABLE comments (
            id SERIAL PRIMARY KEY,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            -- Materialized path for threading (e.g., "1.5.23")
            path LTREE NOT NULL,
            depth INTEGER NOT NULL DEFAULT 1 CHECK (depth >= 1 AND depth <= 5),
            parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,

            content TEXT NOT NULL,
            video_timestamp NUMERIC(10,3),  -- Millisecond precision for timestamp links

            -- Status: pending (if moderation enabled), approved, rejected, spam
            status VARCHAR(20) NOT NULL DEFAULT 'approved'
                CHECK (status IN ('pending', 'approved', 'rejected', 'spam')),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ  -- Soft delete
        );
        """
    )

    # Create optimized indexes for comments
    # GiST index for ltree path queries (ancestors, descendants)
    op.execute("CREATE INDEX idx_comments_path ON comments USING GIST (path);")

    # Composite partial index for active comments by video
    op.execute(
        """
        CREATE INDEX idx_comments_video_active
        ON comments(video_id, status, created_at DESC)
        WHERE deleted_at IS NULL;
        """
    )

    # Index for user's comments
    op.execute("CREATE INDEX idx_comments_user ON comments(user_id, created_at DESC);")

    # Partial index for replies (only when parent exists)
    op.execute(
        """
        CREATE INDEX idx_comments_parent
        ON comments(parent_id)
        WHERE parent_id IS NOT NULL;
        """
    )

    # Partial index for moderation queue
    op.execute(
        """
        CREATE INDEX idx_comments_pending
        ON comments(status, created_at)
        WHERE status = 'pending' AND deleted_at IS NULL;
        """
    )

    # Create ratings table with composite primary key
    op.execute(
        """
        CREATE TABLE ratings (
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            -- Single value: 1-5 for stars, 1 (like) or -1 (dislike) for thumbs
            rating_value INTEGER NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            PRIMARY KEY (video_id, user_id)
        );
        """
    )

    # Create indexes for ratings
    op.execute("CREATE INDEX idx_ratings_video ON ratings(video_id);")
    op.execute("CREATE INDEX idx_ratings_user ON ratings(user_id);")

    # Create trigger function to update videos.comment_count
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_video_comment_count()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                -- Only count approved, non-deleted comments
                IF NEW.status = 'approved' AND NEW.deleted_at IS NULL THEN
                    UPDATE videos SET comment_count = comment_count + 1
                    WHERE id = NEW.video_id;
                END IF;
            ELSIF TG_OP = 'UPDATE' THEN
                -- Handle status changes and soft deletes
                IF OLD.video_id = NEW.video_id THEN
                    -- Same video, check if visibility changed
                    IF (OLD.status = 'approved' AND OLD.deleted_at IS NULL) AND
                       NOT (NEW.status = 'approved' AND NEW.deleted_at IS NULL) THEN
                        UPDATE videos SET comment_count = comment_count - 1
                        WHERE id = NEW.video_id AND comment_count > 0;
                    ELSIF NOT (OLD.status = 'approved' AND OLD.deleted_at IS NULL) AND
                          (NEW.status = 'approved' AND NEW.deleted_at IS NULL) THEN
                        UPDATE videos SET comment_count = comment_count + 1
                        WHERE id = NEW.video_id;
                    END IF;
                ELSE
                    -- Video changed (shouldn't happen, but handle it)
                    IF OLD.status = 'approved' AND OLD.deleted_at IS NULL THEN
                        UPDATE videos SET comment_count = comment_count - 1
                        WHERE id = OLD.video_id AND comment_count > 0;
                    END IF;
                    IF NEW.status = 'approved' AND NEW.deleted_at IS NULL THEN
                        UPDATE videos SET comment_count = comment_count + 1
                        WHERE id = NEW.video_id;
                    END IF;
                END IF;
            ELSIF TG_OP = 'DELETE' THEN
                IF OLD.status = 'approved' AND OLD.deleted_at IS NULL THEN
                    UPDATE videos SET comment_count = comment_count - 1
                    WHERE id = OLD.video_id AND comment_count > 0;
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_update_video_comment_count
        AFTER INSERT OR UPDATE OR DELETE ON comments
        FOR EACH ROW EXECUTE FUNCTION update_video_comment_count();
        """
    )

    # Create trigger function to update videos rating aggregates
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_video_rating_aggregates()
        RETURNS TRIGGER AS $$
        DECLARE
            v_video_id INTEGER;
            v_avg NUMERIC(3,2);
            v_count INTEGER;
            v_distribution JSONB;
            v_likes INTEGER;
            v_dislikes INTEGER;
        BEGIN
            -- Determine which video to update
            IF TG_OP = 'DELETE' THEN
                v_video_id := OLD.video_id;
            ELSE
                v_video_id := NEW.video_id;
            END IF;

            -- Calculate new aggregates
            SELECT
                ROUND(AVG(rating_value)::NUMERIC, 2),
                COUNT(*),
                COALESCE(jsonb_object_agg(rating_value::text, cnt), '{}'),
                COALESCE(SUM(CASE WHEN rating_value > 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN rating_value < 0 THEN 1 ELSE 0 END), 0)
            INTO v_avg, v_count, v_distribution, v_likes, v_dislikes
            FROM (
                SELECT rating_value, COUNT(*) as cnt
                FROM ratings
                WHERE video_id = v_video_id
                GROUP BY rating_value
            ) AS rating_counts,
            (
                SELECT rating_value FROM ratings WHERE video_id = v_video_id
            ) AS all_ratings;

            -- Recalculate properly
            SELECT
                ROUND(AVG(rating_value)::NUMERIC, 2),
                COUNT(*),
                COALESCE(SUM(CASE WHEN rating_value > 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN rating_value < 0 THEN 1 ELSE 0 END), 0)
            INTO v_avg, v_count, v_likes, v_dislikes
            FROM ratings
            WHERE video_id = v_video_id;

            -- Calculate distribution
            SELECT COALESCE(jsonb_object_agg(rating_value::text, cnt), '{}')
            INTO v_distribution
            FROM (
                SELECT rating_value, COUNT(*) as cnt
                FROM ratings
                WHERE video_id = v_video_id
                GROUP BY rating_value
            ) AS rating_counts;

            -- Update the video
            UPDATE videos
            SET
                rating_avg = v_avg,
                rating_count = COALESCE(v_count, 0),
                rating_distribution = COALESCE(v_distribution::text, '{}'),
                likes_count = COALESCE(v_likes, 0),
                dislikes_count = COALESCE(v_dislikes, 0)
            WHERE id = v_video_id;

            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_update_video_rating_aggregates
        AFTER INSERT OR UPDATE OR DELETE ON ratings
        FOR EACH ROW EXECUTE FUNCTION update_video_rating_aggregates();
        """
    )


def downgrade() -> None:
    """Remove comments and ratings tables and related columns."""

    # Drop triggers first
    op.execute("DROP TRIGGER IF EXISTS trg_update_video_rating_aggregates ON ratings;")
    op.execute("DROP TRIGGER IF EXISTS trg_update_video_comment_count ON comments;")

    # Drop trigger functions
    op.execute("DROP FUNCTION IF EXISTS update_video_rating_aggregates();")
    op.execute("DROP FUNCTION IF EXISTS update_video_comment_count();")

    # Drop tables (indexes are dropped automatically with tables)
    op.execute("DROP TABLE IF EXISTS ratings;")
    op.execute("DROP TABLE IF EXISTS comments;")

    # Remove columns from videos table
    op.drop_column("videos", "dislikes_count")
    op.drop_column("videos", "likes_count")
    op.drop_column("videos", "rating_distribution")
    op.drop_column("videos", "rating_count")
    op.drop_column("videos", "rating_avg")
    op.drop_column("videos", "comment_count")
    op.drop_column("videos", "ratings_enabled")
    op.drop_column("videos", "comments_enabled")

    # Note: We don't drop the ltree extension as other migrations might use it
