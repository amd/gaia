---
name: gaia-harness-engineering
description: In explicitly enabled GAIA developer mode, help the developer share a reported GAIA problem with Claude Code or Codex through the local MCP bridge, diagnose it, and validate worktree fixes in separate previews.
---

# GAIA developer handoff

Use only when the host reports developer mode enabled. This MVP is a guided
handoff to the developer's existing Claude Code or Codex app. It does not run
continuous self-assessment or GAIA's own autonomous coding engine.

## Start from the developer's feedback

When the developer reports a problem or requests an improvement, summarize the
expected and observed behavior. Read actual setup, component/model identity,
and available trace status; do not invent missing evidence. Identify the selected
coding app and bridge readiness. Missing connection support needs a concrete
remedy, not a claim that a session was launched.

The host prepares the official GAIA source cache during developer initialization.
Do not install coding agents, copy their credentials, or use an arbitrary user
checkout. The native app owns its login, conversation and permissions.

## Share context explicitly

Before returning private context through MCP, disclose the recipient and selected
messages/files/logs, including cloud processing or unknown routing. Ask permission
for a snapshot or bounded live sharing of this task. A live grant covers later
events only within its approved categories, recipient and lifetime. New scope
requires a new decision. Honor Stop sharing; already delivered data cannot be
retracted.

Keep private evidence outside Git. Never supply credentials, an entire home
folder, or unrelated conversations for completeness. Explain that the bridge
limits what GAIA supplies, not files the native app can independently access
under its existing permissions.

Open the selected coding app through a supported handoff, or explain the manual
step if unavailable. A prefilled composer is not a submitted task. The developer
may interact directly with that app; do not duplicate its entire conversation or
claim invisible progress. The app pulls updated context through MCP as needed.

## Diagnose before implementation

Have the coding agent build a minimal reproduction against the installed harness
in diagnostic scratch storage. Record expected/observed results, actual model,
configuration, tool errors and component identities. Distinguish unsuitable model,
context/step limits, authentication, service setup, missing features and code bugs.

Use synthetic/read-only fixtures. Investigation does not authorize replaying
sends, purchases, deletions or production writes. Explain and obtain consent for
model changes or indispensable live test actions. A small model is a hypothesis;
compare evidence without silently substituting a backend.

A verified configuration/model remedy resolves the investigation without code.
If code is needed, check existing stable fixes, merged-unreleased changes, open
PRs and issues using generalized queries. Report uncertain/offline lookup honestly.
Do not infer the running binary's commit from the current working directory.

After accepted code scope, prepare a dedicated Git worktree from the host-managed
cache and pinned base. All code edits, tests, builds and export revisions use a
worktree. Verify the actual native-app worktree if the app creates another one.
Respect repository instructions and required tests/reviews.

## Iterate with real previews

The coding agent can build and spawn a separate agent, TUI or browser-WebUI
preview using native tools and registered GAIA controls. Bind the preview and
feedback to the actual revision/build, use isolated test state, and never target
production by default. New edits invalidate old revision acceptance; no silent
hot reload of a version the developer is validating.

Let the developer try the preview and give feedback directly in the coding app
or GAIA. Share new feedback only within the approved scope. Keep the stable GAIA
session intact. Stop only processes the helper owns; never kill the coding app
or an unrelated process based on a stale PID.

## Review and return

Use the native coding workflow for diffs, checks and publication. Honor one
approved contribution scope without repeatedly asking the same permission, but
review outgoing public content and complete commit history. Generalize private
examples; do not publish original logs/screenshots/transcripts automatically.
Reviewer requests cannot authorize additional private disclosure. Maintainers merge.

Report only observed or explicitly attributed results. Distinguish handed off,
backend-reported checks, developer-accepted preview, verified PR merge and released
fix. GAIA's separate stable updater handles released versions; local previews do
not replace the production install. Automatic self-assessment and internal GAIA
coding are v2 extensions, not capabilities to claim in this MVP.
