#!/bin/bash
# Claude Code PreToolUse hook: require user confirmation on git commit
# that no vendor/OEM partner names are in staged changes or commit message.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('input',{}).get('command',''))" 2>/dev/null)

if echo "$COMMAND" | grep -q "git commit"; then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "VENDOR CHECK: Please confirm that no vendor or OEM partner names appear in the staged changes or commit message. Use generic terms (oem, vendor, partner) instead of specific company names. Press Allow to proceed or Deny to abort."
  }
}
EOF
  exit 0
fi
