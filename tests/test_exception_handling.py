"""
Tests for standardized exception handling utilities.
"""

import logging

import pytest
from fastapi import HTTPException

from api.exception_utils import handle_api_exceptions, log_and_raise_http_exception


class TestHandleAPIExceptions:
    """Test the handle_api_exceptions decorator."""

    @pytest.mark.asyncio
    async def test_reraises_http_exception(self):
        """HTTPExceptions should always be re-raised."""
        @handle_api_exceptions("test_operation")
        async def failing_func():
            raise HTTPException(status_code=404, detail="Not found")

        with pytest.raises(HTTPException) as exc_info:
            await failing_func()

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Not found"

    @pytest.mark.asyncio
    async def test_converts_generic_exception_to_http(self):
        """Generic exceptions should be converted to HTTPException."""
        @handle_api_exceptions("test_operation", "Operation failed", 500)
        async def failing_func():
            raise ValueError("Some internal error")

        with pytest.raises(HTTPException) as exc_info:
            await failing_func()

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Operation failed"

    @pytest.mark.asyncio
    async def test_custom_status_code(self):
        """Should use custom status code for generic exceptions."""
        @handle_api_exceptions("test_operation", "Bad request", 400)
        async def failing_func():
            raise ValueError("Invalid input")

        with pytest.raises(HTTPException) as exc_info:
            await failing_func()

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Bad request"

    @pytest.mark.asyncio
    async def test_logs_exception(self, caplog):
        """Should log exceptions when log_errors=True."""
        @handle_api_exceptions("test_operation", "Error occurred", 500, log_errors=True)
        async def failing_func():
            raise ValueError("Internal error")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPException):
                await failing_func()

        assert "Unexpected error in test_operation" in caplog.text
        assert "Internal error" in caplog.text

    @pytest.mark.asyncio
    async def test_no_logging_when_disabled(self, caplog):
        """Should not log exceptions when log_errors=False."""
        @handle_api_exceptions("test_operation", "Error occurred", 500, log_errors=False)
        async def failing_func():
            raise ValueError("Internal error")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPException):
                await failing_func()

        assert "Unexpected error in test_operation" not in caplog.text

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """Should pass through successful execution."""
        @handle_api_exceptions("test_operation")
        async def success_func():
            return "success"

        result = await success_func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_preserves_http_exception_details(self):
        """Should preserve all HTTPException details including status code and headers."""
        @handle_api_exceptions("test_operation")
        async def failing_func():
            raise HTTPException(
                status_code=503,
                detail="Service unavailable",
                headers={"Retry-After": "30"}
            )

        with pytest.raises(HTTPException) as exc_info:
            await failing_func()

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Service unavailable"
        assert exc_info.value.headers == {"Retry-After": "30"}

    @pytest.mark.asyncio
    async def test_exception_chaining(self):
        """Should maintain exception chain with __cause__."""
        @handle_api_exceptions("test_operation", "Operation failed")
        async def failing_func():
            raise ValueError("Original error")

        with pytest.raises(HTTPException) as exc_info:
            await failing_func()

        # Check that the original exception is preserved in the chain
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert str(exc_info.value.__cause__) == "Original error"

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        """Should preserve function name, docstring, and annotations with @functools.wraps."""
        @handle_api_exceptions("test_operation")
        async def documented_func(arg: int) -> str:
            """This is a test function."""
            return f"result: {arg}"

        # Check that function metadata is preserved
        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "This is a test function."
        assert "arg" in documented_func.__annotations__
        assert documented_func.__annotations__["arg"] is int
        assert documented_func.__annotations__["return"] is str

        # Also verify it still works functionally
        result = await documented_func(42)
        assert result == "result: 42"


class TestLogAndRaiseHTTPException:
    """Test the log_and_raise_http_exception utility."""

    def test_logs_and_raises(self, caplog):
        """Should log the exception and raise HTTPException."""
        original_error = ValueError("Database error")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPException) as exc_info:
                log_and_raise_http_exception(
                    original_error,
                    500,
                    "Internal server error",
                    "save_video"
                )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"
        assert "Error in save_video" in caplog.text
        assert "Database error" in caplog.text

    def test_logs_without_operation_name(self, caplog):
        """Should log even without operation name."""
        original_error = ValueError("Some error")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPException):
                log_and_raise_http_exception(
                    original_error,
                    500,
                    "Error occurred"
                )

        assert "Some error" in caplog.text

    def test_custom_log_level(self, caplog):
        """Should support custom log levels."""
        original_error = ValueError("Warning level error")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(HTTPException):
                log_and_raise_http_exception(
                    original_error,
                    400,
                    "Bad request",
                    "validate_input",
                    log_level="warning"
                )

        assert "Error in validate_input" in caplog.text
        # Check it was logged at WARNING level
        assert any(record.levelname == "WARNING" for record in caplog.records)

    def test_exception_chaining(self):
        """Should maintain exception chain."""
        original_error = ValueError("Original error")

        with pytest.raises(HTTPException) as exc_info:
            log_and_raise_http_exception(
                original_error,
                500,
                "Error occurred"
            )

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert str(exc_info.value.__cause__) == "Original error"


class TestExceptionPatternConsistency:
    """Test that exception patterns are consistent across the codebase."""

    @pytest.mark.asyncio
    async def test_standard_pattern_example(self):
        """Demonstrate the standard exception handling pattern."""
        @handle_api_exceptions("example_operation", "Operation failed", 500)
        async def example_operation():
            # This would be actual operation code
            raise ValueError("Something went wrong")

        # Should convert to HTTPException with proper status and message
        with pytest.raises(HTTPException) as exc_info:
            await example_operation()

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Operation failed"

    @pytest.mark.asyncio
    async def test_http_exceptions_not_masked(self):
        """HTTPExceptions should never be masked by exception handlers."""
        @handle_api_exceptions("example_operation")
        async def operation_with_http_error():
            raise HTTPException(status_code=404, detail="Resource not found")

        # HTTPException should pass through unchanged
        with pytest.raises(HTTPException) as exc_info:
            await operation_with_http_error()

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Resource not found"
