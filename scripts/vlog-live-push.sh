#!/bin/bash
#
# vlog-live-push.sh - Secure FFmpeg live stream push script
#
# Usage:
#   VLOG_STREAM_KEY=sk_live_xxx ./vlog-live-push.sh <stream-slug> <quality> [input-source]
#
# Examples:
#   # Stream webcam at 720p
#   VLOG_STREAM_KEY=sk_live_xxx ./vlog-live-push.sh my-stream 720p
#
#   # Stream specific video device at 1080p
#   VLOG_STREAM_KEY=sk_live_xxx ./vlog-live-push.sh my-stream 1080p /dev/video1
#
#   # Stream a file as live source (for testing)
#   VLOG_STREAM_KEY=sk_live_xxx ./vlog-live-push.sh my-stream 720p test.mp4
#
# Environment Variables:
#   VLOG_STREAM_KEY   - Required. The stream key from vlog admin API
#   VLOG_API_URL      - Optional. API URL (default: http://localhost:9000)
#   VLOG_SEGMENT_TIME - Optional. Segment duration in seconds (default: 4)
#
# Security Notes:
#   - Stream key is read from environment variable (not visible in `ps aux`)
#   - Auth header is written to a temp file with chmod 600
#   - Temp file is cleaned up on exit
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check required arguments
if [ $# -lt 2 ]; then
    echo -e "${RED}Usage: VLOG_STREAM_KEY=xxx $0 <stream-slug> <quality> [input-source]${NC}"
    echo ""
    echo "Arguments:"
    echo "  stream-slug  - The stream slug (from admin API)"
    echo "  quality      - Quality preset: 2160p, 1440p, 1080p, 720p, 480p, 360p"
    echo "  input-source - Optional: video device or file (default: /dev/video0)"
    echo ""
    echo "Environment:"
    echo "  VLOG_STREAM_KEY   - Required: Stream key from admin API"
    echo "  VLOG_API_URL      - Optional: API URL (default: http://localhost:9000)"
    echo "  VLOG_SEGMENT_TIME - Optional: Segment duration (default: 4)"
    exit 1
fi

STREAM_SLUG="$1"
QUALITY="$2"
INPUT_SOURCE="${3:-/dev/video0}"

# Validate stream key
if [ -z "$VLOG_STREAM_KEY" ]; then
    echo -e "${RED}Error: VLOG_STREAM_KEY environment variable is required${NC}"
    echo "Get a stream key from: POST /api/v1/live/streams"
    exit 1
fi

# Configuration
API_URL="${VLOG_API_URL:-http://localhost:9000}"
SEGMENT_TIME="${VLOG_SEGMENT_TIME:-4}"

# Quality presets (resolution, video bitrate, audio bitrate, preset)
declare -A QUALITY_RESOLUTION=(
    ["2160p"]="3840x2160"
    ["1440p"]="2560x1440"
    ["1080p"]="1920x1080"
    ["720p"]="1280x720"
    ["480p"]="854x480"
    ["360p"]="640x360"
)

declare -A QUALITY_VIDEO_BITRATE=(
    ["2160p"]="15000k"
    ["1440p"]="8000k"
    ["1080p"]="5000k"
    ["720p"]="2500k"
    ["480p"]="1000k"
    ["360p"]="600k"
)

declare -A QUALITY_AUDIO_BITRATE=(
    ["2160p"]="192k"
    ["1440p"]="192k"
    ["1080p"]="128k"
    ["720p"]="128k"
    ["480p"]="96k"
    ["360p"]="96k"
)

# Validate quality
if [ -z "${QUALITY_RESOLUTION[$QUALITY]}" ]; then
    echo -e "${RED}Error: Invalid quality '$QUALITY'${NC}"
    echo "Valid qualities: 2160p, 1440p, 1080p, 720p, 480p, 360p"
    exit 1
fi

RESOLUTION="${QUALITY_RESOLUTION[$QUALITY]}"
VIDEO_BITRATE="${QUALITY_VIDEO_BITRATE[$QUALITY]}"
AUDIO_BITRATE="${QUALITY_AUDIO_BITRATE[$QUALITY]}"

# Create secure temp file for auth header
HEADER_FILE=$(mktemp)
chmod 600 "$HEADER_FILE"
echo "Authorization: Bearer $VLOG_STREAM_KEY" > "$HEADER_FILE"

# Cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    rm -f "$HEADER_FILE"
}
trap cleanup EXIT INT TERM

