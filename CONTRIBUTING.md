# Contributing to VLog

Thank you for your interest in contributing to VLog! This document provides guidelines and instructions for contributing.

## Branching Strategy

VLog uses a two-branch development model:

| Branch | Purpose |
|--------|---------|
| `dev` | Active development (default branch) |
| `main` | Stable releases only |

### Workflow

1. **Feature development**: Create branches from `dev`, submit PRs to `dev`
2. **Quick feedback**: PRs to `dev` run a fast test suite (skips slow integration tests)
3. **Releases**: Periodically, `dev` is merged to `main` after full test suite passes
4. **Hotfixes**: Critical fixes can go directly to `main` if needed

### Which branch should I target?

- **Most contributions**: Target `dev` (the default)
- **Documentation-only changes**: Either `dev` or `main` is fine
- **Critical security fixes**: Discuss with maintainers first

## Getting Started

### Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/filthyrake/vlog.git
   cd vlog
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install in development mode**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Initialize the database**
   ```bash
   python api/database.py
   ```

5. **Start development servers**
   ```bash
   ./start.sh
   ```

### Running Tests

Tests require `VLOG_TEST_MODE=1` to avoid NAS directory creation:

```bash
# Run all tests
VLOG_TEST_MODE=1 pytest

# Run with coverage
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker --cov=cli

# Run specific test file
VLOG_TEST_MODE=1 pytest tests/test_public_api.py

# Run tests matching pattern
VLOG_TEST_MODE=1 pytest -k "test_upload"
```

### Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting:

```bash
# Check for issues
VLOG_TEST_MODE=1 ruff check api/ worker/ cli/ tests/ config.py

# Auto-format code
ruff format api/ worker/ cli/ tests/ config.py
```

## How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/filthyrake/vlog/issues) to avoid duplicates
2. Use the bug report template when creating a new issue
3. Include:
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python version, etc.)
   - Relevant logs

### Suggesting Features

1. Check existing issues and discussions first
2. Use the feature request template
3. Describe the use case and why it would be valuable

### Submitting Pull Requests

1. **Fork and branch from `dev`**
   ```bash
   git checkout dev
   git pull origin dev
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Follow existing code style
   - Add tests for new functionality
   - Update documentation as needed

3. **Test your changes**
   ```bash
   # Run quick tests (same as CI for dev branch)
   VLOG_TEST_MODE=1 pytest \
     --ignore=tests/test_workflows.py \
     --ignore=tests/test_worker_integration.py \
     --ignore=tests/test_transcoder_integration.py \
     --ignore=tests/test_e2e_upload.py \
     --ignore=tests/test_transcoder.py

   # Run linting
   VLOG_TEST_MODE=1 ruff check api/ worker/ cli/ tests/ config.py

   # Optional: Run full test suite
   VLOG_TEST_MODE=1 pytest
   ```

4. **Commit with clear messages**
   ```bash
   git commit -m "Add feature: brief description"
   ```

5. **Push and create PR targeting `dev`**
   - PRs automatically target `dev` (the default branch)
   - Reference any related issues
   - Describe what your PR does and why

### Pull Request Guidelines

- Keep PRs focused on a single change
- Ensure all tests pass
- Ensure linting passes
- Update documentation for user-facing changes
- Add tests for new features or bug fixes

## Code Guidelines

### Python Version

This project uses Python 3.9+. Use `Optional[T]` instead of `T | None` union syntax for compatibility.

### Project Structure

- `api/` - FastAPI backend (public, admin, worker APIs)
- `worker/` - Background transcoding workers
- `cli/` - Command-line interface
- `web/` - Frontend (Alpine.js + Tailwind CSS)
- `tests/` - pytest test suite
- `docs/` - Documentation

### Key Patterns

- **Async/await**: All database operations are async
- **Pydantic models**: Request/response validation in `api/schemas.py`
- **SQLAlchemy**: Database operations in `api/database.py`
- **Environment config**: All settings configurable via `VLOG_*` env vars

## Getting Help

- Read the [documentation](docs/)
- Check existing [issues](https://github.com/filthyrake/vlog/issues)
- Open a new issue for questions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
