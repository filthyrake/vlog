#!/bin/bash
# Start only the admin server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Admin doesn't need proxy headers since it's internal-only
exec python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001
