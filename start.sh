#!/bin/bash
# Start all VLog services

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

# Verify Python and required modules are available
if ! python -c "from api.database import create_tables" 2>/dev/null; then
    echo "Error: Failed to import api.database module"
    echo "Ensure the package is installed: pip install -e ."
    exit 1
fi

# Initialize database
echo "Initializing database..."
if ! python -c "from api.database import create_tables; create_tables()"; then
    echo "Error: Database initialization failed"
    exit 1
fi

echo "Starting VLog services..."
echo "  Public site: http://localhost:9000"
echo "  Admin panel: http://localhost:9001"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Start all services in background, but trap Ctrl+C to kill them all
cleanup() {
    echo ""
    echo "Shutting down services..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "All services stopped."
    exit 0
}
trap cleanup INT TERM

# Start public server (with proxy headers support for reverse proxy)
python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='*' &
PUBLIC_PID=$!

# Brief pause to check if it started
sleep 1
if ! kill -0 $PUBLIC_PID 2>/dev/null; then
    echo "Error: Public API failed to start"
    cleanup
fi

# Start admin server
python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001 --proxy-headers --forwarded-allow-ips='*' &
ADMIN_PID=$!

sleep 1
if ! kill -0 $ADMIN_PID 2>/dev/null; then
    echo "Error: Admin API failed to start"
    cleanup
fi

# Start transcoding worker
python worker/transcoder.py &
WORKER_PID=$!

sleep 1
if ! kill -0 $WORKER_PID 2>/dev/null; then
    echo "Error: Transcoding worker failed to start"
    cleanup
fi

echo "Services started:"
echo "  Public API PID: $PUBLIC_PID"
echo "  Admin API PID: $ADMIN_PID"
echo "  Worker PID: $WORKER_PID"
echo ""

# Wait for all background processes
wait
