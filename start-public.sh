#!/bin/bash
# Start only the public-facing server (for production behind nginx/reverse proxy)

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
if ! python -c "from api.public import app" 2>/dev/null; then
    echo "Error: Failed to import api.public module"
    echo "Ensure the package is installed: pip install -e ."
    exit 1
fi

# --proxy-headers: Trust X-Forwarded-Proto, X-Forwarded-For headers
# --forwarded-allow-ips: Accept forwarded headers from any IP (your pfSense/proxy)
exec python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='*'
