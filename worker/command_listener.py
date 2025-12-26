"""
Worker command listener for agent-based management.

Subscribes to Redis channels for management commands:
- vlog:worker:{worker_id}:commands - Worker-specific commands
- vlog:workers:commands - Broadcast commands to all workers

Supported commands:
- restart: Finish current job, then restart worker process
- stop: Finish current job, then stop worker process
- update: Pull latest code and restart (for git-based deployments)
- get_logs: Fetch recent logs and publish to response channel (immediate)
- get_metrics: Fetch worker metrics and publish to response channel (immediate)
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


async def _publish_response(channel: str, data: Dict) -> bool:
    """Publish a response to a Redis channel."""
    try:
        from api.redis_client import get_redis

        redis = await get_redis()
        if not redis:
            return False

        await redis.publish(channel, json.dumps(data))
        return True
    except Exception as e:
        logger.error(f"Failed to publish response: {e}")
        return False


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
                request_id = message.get("request_id")

                if not command:
                    continue

                logger.info(f"Received management command: {command}")

                if command in ("restart", "stop", "update"):
                    # Queue the command for execution after current job
                    self._pending_command = (command, params)
                    logger.info(f"Command '{command}' queued for execution after current job")
                elif command == "get_logs":
                    # Immediate response command - fetch logs and respond
                    asyncio.create_task(self._handle_get_logs(params, request_id))
                elif command == "get_metrics":
                    # Immediate response command - fetch metrics and respond
                    asyncio.create_task(self._handle_get_metrics(params, request_id))
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

    async def _handle_get_logs(self, params: Dict, request_id: Optional[str]):
        """Handle get_logs command - fetch logs and publish response."""
        from worker.hwaccel import detect_deployment_type

        lines = params.get("lines", 100)
        response_channel = f"vlog:worker:{self.worker_id}:response:{request_id or 'default'}"

        try:
            deployment_type = detect_deployment_type()
            logs = await self._fetch_logs(deployment_type, lines)

            response = {
                "type": "logs_response",
                "worker_id": self.worker_id,
                "request_id": request_id,
                "success": True,
                "logs": logs,
                "deployment_type": deployment_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to fetch logs: {e}")
            response = {
                "type": "logs_response",
                "worker_id": self.worker_id,
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        await _publish_response(response_channel, response)

    async def _fetch_logs(self, deployment_type: str, lines: int) -> str:
        """Fetch logs based on deployment type."""
        try:
            if deployment_type == "systemd":
                # Fetch from journalctl
                result = subprocess.run(
                    ["journalctl", "-u", "vlog-worker", "-n", str(lines), "--no-pager"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return result.stdout
                # Fallback: try without unit specification
                result = subprocess.run(
                    ["journalctl", "-n", str(lines), "--no-pager", f"_PID={os.getpid()}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return result.stdout if result.returncode == 0 else f"journalctl error: {result.stderr}"

            elif deployment_type == "kubernetes":
                # In K8s, logs are typically managed by the container runtime
                # Return what we can from stdout (if captured) or point to kubectl
                return self._get_python_logs(lines)

            elif deployment_type == "docker":
                # Docker logs are managed by the Docker daemon
                # Return Python logs from memory
                return self._get_python_logs(lines)

            else:
                # Manual deployment - try to get Python logs
                return self._get_python_logs(lines)

        except subprocess.TimeoutExpired:
            return "Log fetch timed out"
        except FileNotFoundError as e:
            return f"Log command not found: {e}"
        except Exception as e:
            return f"Failed to fetch logs: {e}"

    def _get_python_logs(self, lines: int) -> str:
        """Get recent Python log entries from the logging system."""
        # Check for log files in common locations
        log_locations = [
            Path("/var/log/vlog-worker.log"),
            Path.home() / ".vlog" / "worker.log",
            Path("/tmp/vlog-worker.log"),
        ]

        for log_path in log_locations:
            if log_path.exists():
                try:
                    with open(log_path, "r") as f:
                        all_lines = f.readlines()
                        return "".join(all_lines[-lines:])
                except Exception:
                    continue

        # If no log file found, return process info
        return f"""Worker Process Info:
PID: {os.getpid()}
Python: {sys.executable}
Working Dir: {os.getcwd()}

Note: Logs are written to stdout/stderr.
For containerized workers, use: kubectl logs <pod-name>
For systemd workers, use: journalctl -u vlog-worker
"""

    async def _handle_get_metrics(self, params: Dict, request_id: Optional[str]):
        """Handle get_metrics command - fetch metrics and publish response."""
        import psutil

        response_channel = f"vlog:worker:{self.worker_id}:response:{request_id or 'default'}"

        try:
            process = psutil.Process(os.getpid())

            # Get CPU and memory info
            cpu_percent = process.cpu_percent(interval=0.1)
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()

            # Get system-wide info
            system_cpu = psutil.cpu_percent(interval=0.1)
            system_memory = psutil.virtual_memory()

            # Get disk usage for work directory
            from config import WORKER_WORK_DIR

            disk_usage = psutil.disk_usage(str(WORKER_WORK_DIR))

            # Try to get GPU info if available
            gpu_info = await self._get_gpu_metrics()

            response = {
                "type": "metrics_response",
                "worker_id": self.worker_id,
                "request_id": request_id,
                "success": True,
                "metrics": {
                    "process": {
                        "pid": os.getpid(),
                        "cpu_percent": cpu_percent,
                        "memory_rss_mb": memory_info.rss / (1024 * 1024),
                        "memory_percent": memory_percent,
                        "threads": process.num_threads(),
                        "open_files": len(process.open_files()),
                    },
                    "system": {
                        "cpu_percent": system_cpu,
                        "memory_total_gb": system_memory.total / (1024**3),
                        "memory_available_gb": system_memory.available / (1024**3),
                        "memory_percent": system_memory.percent,
                    },
                    "disk": {
                        "total_gb": disk_usage.total / (1024**3),
                        "used_gb": disk_usage.used / (1024**3),
                        "free_gb": disk_usage.free / (1024**3),
                        "percent": disk_usage.percent,
                    },
                    "gpu": gpu_info,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to fetch metrics: {e}")
            response = {
                "type": "metrics_response",
                "worker_id": self.worker_id,
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        await _publish_response(response_channel, response)

    async def _get_gpu_metrics(self) -> Optional[Dict]:
        """Get GPU metrics if available."""
        try:
            # Try nvidia-smi for NVIDIA GPUs
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                values = result.stdout.strip().split(", ")
                if len(values) >= 5:
                    return {
                        "type": "nvidia",
                        "utilization_percent": float(values[0]),
                        "memory_utilization_percent": float(values[1]),
                        "memory_used_mb": float(values[2]),
                        "memory_total_mb": float(values[3]),
                        "temperature_c": float(values[4]),
                    }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        except Exception as e:
            logger.debug(f"GPU metrics unavailable: {e}")

        return None
