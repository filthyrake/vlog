"""
Worker command listener for agent-based management.

Subscribes to Redis channels for management commands:
- vlog:worker:{worker_id}:commands - Worker-specific commands
- vlog:workers:commands - Broadcast commands to all workers

Supported commands:
- restart: Finish current job, then restart worker process
- stop: Finish current job, then stop worker process
- update: Pull latest code and restart (for git-based deployments)
"""

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CommandListener:
    """
    Listens for and handles worker management commands via Redis pub/sub.

    Commands are queued and executed after the current job completes,
    ensuring graceful shutdown without interrupting active transcoding.

    Usage:
        listener = CommandListener(worker_id)
        await listener.start()

        # In main loop, after job completion:
        if listener.has_pending_command():
            await listener.execute_pending_command()
            break  # Exit loop if restart/stop was requested
    """

    def __init__(
        self,
        worker_id: str,
        on_restart: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_update: Optional[Callable] = None,
    ):
        """
        Initialize command listener.

        Args:
            worker_id: This worker's UUID
            on_restart: Custom restart handler (default: SIGTERM self)
            on_stop: Custom stop handler (default: SIGTERM self)
            on_update: Custom update handler (default: git pull + SIGTERM)
        """
        self.worker_id = worker_id
        self.on_restart = on_restart or self._default_restart
        self.on_stop = on_stop or self._default_stop
        self.on_update = on_update or self._default_update
        self._subscriber = None
        self._running = False
        self._pending_command: Optional[tuple] = None
        self._listen_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """
        Start listening for commands.

        Returns:
            True if successfully subscribed, False if Redis unavailable
        """
        try:
            from api.pubsub import subscribe_to_worker_commands

            self._subscriber = await subscribe_to_worker_commands(self.worker_id)
            if not self._subscriber.is_active:
                logger.warning("Command listener: Redis pub/sub unavailable")
                return False

            self._running = True
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info(f"Command listener started for worker {self.worker_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to start command listener: {e}")
            return False

    async def _listen_loop(self):
        """Background task to process incoming commands."""
        if not self._subscriber:
            return

        try:
            async for message in self._subscriber.listen():
                if not self._running:
                    break

                command = message.get("command")
                params = message.get("params", {})

                if not command:
                    continue

                logger.info(f"Received management command: {command}")

                if command in ("restart", "stop", "update"):
                    # Queue the command for execution after current job
                    self._pending_command = (command, params)
                    logger.info(f"Command '{command}' queued for execution after current job")
                else:
                    logger.warning(f"Unknown command: {command}")

        except asyncio.CancelledError:
            logger.debug("Command listener cancelled")
        except Exception as e:
            if self._running:
                logger.error(f"Command listener error: {e}")

    def has_pending_command(self) -> bool:
        """Check if there's a pending command waiting for job completion."""
        return self._pending_command is not None

    def get_pending_command(self) -> Optional[str]:
        """Get the pending command name, if any."""
        if self._pending_command:
            return self._pending_command[0]
        return None

    async def execute_pending_command(self):
        """
        Execute pending command after current job completes.

        This should be called from the main worker loop after a job finishes
        when has_pending_command() returns True.
        """
        if not self._pending_command:
            return

        command, params = self._pending_command
        self._pending_command = None

        logger.info(f"Executing pending command: {command}")

        try:
            if command == "restart":
                await self.on_restart(params)
            elif command == "stop":
                await self.on_stop(params)
            elif command == "update":
                await self.on_update(params)
        except Exception as e:
            logger.error(f"Error executing command '{command}': {e}")

    async def stop(self):
        """Stop the command listener and clean up."""
        self._running = False

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._subscriber:
            await self._subscriber.close()
            self._subscriber = None

        logger.debug("Command listener stopped")

    @staticmethod
    async def _default_restart(params: Dict):
        """Default restart handler - send SIGTERM to trigger graceful restart."""
        logger.info("Executing restart: sending SIGTERM to self")
        # The process manager (systemd, k8s) will restart the worker
        os.kill(os.getpid(), signal.SIGTERM)

    @staticmethod
    async def _default_stop(params: Dict):
        """Default stop handler - send SIGTERM for graceful shutdown."""
        logger.info("Executing stop: sending SIGTERM to self")
        os.kill(os.getpid(), signal.SIGTERM)

    @staticmethod
    async def _default_update(params: Dict):
        """Default update handler - git pull and restart."""
        logger.info("Executing update: pulling latest code")
        project_root = Path(__file__).parent.parent

        try:
            # Pull latest code
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                logger.info(f"Git pull successful: {result.stdout.strip()}")
                logger.info("Restarting worker to apply updates...")
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                logger.error(f"Git pull failed: {result.stderr}")

        except subprocess.TimeoutExpired:
            logger.error("Git pull timed out after 60 seconds")
        except FileNotFoundError:
            logger.error("Git command not found - update not available")
        except Exception as e:
            logger.error(f"Update failed: {e}")
