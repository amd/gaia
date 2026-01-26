#!/bin/bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Compress and convert video recordings for documentation
# Usage: ./compress-video.sh <input> [output] [quality] [format]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m'

step() { echo -e "  ${CYAN}→${NC} $1"; }
success() { echo -e "  ${GREEN}✓${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }

# Help
if [[ "$1" == "-h" || "$1" == "--help" || -z "$1" ]]; then
    echo ""
    echo -e "  ${CYAN}Video Compression Script for GAIA Documentation${NC}"
    echo -e "  ${CYAN}================================================${NC}"
    echo ""
    echo "  Usage:"
    echo "    ./compress-video.sh <input> [output] [quality] [format]"
    echo ""
    echo "  Arguments:"
    echo "    input     Input video file (required)"
    echo "    output    Output filename (default: input-compressed.ext)"
    echo "    quality   CRF value 18-35, lower=better (default: 24)"
    echo "    format    mp4 or webm (default: mp4)"
    echo ""
    echo "  Examples:"
    echo "    ./compress-video.sh demo.mp4"
    echo "    ./compress-video.sh demo.mp4 output.mp4 20"
    echo "    ./compress-video.sh demo.mp4 output.webm 24 webm"
    echo ""
    exit 0
fi

INPUT="$1"
QUALITY="${3:-24}"
FORMAT="${4:-mp4}"

# Generate output name
if [[ -z "$2" ]]; then
    BASENAME="${INPUT%.*}"
    OUTPUT="${BASENAME}-compressed.${FORMAT}"
else
    OUTPUT="$2"
fi

# Check ffmpeg
step "Checking ffmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    err "ffmpeg not found"
    echo "  Install with: sudo apt install ffmpeg"
    exit 1
fi
success "ffmpeg found"

# Check input
if [[ ! -f "$INPUT" ]]; then
    err "Input file not found: $INPUT"
    exit 1
fi

INPUT_SIZE=$(du -h "$INPUT" | cut -f1)
step "Input: $INPUT ($INPUT_SIZE)"

# Build ffmpeg command
if [[ "$FORMAT" == "mp4" ]]; then
    CODEC_ARGS="-c:v libx264 -preset slow -crf $QUALITY -c:a aac -b:a 128k -movflags +faststart"
else
    CODEC_ARGS="-c:v libvpx-vp9 -crf $QUALITY -b:v 0 -c:a libopus -b:a 128k"
fi

step "Compressing (CRF: $QUALITY, Format: $FORMAT)..."
echo -e "  ${GRAY}This may take a while...${NC}"
echo ""

ffmpeg -i "$INPUT" -y $CODEC_ARGS "$OUTPUT"

if [[ -f "$OUTPUT" ]]; then
    OUTPUT_SIZE=$(du -h "$OUTPUT" | cut -f1)
    echo ""
    success "Compression complete!"
    echo ""
    echo "  Output: $OUTPUT"
    echo -e "  ${GRAY}Size: $OUTPUT_SIZE${NC}"
    echo ""
    echo "  Upload to R2:"
    echo -e "  ${CYAN}rclone copy $OUTPUT r2:gaia-docs-assets/videos/${NC}"
    echo ""
else
    echo ""
    err "Compression failed"
    exit 1
fi
