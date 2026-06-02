---
type: plan
source-issue: 1084
repo: amd/gaia
title: "Email connector requires Agent UI for OAuth config; gaia connectors configure google accepts no flags"
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 3
test_command: ".venv/bin/python -m pytest tests/unit/test_email_cli.py tests/unit/connectors/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1084-connector-cli-flags
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1084 — Self-contained CLI OAuth-client config for the Google connector

## Problem
`gaia connectors configure google` only takes a generic `--set KEY=VALUE` / `--json`
dispatcher. There is no `--client-id` / `--client-secret`, so new users onboarding the
email connector are bounced to the Agent UI (or told env vars are a "Power users" path).
The CLI's most prominently named connector command cannot configure the thing it names.

## Resolution
Add first-class `--client-id` / `--client-secret` flags to `gaia connectors configure`.
When supplied, persist the OAuth *client* credentials to the keyring via the existing
`store.save_provider_credentials(...)` — the exact store `GoogleOAuthProvider.__init__`
reads from (`store.peek_provider_credentials("google")`). This completes OAuth *config*
(client id/secret) without the Agent UI, leaving the actual interactive login to a
separate `gaia connectors connect google` step (the live OAuth flow excluded from this
issue's AC).

### Why persist-only (no auto `connect`)
`OAuthPkceHandler.configure` always calls `start_authorization` after saving creds, which
launches a browser + a loopback callback server. That is the live-network OAuth step the
AC explicitly excludes ("Mock/avoid live network"). So the `--client-id/--client-secret`
path in the CLI persists credentials and prints the next-step hint, rather than routing
through the handler's flow-starting `configure`. The generic `--set`/`--json` dispatcher
path is unchanged (still goes through `handler.configure`).

### Fail-loudly
`--client-id` requires `--client-secret` and vice-versa (Google rejects token requests
that omit the secret even for Desktop PKCE clients). Supplying one without the other is a
usage error (exit 2). Mixing `--client-id` with `--set`/`--json` is rejected (exit 2) to
avoid ambiguous double-writes.

## Files (lane: cli.py + handler.py + email.mdx + tests)
- `src/gaia/connectors/cli.py` — add `--client-id` / `--client-secret` to the `configure`
  subparser; branch `_handle_configure` to a credential-persist path when they are set.
- `src/gaia/connectors/handler.py` — (likely no change needed; persistence goes through
  `store.save_provider_credentials`. Kept in lane in case a thin helper is cleaner.)
- `docs/guides/email.mdx` — add a "Configure via CLI (no Agent UI)" subsection under the
  CLI tab of "Connect your Google account", surfacing the runbook link.
- `tests/unit/connectors/test_cli.py` — FAILING-first tests: invoking `configure google
  --client-id X --client-secret Y` persists creds to the store the provider resolves from
  (assert via `peek_provider_credentials` / a real provider re-read on the in-memory
  keyring), no network call; one-flag-only is a usage error; mixing with `--set` errors.

## Lane boundary
OWN: cli.py, handler.py, email.mdx, tests. Do NOT touch providers/ or catalog/ (issue
#1105 owns Microsoft provider there). Persistence is wired via the existing store API.

## TDD
1. RED: add tests in `test_cli.py` for the new flags → fail (argparse rejects unknown flag).
2. GREEN: add flags + persist branch → tests pass.
3. Docs: email.mdx CLI-config subsection + runbook link.

## Live real-world recipe (for the orchestrator, NOT part of unit AC)
Requires a real Google Cloud OAuth *Desktop-app* client id + secret and an interactive
browser login:
1. `gaia connectors configure google --client-id <REAL_ID> --client-secret <REAL_SECRET>`
2. `gaia connectors connect google` → open the printed URL, complete consent in a browser.
3. `gaia connectors test google` → expect `OK token_valid`.
