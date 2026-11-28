#!/bin/bash

# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# GAIA - Claude Code design reminder for UI edits
#
# A PostToolUse hook wired up in .claude/settings.json. The first time a session
# edits a screen people use (the Agent UI at any width, its Electron shell, or
# the TUI), it reminds Claude to check the change against the ui-ux-pro-max
# skill and GAIA's design specs. It says so once per session and is silent for
# tests and every other file.

payload=$(cat)

# One flat JSON object from Claude Code; pull the two strings this needs.
field() {
    printf '%s' "$payload" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" | head -n 1
}

file_path=$(field file_path | sed 's#\\\\#/#g; s#\\#/#g')
session_id=$(field session_id | tr -cd 'A-Za-z0-9_-')

case "$file_path" in
    */__tests__/* | *.test.* | *_test.go | */visual/* | */tui/test/*) exit 0 ;;
    */src/gaia/apps/webui/src/* | */src/gaia/apps/webui/main.cjs | */src/gaia/apps/webui/services/*) ;;
    */tui/internal/ui/*) ;;
    *) exit 0 ;;
esac

if [ -n "$session_id" ]; then
    marker="${TMPDIR:-/tmp}/gaia-ui-design-reminder-$session_id"
    [ -e "$marker" ] && exit 0
    : > "$marker"
fi

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"This edit changes a screen people use. Before calling it done, search the ui-ux-pro-max skill's rules for the pattern you changed (python .claude/skills/ui-ux-pro-max/scripts/search.py \"<pattern>\" --domain ux) and check the result against them. GAIA's own specs win where they disagree: docs/spec/gaia-design-language.mdx, plus docs/spec/mobile-design-system.md at phone width. For the Agent UI, also follow the reviewing-agent-ui skill; for the TUI, look at it running with the driving-the-tui skill."}}
EOF
