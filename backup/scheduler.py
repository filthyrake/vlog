"""
Backup scheduler daemon.

Runs scheduled backups at configurable times with:
- Daily or weekly schedules
- Graceful shutdown handling (SIGTERM/SIGINT)
- Health endpoint for monitoring
- Automatic retention policy application
- PID file for process supervision

Run as: python -m backup.scheduler

Alternatively, use cron or systemd-timer for simpler deployments.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# PID file location
PID_FILE = Path("/var/run/vlog/backup-scheduler.pid")

# Health check port
HEALTH_PORT = 8081


class BackupScheduler:
    """
    Scheduled backup daemon.

    Supports daily and weekly backup schedules.
    """

    def __init__(
        self,
        schedule_time: str = "02:00",
        schedule_day: Optional[str] = None,
        include_videos: bool = False,
        upload_to_s3: bool = False,
        retention_count: int = 7,
    ):
        """
        Initialize scheduler.

        Args:
            schedule_time: Time to run backup (HH:MM in 24-hour format)
            schedule_day: Day of week for weekly backups (0=Monday, 6=Sunday)
                         If None, runs daily.
            include_videos: Whether to include video files
            upload_to_s3: Whether to upload to S3
            retention_count: Number of backups to retain
        """
        self.schedule_time = self._parse_time(schedule_time)
        self.schedule_day = int(schedule_day) if schedule_day else None
        self.include_videos = include_videos
        self.upload_to_s3 = upload_to_s3
        self.retention_count = retention_count

        self._running = False
        self._shutdown_event = asyncio.Event()

    def _parse_time(self, time_str: str) -> time:
        """Parse time string to time object."""
        try:
            parts = time_str.split(":")
            return time(hour=int(parts[0]), minute=int(parts[1]))
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid time format '{time_str}'. Use HH:MM format.") from e

    def _get_next_run_time(self) -> datetime:
        """Calculate next scheduled run time."""
        now = datetime.now(timezone.utc)
        today = now.date()

        # Create scheduled time for today
        scheduled = datetime.combine(today, self.schedule_time, tzinfo=timezone.utc)

        # If we're past the scheduled time today, move to tomorrow
        if now >= scheduled:
            scheduled = scheduled + timedelta(days=1)

        # For weekly schedules, find the next matching day
        if self.schedule_day is not None:
            while scheduled.weekday() != self.schedule_day:
                scheduled = scheduled + timedelta(days=1)

        return scheduled

    async def _run_backup(self) -> None:
        """Run a scheduled backup."""
        from config import BACKUP_PATH, BACKUP_RETENTION_COUNT, DATABASE_URL, VIDEOS_DIR
        from backup.manifest import BackupType
        from backup.service import BackupService

        logger.info("Starting scheduled backup...")

        try:
            service = BackupService(
                database_url=DATABASE_URL,
                videos_dir=VIDEOS_DIR,
                backup_path=BACKUP_PATH,
            )

            result = await service.create_backup(
                backup_type=BackupType.FULL if self.include_videos else BackupType.DATABASE_ONLY,
                include_videos=self.include_videos,
                description="Scheduled backup",
                upload_to_s3=self.upload_to_s3,
                created_by="scheduler",
            )

            logger.info(f"Scheduled backup complete: {result['backup_id']}")

            # Apply retention policy
            deleted = await service.apply_retention_policy(
                self.retention_count or BACKUP_RETENTION_COUNT
            )
            if deleted:
                logger.info(f"Retention policy: deleted {len(deleted)} old backups")

            # Send success alert via webhook if configured
            await self._send_alert("backup_success", {
                "backup_id": result["backup_id"],
                "size_bytes": result["size_bytes"],
            })

        except Exception as e:
            logger.error(f"Scheduled backup failed: {e}")
            # Send failure alert
            await self._send_alert("backup_failed", {
                "error": str(e),
            })

    async def _send_alert(self, event_type: str, data: dict) -> None:
        """Send alert via webhook if configured."""
        from config import ALERT_WEBHOOK_URL

        if not ALERT_WEBHOOK_URL:
            return

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                await client.post(
                    ALERT_WEBHOOK_URL,
                    json={
                        "event": f"backup.{event_type}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **data,
                    },
                    timeout=10,
                )
        except Exception as e:
            logger.warning(f"Failed to send alert: {e}")

    async def _health_server(self) -> None:
        """Simple health check HTTP server."""
        try:
            from aiohttp import web

            async def health_handler(request):
                return web.json_response({
                    "status": "healthy",
                    "running": self._running,
                    "next_run": self._get_next_run_time().isoformat(),
                })

            app = web.Application()
            app.router.add_get("/health", health_handler)
            app.router.add_get("/", health_handler)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
            await site.start()
            logger.info(f"Health endpoint listening on port {HEALTH_PORT}")

            # Keep running until shutdown
            await self._shutdown_event.wait()

            await runner.cleanup()
        except ImportError:
            logger.warning("aiohttp not installed, health endpoint disabled")
        except Exception as e:
            logger.warning(f"Failed to start health endpoint: {e}")

    def _write_pid_file(self) -> None:
        """Write PID file."""
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(os.getpid()))
            logger.info(f"PID file written: {PID_FILE}")
        except Exception as e:
            logger.warning(f"Failed to write PID file: {e}")

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            self._running = False
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    async def run(self) -> None:
        """
        Run the scheduler daemon.

        Blocks until shutdown signal received.
        """
        # Refuse to run as root
        if os.getuid() == 0:
            logger.error("Refusing to run as root. Use a non-privileged user.")
            sys.exit(1)

        self._setup_signal_handlers()
        self._write_pid_file()
        self._running = True

        logger.info("Backup scheduler started")
        logger.info(f"Schedule: {self.schedule_time}")
        if self.schedule_day is not None:
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            logger.info(f"Weekly backup on {days[self.schedule_day]}")
        else:
            logger.info("Daily backup")

        # Start health server
        health_task = asyncio.create_task(self._health_server())

        try:
            while self._running:
                next_run = self._get_next_run_time()
                now = datetime.now(timezone.utc)
                wait_seconds = (next_run - now).total_seconds()

                logger.info(f"Next backup scheduled at {next_run.isoformat()} ({wait_seconds:.0f}s)")

                # Wait until scheduled time or shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=wait_seconds,
                    )
                    # Shutdown requested
                    break
                except asyncio.TimeoutError:
                    # Time to run backup
                    pass

                if self._running:
                    await self._run_backup()

        finally:
            self._shutdown_event.set()
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass
            self._remove_pid_file()
            logger.info("Backup scheduler stopped")


def main():
    """Entry point for backup scheduler."""
    from config import (
        BACKUP_INCLUDE_VIDEOS,
        BACKUP_RETENTION_COUNT,
        BACKUP_S3_BUCKET,
        BACKUP_SCHEDULE_DAY,
        BACKUP_SCHEDULE_ENABLED,
        BACKUP_SCHEDULE_TIME,
    )
    from api.logging_config import setup_logging

    # Set up logging
    setup_logging()

    if not BACKUP_SCHEDULE_ENABLED:
        logger.error("Backup scheduling is not enabled. Set VLOG_BACKUP_SCHEDULE_ENABLED=true")
        sys.exit(1)

    scheduler = BackupScheduler(
        schedule_time=BACKUP_SCHEDULE_TIME,
        schedule_day=BACKUP_SCHEDULE_DAY or None,
        include_videos=BACKUP_INCLUDE_VIDEOS,
        upload_to_s3=bool(BACKUP_S3_BUCKET),
        retention_count=BACKUP_RETENTION_COUNT,
    )

    asyncio.run(scheduler.run())


if __name__ == "__main__":
    main()
