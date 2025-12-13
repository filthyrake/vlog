# Testing Guide

This document describes the testing infrastructure and guidelines for VLog.

## Test Organization

### Test Structure

```
tests/
├── conftest.py                          # Shared fixtures and test database setup
├── test_*.py                            # Test modules organized by component
│
├── Unit Tests (Component-specific)
│   ├── test_admin_api.py               # Admin API endpoints
│   ├── test_public_api.py              # Public API endpoints
│   ├── test_worker_api.py              # Worker API endpoints
│   ├── test_cli.py                     # CLI command tests
│   ├── test_transcoder.py              # Transcoding logic
│   ├── test_hwaccel.py                 # Hardware acceleration
│   └── test_*.py                       # Other component tests
│
├── Integration Tests
│   ├── test_transcoder_integration.py  # Transcoder pipeline integration
│   ├── test_worker_integration.py      # Worker job flow integration
│   ├── test_analytics_caching_integration.py
│   └── test_remote_transcoder.py       # Remote worker lifecycle
│
└── End-to-End Tests
    ├── test_e2e_upload.py              # Complete upload workflow
    ├── test_workflows.py               # Multi-component workflows
    └── test_migrations.py              # Database migration tests
```

## Running Tests

### Prerequisites

1. **PostgreSQL**: Tests require a PostgreSQL database server
   ```bash
   # Configure test database connection
   export VLOG_TEST_PG_HOST=localhost
   export VLOG_TEST_PG_PORT=5432
   export VLOG_TEST_PG_USER=vlog
   export VLOG_TEST_PG_PASSWORD=vlog_password
   ```

2. **Python Dependencies**: Install test dependencies
   ```bash
   pip install -e .
   pip install pytest pytest-asyncio pytest-cov
   ```

3. **Test Mode**: Always set test mode environment variable
   ```bash
   export VLOG_TEST_MODE=1
   ```

### Running All Tests

```bash
# Run all tests with coverage
VLOG_TEST_MODE=1 pytest

# Run with coverage report
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker --cov=cli --cov-report=html

# Run with verbose output
VLOG_TEST_MODE=1 pytest -v
```

### Running Specific Tests

```bash
# Run a single test file
VLOG_TEST_MODE=1 pytest tests/test_public_api.py

# Run a specific test class
VLOG_TEST_MODE=1 pytest tests/test_admin_api.py::TestVideoUpload

# Run a specific test
VLOG_TEST_MODE=1 pytest tests/test_admin_api.py::TestVideoUpload::test_upload_video

# Run tests matching a pattern
VLOG_TEST_MODE=1 pytest -k "upload"
```

### Running by Test Type

```bash
# Run only integration tests
VLOG_TEST_MODE=1 pytest -m integration

# Run only end-to-end tests
VLOG_TEST_MODE=1 pytest -m e2e

# Exclude slow tests
VLOG_TEST_MODE=1 pytest -m "not slow"
```

## Test Coverage

### Coverage Goals

- **Line coverage**: 80%+ (current baseline)
- **Branch coverage**: 70%+
- **Critical paths**: 100% (upload, transcode, playback)

### Viewing Coverage Reports

After running tests with coverage:

```bash
# View HTML report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux

# View terminal summary
VLOG_TEST_MODE=1 pytest --cov-report=term-missing
```

### Coverage by Module

```bash
# Check coverage for specific module
VLOG_TEST_MODE=1 pytest --cov=api.admin --cov-report=term

# Check coverage with branch analysis
VLOG_TEST_MODE=1 pytest --cov=api --cov-report=term --cov-branch
```

## Writing Tests

### Test Fixtures

Common fixtures are defined in `tests/conftest.py`:

- `test_database`: Async database connection with fresh schema
- `test_storage`: Temporary directories for video files
- `admin_client`: Test client for admin API
- `public_client`: Test client for public API
- `worker_client`: Test client for worker API
- `sample_category`: Pre-created category
- `sample_pending_video`: Video in pending state
- `sample_video`: Video in ready state
- `registered_worker`: Pre-registered worker with API key

### Test Naming Convention

```python
class TestFeatureName:
    """Test description."""
    
    @pytest.mark.asyncio
    async def test_specific_behavior(self, fixture1, fixture2):
        """Test that specific behavior works as expected."""
        # Arrange
        setup_data()
        
        # Act
        result = await perform_action()
        
        # Assert
        assert result == expected
```

### Marking Tests

