#!/bin/bash
# Start only the transcription worker

set -e  # Exit on error
set -u  # Exit on undefined variable

# Ensure we're in the project root regardless of where script was called from
# This is required because we use relative paths for venv and worker scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# Validate virtual environment exists
if [[ ! -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
    echo "Error: Virtual environment not found at $SCRIPT_DIR/venv"
    echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -e ."
    exit 1
fi

# Activate virtual environment with error capture
activation_output=$(source venv/bin/activate 2>&1) || {
    echo "Error: Failed to activate virtual environment"
    echo "Details: $activation_output"
    echo "Check permissions and virtual environment integrity"
    exit 1
}

# Validate transcription worker script exists
if [[ ! -f "$SCRIPT_DIR/worker/transcription.py" ]]; then
    echo "Error: Transcription script not found at $SCRIPT_DIR/worker/transcription.py"
    exit 1
fi

# Verify required modules are available
import_error=$(python -c "from worker.transcription import main" 2>&1) || {
    echo "Error: worker.transcription module not available for import"
    echo "Details: $import_error"
    echo "Ensure the package is installed: pip install -e ."
    exit 1
}

# Verify whisper module is installed
whisper_error=$(python -c "import whisper" 2>&1) || {
    echo "Error: whisper module not installed"
    echo "Details: $whisper_error"
    echo "Install with: pip install openai-whisper"
    exit 1
}

exec python worker/transcription.py "$@"
