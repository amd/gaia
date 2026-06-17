---
type: plan
source-issue: 1336
repo: amd/gaia
title: "feat(agent-ui): let users roll back to a specific previous release"
created: 2026-06-17
status: in-progress
work_type: code-feature
complexity: complex
tdd_required: true
suggested_team_size: 2
estimated_files_changed: 11
test_command: "cd tests/electron && npm install && npm test"
build_command: "cd src/gaia/apps/webui && npm install && npm run build"
branch: feat/issue-1336-version-rollback
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1336 — In-app rollback to a previous release (Agent UI)

## User impact
A bad desktop update currently leaves a user stuck on latest (their only escape is hunting an old installer on GitHub). This adds an in-app path — Settings → About → "Roll back to a previous version" — that lists published releases, lets the user pick an older one, confirms the downgrade, downloads it, and restarts into it. After a manual rollback the app PAUSES auto-update (pins the chosen version) so the next scheduled check can't silently re-upgrade past it; the user explicitly Resumes updates when ready.

## Acceptance criteria
- AC1: A user can open the app, see the list of releases, pick an older one, and end up running it after a restart.
- AC2: After a manual rollback, an auto-update check does NOT silently re-upgrade past the pinned version.
- AC3: Works on the platforms the installer targets (Windows NSIS at minimum; note any platform gaps).

## Real-world recipe (orchestrator-run, post-handoff)
- Feasible automated slice: build the renderer, launch the Agent UI, drive Settings → About → Roll back via the preview/Playwright tooling. Confirm the picker lists REAL GAIA releases from api.github.com/repos/amd/gaia/releases, the installed version is marked, selecting an older version shows the downgrade confirmation. Capture screenshots + the network call. Do NOT perform the irreversible install in this slice.
- Manual gate: on a packaged Windows NSIS install with ≥2 published releases — roll back to a prior release, restart, confirm the older version runs (AC1), then confirm the next auto-check does not silently re-upgrade (AC2). Note mac-signing and Linux-.deb gaps (AC3).
