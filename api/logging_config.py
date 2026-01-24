"""
Structured JSON Logging Configuration (Issue #208)

Provides centralized logging setup with:
- JSON format for production (log aggregation)
- Text format for development (human readable)
- Request context injection (request_id, client_ip, user_agent)
- Safe handling of non-serializable objects
- Secure user-agent sanitization

Usage:
    from api.logging_config import setup_logging, set_request_context, clear_request_context

    # At application startup (before creating FastAPI app)
    setup_logging()

    # In middleware
    set_request_context(request_id="...", client_ip="...", user_agent="...")
    try:
        response = await call_next(request)
    finally:
        clear_request_context()
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from pythonjsonlogger import jsonlogger

# Flag to prevent duplicate logging setup
_logging_configured = False

# Context variables for request-scoped logging
# These are automatically included in log records when set
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[Optional[str]] = ContextVar("client_ip", default=None)
user_agent_var: ContextVar[Optional[str]] = ContextVar("user_agent", default=None)


# User-Agent sanitization regex (Bruce's security review)
# Removes control characters that could be used for log injection
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_user_agent(user_agent: Optional[str], max_length: int = 512) -> str:
    """
    Sanitize User-Agent header for safe logging.

    Removes control characters that could be used for log injection attacks
    and truncates to prevent excessive log storage.

    Args:
        user_agent: Raw User-Agent header value
        max_length: Maximum allowed length (default: 512)

    Returns:
        Sanitized User-Agent string, empty string if None/empty input
    """
    if not user_agent:
        return ""
    # Remove control characters (including \r\n for log injection)
    sanitized = _CONTROL_CHAR_PATTERN.sub("", user_agent)
    return sanitized[:max_length]


class SafeJSONEncoder(json.JSONEncoder):
    """
    JSON encoder that safely handles non-serializable objects.

    Per Margo's reliability review, this prevents logging failures due to
    objects that can't be serialized to JSON.
    """

    def default(self, obj: Any) -> Any:
        try:
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, (bytes, bytearray)):
                return "<binary data>"
            if isinstance(obj, Path):
                return str(obj)
            if hasattr(obj, "__dict__"):
                return str(obj)
            return super().default(obj)
        except Exception:
            return f"<unserializable: {type(obj).__name__}>"


class VLogJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter with request context injection.

    Automatically adds request_id, client_ip, and user_agent from context vars
    when available. Workers without request context will have these fields omitted.

    Example output:
    {"timestamp": "2024-01-15T10:30:00.123Z", "level": "INFO", "logger": "api.public",
     "message": "Video requested", "request_id": "abc-123", "client_ip": "192.168.1.1"}
    """

    def __init__(self, *args, **kwargs):
        # Set default fields if not specified
        kwargs.setdefault("timestamp", True)
        super().__init__(*args, json_encoder=SafeJSONEncoder, **kwargs)

    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Dict[str, Any],
    ) -> None:
        """Add standard fields and request context to log record."""
        super().add_fields(log_record, record, message_dict)

        # Add timestamp in ISO format with timezone
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Add standard fields
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        # Add request context from context vars (if set)
        # Workers won't have these set, which is fine - they'll be omitted
        request_id = request_id_var.get()
        if request_id:
            log_record["request_id"] = request_id

        client_ip = client_ip_var.get()
        if client_ip:
            log_record["client_ip"] = client_ip

        user_agent = user_agent_var.get()
        if user_agent:
            log_record["user_agent"] = user_agent

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record, with exception handling for reliability.

        Per Margo's review: Logging should never crash the app.
        """
        try:
            return super().format(record)
        except Exception as e:
            # Fallback to simple format if JSON formatting fails
            # Use getMessage() to get the fully formatted message with args
            return json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "ERROR",
                    "logger": "logging_config",
                    "message": f"Failed to format log record: {e}",
                    "original_message": record.getMessage(),
                },
                cls=SafeJSONEncoder,
            )


class VLogTextFormatter(logging.Formatter):
    """
    Human-readable text formatter for development.

    Format: 2024-01-15 10:30:00 INFO [api.public] Video requested
    """

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


class SecureRotatingFileHandler(RotatingFileHandler):
    """
    RotatingFileHandler that ensures log files have restrictive permissions.

    Per Bruce's security review: Log files should be created with 0o600 permissions
    (owner read/write only) to prevent unauthorized access to potentially sensitive
    log data. This handler overrides _open() to ensure permissions are set correctly
    on both the initial file and rotated files.
    """

    def _open(self):
        """Open the log file with secure permissions."""
        # Open the file normally first
        stream = super()._open()

        # Ensure file has restrictive permissions (0o600 = owner read/write only)
        # This handles both new files and rotated files
        try:
            import os

            os.chmod(self.baseFilename, 0o600)
        except OSError:
            # If we can't set permissions (e.g., not owner), log continues to work
            pass

        return stream


def set_request_context(
    request_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    Set request context for logging.

    Call this at the start of each request to include request metadata
    in all log messages during the request lifecycle.

    Args:
        request_id: Unique request identifier
        client_ip: Client IP address
        user_agent: Sanitized User-Agent header
    """
    if request_id:
        request_id_var.set(request_id)
    if client_ip:
        client_ip_var.set(client_ip)
    if user_agent:
        user_agent_var.set(user_agent)


