#!/bin/bash
# Start only the admin server

set -e  # Exit on error
set -u  # Exit on undefined variable

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Validate virtual environment exists
if [[ ! -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
    echo "Error: Virtual environment not found at $SCRIPT_DIR/venv"
    echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -e ."
    exit 1
fi

source venv/bin/activate || {
    echo "Error: Failed to activate virtual environment"
    exit 1
}

# Verify required module is available
if ! python -c "from api.admin import app" 2>/dev/null; then
    echo "Error: Failed to import api.admin module"
    echo "Ensure the package is installed: pip install -e ."
    exit 1
fi

# Admin doesn't need proxy headers since it's internal-only
exec python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001