```python
# Mark as integration test
@pytest.mark.integration
async def test_integration_workflow():
    pass

# Mark as end-to-end test
@pytest.mark.e2e
async def test_full_system_workflow():
    pass

# Mark as slow test
@pytest.mark.slow
async def test_large_file_processing():
    pass
```

### Testing Async Code

```python
import pytest

@pytest.mark.asyncio
async def test_async_function(test_database):
    """Test async database operations."""
    result = await test_database.fetch_one(query)
    assert result is not None
```

### Testing API Endpoints

```python
def test_api_endpoint(admin_client):
    """Test API endpoint response."""
    response = admin_client.post(
        "/api/videos",
        files={"file": ("test.mp4", io.BytesIO(b"content"), "video/mp4")},
        data={"title": "Test Video"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "video_id" in data
```

### Testing Error Conditions

```python
def test_error_handling(admin_client):
    """Test that errors are handled appropriately."""
    # Test missing required field
    response = admin_client.post("/api/videos", data={})
    assert response.status_code == 400
    
    # Test invalid data
    response = admin_client.post("/api/videos", json={"title": ""})
    assert response.status_code == 422
```

## Continuous Integration

### GitHub Actions

Tests run automatically on:
- Push to main branch
- Pull requests to main branch

CI workflow (`.github/workflows/tests.yml`):
1. Set up Python 3.12
2. Start PostgreSQL service
3. Install dependencies
4. Run tests with coverage
5. Upload coverage report
6. Check coverage thresholds

### Local CI Simulation

```bash
# Install dependencies
pip install -e .
pip install pytest pytest-asyncio pytest-cov ruff

# Run linting
ruff check api/ worker/ cli/ tests/ config.py

# Run tests
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker --cov=cli --cov-report=term-missing

# Check coverage meets threshold (80%+)
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker --cov=cli --cov-fail-under=80
```

## Test Database

### PostgreSQL Setup

Tests use a unique PostgreSQL database per test session:

1. Each test session generates a unique database name: `vlog_test_<uuid>`
2. Database is created before tests run
3. Schema is applied via Alembic migrations
4. Database is dropped after tests complete

### Test Isolation

- Each test gets a fresh database transaction
- Changes are rolled back after each test
- Parallel tests use separate databases

### Test Data

Test fixtures provide:
- Temporary file storage (auto-cleaned)
- Sample categories, videos, workers
- Pre-configured API clients

## Coverage Exclusions

The following are excluded from coverage:

- Test files (`tests/*`)
- Database migrations (`migrations/*`)
- `__init__.py` files
- Abstract methods
- Debug code (`if __name__ == "__main__"`)

See `pyproject.toml` `[tool.coverage.report]` for complete list.

## Troubleshooting

### PostgreSQL Connection Issues

```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Set correct credentials
export VLOG_TEST_PG_USER=vlog
export VLOG_TEST_PG_PASSWORD=vlog_password

# Create test user if needed
psql -c "CREATE USER vlog WITH PASSWORD 'vlog_password';"
psql -c "ALTER USER vlog CREATEDB;"
```

### Import Errors

```bash
# Install package in development mode
pip install -e .

# Verify installation
python -c "import api; import worker; import cli; print('OK')"
```

### Fixture Errors

```bash
# Check conftest.py is loaded
pytest --fixtures tests/

# Verify test database setup
VLOG_TEST_MODE=1 pytest tests/test_database_compatibility.py -v
```

### Slow Tests

```bash
# Skip slow tests during development
pytest -m "not slow"

# Run only fast tests
pytest -m "not slow and not integration"
```

## Best Practices

1. **Test Independence**: Each test should be independent and not rely on other tests
2. **Clean Up**: Use fixtures for setup/teardown, don't leave test artifacts
3. **Descriptive Names**: Test names should clearly describe what they test
4. **Arrange-Act-Assert**: Follow AAA pattern for test structure
5. **Mock External Services**: Don't call external APIs in tests
6. **Test Edge Cases**: Test boundary conditions, errors, and unusual inputs
7. **Keep Tests Fast**: Avoid unnecessary delays, use mocks when possible
8. **Document Intent**: Use docstrings to explain what the test verifies

## Contributing

When adding new features:

1. Write tests for new functionality
2. Update existing tests if behavior changes
3. Ensure all tests pass locally
4. Check coverage hasn't decreased
5. Run linter before committing

See `CONTRIBUTING.md` for more details on the contribution process.
