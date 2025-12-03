#!/bin/bash
# Start only the public-facing server (for production behind nginx/reverse proxy)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

# --proxy-headers: Trust X-Forwarded-Proto, X-Forwarded-For headers
# --forwarded-allow-ips: Accept forwarded headers from any IP (your pfSense/proxy)
exec python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='*'
