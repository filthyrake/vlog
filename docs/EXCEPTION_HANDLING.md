# Exception Handling Standards

This document defines the standard exception handling patterns for the VLog codebase.

## Principles

1. **Always re-raise HTTPException** - Never mask HTTPExceptions as they contain proper status codes and error messages
2. **Use specific exception types** - Catch specific exceptions when possible rather than broad `Exception`
3. **Log with context** - Include operation name and relevant context in error logs
4. **Sanitize error messages** - Use the `sanitize_error_message()` utility to prevent leaking internal details
5. **Maintain exception chains** - Use `raise ... from e` to preserve the original exception

## Standard Patterns

### Pattern 1: Basic Exception Handling with HTTPException Re-raise

**Use when:** You need to catch generic exceptions but want to preserve HTTPExceptions

```python
try:
    result = await some_operation()
except HTTPException:
    raise  # Always re-raise HTTP errors
except Exception as e:
    logger.exception(f"Unexpected error in operation_name: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Pattern 2: Specific Exception Handling

**Use when:** You can predict specific exception types

```python
try:
    result = await database_operation()
except HTTPException:
    raise
except (ValueError, KeyError, TypeError) as e:
    logger.error(f"Validation error in operation_name: {e}")
    raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
except DatabaseLockedError as e:
    raise HTTPException(status_code=503, detail="Database temporarily unavailable")
except Exception as e:
    logger.exception(f"Unexpected error in operation_name: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Pattern 3: Resource Cleanup on Error

**Use when:** You need to clean up resources on failure

```python
resource_path = None
try:
    resource_path = create_resource()
    result = await process_resource(resource_path)
except HTTPException:
    # Clean up on HTTP errors too
    if resource_path:
        cleanup_resource(resource_path)
    raise
except Exception as e:
    # Clean up on any error
    if resource_path:
        cleanup_resource(resource_path)
    logger.exception(f"Error processing resource: {e}")
    raise HTTPException(status_code=500, detail="Processing failed")
```

### Pattern 4: Using the Exception Utilities Decorator

**Use when:** You want standardized handling for an entire function

```python
from api.exception_utils import handle_api_exceptions

@handle_api_exceptions("video_upload", "Failed to upload video", 500)
async def upload_video(...):
    # Your code here - HTTPExceptions will be re-raised,
    # other exceptions will be converted to 500 errors
    result = await process_upload()
    return result
```

### Pattern 5: Background Tasks and Non-Critical Operations

**Use when:** Errors should be logged but not propagate

```python
async def background_cleanup():
    """Non-critical background operation."""
    try:
        await cleanup_old_files()
    except Exception as e:
        # Log but don't propagate - this is a background task
        logger.exception(f"Error in background cleanup: {e}")
        # Don't raise - let the task continue
```

### Pattern 6: Logging with Sanitized User Messages

**Use when:** You want detailed logs but sanitized user-facing errors

```python
from api.errors import sanitize_error_message

try:
    result = await transcode_video()
except HTTPException:
    raise
except Exception as e:
    # Log the full error internally
    logger.exception(f"Transcoding failed for video {video_id}: {e}")
    # Send sanitized error to user
    sanitized = sanitize_error_message(str(e), context=f"video_id={video_id}")
    raise HTTPException(status_code=500, detail=sanitized)
```

## Anti-Patterns to Avoid

### ❌ Don't: Mask HTTPExceptions

```python
# BAD - HTTPException gets masked
try:
    await operation()
except Exception as e:  # This catches HTTPException too!
    raise HTTPException(status_code=500, detail="Error")
```

### ❌ Don't: Bare Exception without Logging

```python
# BAD - Error swallowed with no logging
try:
    await operation()
except Exception:
    pass  # Silent failure
```

### ❌ Don't: Expose Internal Details

```python
# BAD - Exposes internal file paths
try:
    await process_file(path)
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))  # May contain /home/user/...
```

### ❌ Don't: Generic Exception When Specific is Known

```python
# BAD - Should catch ValueError specifically
try:
    value = int(user_input)
except Exception as e:  # Too broad
    raise HTTPException(status_code=400, detail="Invalid input")
```

## Migration Guide

When updating existing code:

1. **Identify broad Exception handlers** - Look for `except Exception` blocks
2. **Add HTTPException re-raise** - Add `except HTTPException: raise` before the Exception block
3. **Consider specific exceptions** - Can you catch more specific exception types?
4. **Add logging** - Use `logger.exception()` for unexpected errors
5. **Sanitize messages** - Use `sanitize_error_message()` for user-facing errors
6. **Test the changes** - Verify HTTPExceptions still propagate correctly

### Example Migration

Before:
```python
try:
    result = await operation()
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))
```

After:
```python
try:
    result = await operation()
except HTTPException:
    raise  # Don't mask HTTP errors
except ValueError as e:
    logger.error(f"Validation error in operation: {e}")
    raise HTTPException(status_code=400, detail="Invalid input")
except Exception as e:
    logger.exception(f"Unexpected error in operation: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

## Testing

All exception handling should be tested:

1. **Test HTTPException propagation** - Verify HTTPExceptions are not masked
2. **Test error conversion** - Verify generic exceptions become HTTPExceptions
3. **Test error messages** - Verify messages are sanitized and user-friendly
4. **Test cleanup** - Verify resources are cleaned up on error
5. **Test logging** - Verify errors are logged with appropriate context

See `tests/test_exception_handling.py` for examples.

## Related Utilities

- `api/exception_utils.py` - Exception handling decorators and utilities
- `api/errors.py` - Error message sanitization
- `api/db_retry.py` - Database-specific retry logic

## Questions?

If you're unsure about the right pattern for a specific case, consider:

1. Is this a user-facing API endpoint? → Use Pattern 1 or 2
2. Is this a background task? → Use Pattern 5
3. Do I need resource cleanup? → Use Pattern 3
4. Is the entire function critical? → Use Pattern 4 decorator
5. Do I know the specific exceptions? → Use Pattern 2

When in doubt, Pattern 1 (basic with HTTPException re-raise) is a safe default.