# Check if input source exists
# Use arrays for FFmpeg options to properly handle arguments with spaces
INPUT_OPTS=()
if [[ "$INPUT_SOURCE" == /dev/* ]]; then
    if [ ! -e "$INPUT_SOURCE" ]; then
        echo -e "${RED}Error: Video device $INPUT_SOURCE not found${NC}"
        echo "Available video devices:"
        ls -la /dev/video* 2>/dev/null || echo "  (none found)"
        exit 1
    fi
    INPUT_OPTS=(-f v4l2 -framerate 30 -video_size "$RESOLUTION" -i "$INPUT_SOURCE")
    # Add audio input from default device
    if command -v arecord &>/dev/null; then
        INPUT_OPTS+=(-f alsa -i default)
    fi
elif [ -f "$INPUT_SOURCE" ]; then
    INPUT_OPTS=(-re -i "$INPUT_SOURCE")
else
    echo -e "${RED}Error: Input source '$INPUT_SOURCE' not found${NC}"
    exit 1
fi

# Build ingest URL
INGEST_URL="${API_URL}/api/live/ingest/${STREAM_SLUG}/${QUALITY}"

echo -e "${GREEN}Starting live stream...${NC}"
echo "  Stream: $STREAM_SLUG"
echo "  Quality: $QUALITY ($RESOLUTION @ $VIDEO_BITRATE)"
echo "  Input: $INPUT_SOURCE"
echo "  Target: $INGEST_URL"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop streaming${NC}"
echo ""

# Retry loop with exponential backoff
MAX_RETRIES=10
RETRY_COUNT=0
RETRY_DELAY=5

while true; do
    # Run FFmpeg
    # Using CMAF (fMP4) for better compatibility with HTTP segment push
    ffmpeg \
        "${INPUT_OPTS[@]}" \
        -c:v libx264 \
        -preset fast \
        -tune zerolatency \
        -b:v "$VIDEO_BITRATE" \
        -maxrate "$VIDEO_BITRATE" \
        -bufsize "${VIDEO_BITRATE}" \
        -g 60 \
        -keyint_min 60 \
        -sc_threshold 0 \
        -c:a aac \
        -b:a "$AUDIO_BITRATE" \
        -ar 44100 \
        -f hls \
        -hls_time "$SEGMENT_TIME" \
        -hls_list_size 0 \
        -hls_segment_type fmp4 \
        -hls_fmp4_init_filename "init.mp4" \
        -hls_segment_filename "seg_%04d.m4s" \
        -method PUT \
        -headers "@$HEADER_FILE" \
        -http_persistent 1 \
        "$INGEST_URL/stream.m3u8" \
        2>&1 | while read -r line; do
            # Log FFmpeg output with timestamp
            echo "[$(date '+%H:%M:%S')] $line"
        done

    EXIT_CODE=${PIPESTATUS[0]}

    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}Stream ended normally${NC}"
        break
    fi

    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "${RED}Max retries ($MAX_RETRIES) exceeded, giving up${NC}"
        exit 1
    fi

    echo -e "${YELLOW}FFmpeg exited with code $EXIT_CODE, retrying in $RETRY_DELAY seconds (attempt $RETRY_COUNT/$MAX_RETRIES)${NC}"
    sleep $RETRY_DELAY

    # Exponential backoff (max 60 seconds)
    RETRY_DELAY=$((RETRY_DELAY * 2))
    if [ $RETRY_DELAY -gt 60 ]; then
        RETRY_DELAY=60
    fi
done

echo -e "${GREEN}Live stream complete${NC}"
