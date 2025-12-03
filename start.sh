#!/bin/bash
# Start all VLog services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Initialize database
python -c "from api.database import create_tables; create_tables()"

echo "Starting VLog services..."
echo "  Public site: http://localhost:9000"
echo "  Admin panel: http://localhost:9001"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Start all services in background, but trap Ctrl+C to kill them all
trap 'kill $(jobs -p) 2>/dev/null; exit' INT TERM

# Start public server (with proxy headers support for reverse proxy)
python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='*' &
PUBLIC_PID=$!

# Start admin server
python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001 --proxy-headers --forwarded-allow-ips='*' &
ADMIN_PID=$!

# Start transcoding worker
python worker/transcoder.py &
WORKER_PID=$!

echo "Services started:"
echo "  Public API PID: $PUBLIC_PID"
echo "  Admin API PID: $ADMIN_PID"
echo "  Worker PID: $WORKER_PID"
echo ""

# Wait for all background processes
wait
