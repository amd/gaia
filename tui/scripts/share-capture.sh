#!/usr/bin/env bash
# Upload a TUI capture to Google Drive and print a shareable link.
#
# Screenshots go straight into a conversation, but a video does not: the
# connectors that can reach Drive take file content inlined, which for a 600KB
# clip means about 800KB of base64 through the conversation. rclone talks to
# Drive directly, so a capture becomes a link in one command and nothing large
# passes through the model.
#
#   share-capture.sh session.mp4 [more.jpg ...]
#
# One-time setup (you run this — it opens a browser so YOU grant the access,
# and the token lands in your rclone config, never in a transcript):
#
#   rclone config create gaia-drive drive scope=drive
#
# Then create the destination folder once, if it isn't there:
#
#   rclone mkdir "gaia-drive:GAIA TUI Captures"
set -euo pipefail

REMOTE="${GAIA_CAPTURE_REMOTE:-gaia-drive}"
FOLDER="${GAIA_CAPTURE_FOLDER:-GAIA TUI Captures}"
RCLONE="${RCLONE:-rclone}"

if ! command -v "$RCLONE" >/dev/null 2>&1; then
    echo "share-capture: rclone is not on PATH." >&2
    echo "  install: curl -sL https://downloads.rclone.org/rclone-current-osx-arm64.zip -o rclone.zip" >&2
    echo "           (or your platform's build from https://rclone.org/downloads/)" >&2
    exit 1
fi

if ! "$RCLONE" listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
    echo "share-capture: no rclone remote named '${REMOTE}'." >&2
    echo "  set it up once, in your own shell:" >&2
    echo "    rclone config create ${REMOTE} drive scope=drive" >&2
    echo "  it opens a browser so you grant the access yourself." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    echo "usage: share-capture.sh FILE [FILE ...]" >&2
    exit 2
fi

for f in "$@"; do
    if [ ! -f "$f" ]; then
        echo "share-capture: no such file: $f" >&2
        exit 1
    fi
done

# Created rather than assumed: a typo'd folder name would otherwise upload into
# a new one silently and the link would point somewhere nobody is looking.
"$RCLONE" mkdir "${REMOTE}:${FOLDER}" 2>/dev/null || true

for f in "$@"; do
    name="$(basename "$f")"
    "$RCLONE" copyto "$f" "${REMOTE}:${FOLDER}/${name}" --progress --stats-one-line
    # A link, not just a successful upload: the point of the whole exercise is
    # something that can be pasted into a review or a demo.
    link="$("$RCLONE" link "${REMOTE}:${FOLDER}/${name}" 2>/dev/null || true)"
    if [ -n "$link" ]; then
        printf '%s  %s\n' "$name" "$link"
    else
        printf '%s  uploaded (no link: the account may not allow link sharing)\n' "$name"
    fi
done
