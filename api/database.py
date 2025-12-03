import sqlalchemy as sa
from databases import Database
from datetime import datetime
import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
from config import DATABASE_PATH

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

database = Database(DATABASE_URL)
metadata = sa.MetaData()

categories = sa.Table(
    "categories",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("slug", sa.String(100), unique=True, nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime, default=datetime.utcnow),
)

videos = sa.Table(
    "videos",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("slug", sa.String(255), unique=True, nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=True),
    sa.Column("duration", sa.Float, default=0),  # seconds
    sa.Column("source_width", sa.Integer, default=0),
    sa.Column("source_height", sa.Integer, default=0),
    sa.Column("status", sa.String(20), default="pending"),  # pending, processing, ready, failed
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime, default=datetime.utcnow),
    sa.Column("published_at", sa.DateTime, nullable=True),
)

# Available quality variants for each video
video_qualities = sa.Table(
    "video_qualities",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE")),
    sa.Column("quality", sa.String(10)),  # 2160p, 1080p, etc.
    sa.Column("width", sa.Integer),
    sa.Column("height", sa.Integer),
    sa.Column("bitrate", sa.Integer),  # kbps
)

# Analytics: unique viewers (cookie-based)
viewers = sa.Table(
    "viewers",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("session_id", sa.String(64), unique=True, nullable=False),
    sa.Column("first_seen", sa.DateTime, default=datetime.utcnow),
    sa.Column("last_seen", sa.DateTime, default=datetime.utcnow),
)

# Analytics: playback sessions
playback_sessions = sa.Table(
    "playback_sessions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
    sa.Column("viewer_id", sa.Integer, sa.ForeignKey("viewers.id", ondelete="SET NULL"), nullable=True),
    sa.Column("session_token", sa.String(64), nullable=False),
    sa.Column("started_at", sa.DateTime, default=datetime.utcnow),
    sa.Column("ended_at", sa.DateTime, nullable=True),
    sa.Column("duration_watched", sa.Float, default=0),  # seconds actually watched
    sa.Column("max_position", sa.Float, default=0),  # furthest point reached
    sa.Column("quality_used", sa.String(10), nullable=True),  # primary quality
    sa.Column("completed", sa.Boolean, default=False),  # watched >= 90%
)


def create_tables():
    """Create all tables in the database."""
    engine = sa.create_engine(DATABASE_URL.replace("sqlite:///", "sqlite:///"))
    metadata.create_all(engine)
    engine.dispose()


if __name__ == "__main__":
    create_tables()
    print("Database tables created successfully!")
