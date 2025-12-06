"""
Standardized exception handling utilities.

This module provides consistent patterns for exception handling across the API,
ensuring HTTPExceptions are properly re-raised and errors are logged uniformly.
"""

import logging
from typing import Any, Callable, Optional, TypeVar

from fastapi import HTTPException

logger = logging.getLogger(__name__)

T = TypeVar("T")


def handle_api_exceptions(
    operation_name: str,
    error_detail: str = "Internal server error",
    status_code: int = 500,
    log_errors: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for standardized exception handling in API endpoints.

    This ensures:
    1. HTTPExceptions are always re-raised (never masked)
    2. Specific domain errors can be caught and converted to appropriate HTTP errors
    3. Generic exceptions are logged and converted to 500 errors with sanitized messages

    Args:
        operation_name: Name of the operation for logging context
        error_detail: Default error message for generic exceptions
        status_code: Default status code for generic exceptions
        log_errors: Whether to log exceptions (default: True)

    Example:
        @handle_api_exceptions("video_upload", "Failed to upload video", 500)
        async def upload_video(...):
            # Your code here
            pass
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # Always re-raise HTTP errors - they contain proper status codes and messages
                raise
            except Exception as e:
                # Log the full exception for debugging
                if log_errors:
                    logger.exception(f"Unexpected error in {operation_name}: {e}")
                # Return a sanitized error to the client
                raise HTTPException(status_code=status_code, detail=error_detail) from e
        return wrapper
    return decorator


def log_and_raise_http_exception(
    exception: Exception,
    status_code: int,
    detail: str,
    operation_name: Optional[str] = None,
    log_level: str = "error",
) -> None:
    """
    Log an exception and raise an HTTPException with sanitized message.

    Args:
        exception: The original exception
        status_code: HTTP status code for the response
        detail: User-facing error message (should be sanitized)
        operation_name: Optional operation name for logging context
        log_level: Logging level (default: "error")

    Example:
        try:
            result = await database_operation()
        except DatabaseError as e:
            log_and_raise_http_exception(
                e, 500, "Database error occurred", "save_video"
            )
    """
    log_msg = f"Error in {operation_name}: {exception}" if operation_name else str(exception)

    log_func = getattr(logger, log_level, logger.error)
    log_func(log_msg)

    raise HTTPException(status_code=status_code, detail=detail) from exception
