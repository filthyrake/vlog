#!/bin/bash
# Start only the transcoding worker

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

# Validate worker script exists
if [[ ! -f "$SCRIPT_DIR/worker/transcoder.py" ]]; then
    echo "Error: Transcoder script not found at $SCRIPT_DIR/worker/transcoder.py"
    exit 1
fi

# Verify required modules are available
if ! python -c "from worker.transcoder import worker_loop" 2>/dev/null; then
    echo "Error: Failed to import worker.transcoder module"
    echo "Ensure the package is installed: pip install -e ."
    exit 1
fi

exec python worker/transcoder.py