def clear_request_context() -> None:
    """
    Clear request context after request completes.

    MUST be called in a finally block to prevent context leakage
    between requests (per Margo's critical review).
    """
    request_id_var.set(None)
    client_ip_var.set(None)
    user_agent_var.set(None)


def _parse_module_log_levels(log_levels_str: str) -> Dict[str, str]:
    """
    Parse module-specific log level overrides.

    Format: "module1=DEBUG,module2=WARNING"

    Args:
        log_levels_str: Comma-separated module=level pairs

    Returns:
        Dict mapping module names to log levels
    """
    result = {}
    if not log_levels_str:
        return result

    for pair in log_levels_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            module, level = pair.split("=", 1)
            module = module.strip()
            level = level.strip().upper()
            if module and level:
                result[module] = level

    return result


def _get_numeric_level(level_str: str) -> int:
    """
    Convert log level string to numeric value.

    Args:
        level_str: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Numeric log level, defaults to INFO if invalid
    """
    level = getattr(logging, level_str.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def setup_logging(
    log_format: Optional[str] = None,
    log_level: Optional[str] = None,
    log_levels: Optional[str] = None,
    log_file: Optional[str] = None,
    log_file_max_bytes: Optional[int] = None,
    log_file_backup_count: Optional[int] = None,
) -> None:
    """
    Initialize logging configuration.

    Should be called once at application startup, before creating the FastAPI app.
    This function is idempotent - multiple calls will be ignored after the first.
    Reads configuration from parameters or falls back to config module.

    Args:
        log_format: "json" or "text" (default: from config.LOG_FORMAT)
        log_level: Root log level (default: from config.LOG_LEVEL)
        log_levels: Module-specific levels like "api.auth=DEBUG" (default: from config.LOG_LEVELS)
        log_file: Optional file path for log output (default: from config.LOG_FILE)
        log_file_max_bytes: Max file size before rotation (default: from config.LOG_FILE_MAX_BYTES)
        log_file_backup_count: Number of backup files (default: from config.LOG_FILE_BACKUP_COUNT)

    Raises:
        ValueError: If log_format is not "json" or "text"
    """
    global _logging_configured

    # Idempotency check - avoid reconfiguring logging multiple times
    if _logging_configured:
        return

    # Import config lazily to avoid circular imports
    import config as cfg

    # Use provided values or fall back to config
    log_format = log_format or getattr(cfg, "LOG_FORMAT", "json")
    log_level = log_level or getattr(cfg, "LOG_LEVEL", "INFO")
    log_levels = log_levels if log_levels is not None else getattr(cfg, "LOG_LEVELS", "")
    log_file = log_file or getattr(cfg, "LOG_FILE", "")
    log_file_max_bytes = log_file_max_bytes or getattr(cfg, "LOG_FILE_MAX_BYTES", 10 * 1024 * 1024)
    log_file_backup_count = (
        log_file_backup_count if log_file_backup_count is not None else getattr(cfg, "LOG_FILE_BACKUP_COUNT", 5)
    )

    # Validate format (fail fast per Margo's review)
    if log_format not in ("json", "text"):
        raise ValueError(f"Invalid LOG_FORMAT '{log_format}', must be 'json' or 'text'")

    # Create appropriate formatter
    if log_format == "json":
        formatter = VLogJsonFormatter()
    else:
        formatter = VLogTextFormatter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(_get_numeric_level(log_level))

    # Close and remove existing handlers to avoid duplicate logs and file descriptor leaks
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass  # Ignore errors during cleanup
        root_logger.removeHandler(handler)

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional, with rotation and secure permissions)
    if log_file:
        log_path = Path(log_file)

        # Create parent directories if needed
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Use SecureRotatingFileHandler to ensure 0o600 permissions on all log files
        # including rotated files (per Bruce's security review)
        file_handler = SecureRotatingFileHandler(
            filename=log_file,
            maxBytes=log_file_max_bytes,
            backupCount=log_file_backup_count,
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Apply module-specific log levels
    module_levels = _parse_module_log_levels(log_levels)
    for module, level in module_levels.items():
        module_logger = logging.getLogger(module)
        module_logger.setLevel(_get_numeric_level(level))

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Mark as configured to prevent duplicate setup
    _logging_configured = True

    # Log startup info
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging initialized",
        extra={
            "log_format": log_format,
            "log_level": log_level,
            "log_file": log_file or "(none)",
        },
    )
