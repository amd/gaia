#!/usr/bin/env bash
# Verifies refresh-context7 exit codes for each HTTP status class.
# Mirrors the decision tree in .github/workflows/publish.yml (job: refresh-context7).
# Update this script in lockstep with that step when the status-code policy changes.
set -u

run() {
  HTTP_STATUS="$1" bash -c '
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "202" ]; then
      echo "ok"; exit 0
    elif [ "$HTTP_STATUS" = "429" ]; then
      echo "::warning::rate limited"; exit 0
    else
      echo "::error::Context7 refresh returned HTTP $HTTP_STATUS"; exit 1
    fi'
  echo "rc=$?"
}

expect() {
  output="$(run "$1")"
  rc_line="$(echo "$output" | tail -1)"
  if [ "$rc_line" != "rc=$2" ]; then
    echo "FAIL $1: expected rc=$2 got $rc_line"
    echo "$output"
    exit 1
  fi
  # Optional 3rd arg: substring that must appear in the output (e.g. "::error::").
  # Exit code alone doesn't catch a regression that silently downgrades an
  # ::error:: to a plain echo, so the annotation check is the AC2/AC4 anchor.
  if [ -n "${3:-}" ] && ! printf '%s\n' "$output" | grep -qF "$3"; then
    echo "FAIL $1: expected annotation '$3' missing"
    echo "$output"
    exit 1
  fi
  echo "PASS $1 -> $rc_line${3:+ (annotation matched: $3)}"
}

expect 200 0
expect 202 0
expect 400 1 "::error::"   # the bug — must now fail loudly with annotation
expect 429 0 "::warning::" # tolerated transient — must still annotate
expect 500 1 "::error::"
expect 000 1 "::error::"   # curl network-error sentinel; only reachable if -e is off (harness only)
