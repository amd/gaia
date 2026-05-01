---
name: "gaia-release"
description: "Cut a GAIA release end-to-end: draft notes, open release PR, run pre-tag verification, push the tag, monitor the publish pipeline, and produce the Discord announcement. Use when the user asks to 'cut a release', 'release vX.Y.Z', 'tag a release', or 'publish v...'. Pauses at every irreversible step for user approval."
---

# GAIA Release

Run a GAIA release end-to-end against the `amd/gaia` repo. The skill is a **phased checklist with hard gates** — each phase produces a concrete artifact, then stops and asks for confirmation before doing anything irreversible (opening a PR, pushing a tag, rerunning a CI job, posting an announcement).

The pre-tag verification phase exists because the v0.17.4 release uncovered two release-blocking bugs that merged-PR CI did not catch (squash-merge silently reverting `version.py`, and the AppImage smoke test's circular `pip install amd-gaia==<unpublished>` dependency). Do not skip it.

## Argument parsing

Accept one argument in any of these shapes:

- `0.17.5` — bare semver
- `v0.17.5` — with the `v` prefix (the actual tag form)
- `0.17.5.1` — hotfix-style four-part version (rare; e.g. `v0.15.4.1`)
- *(no argument)* — read `__version__` from [src/gaia/version.py](src/gaia/version.py), then suggest the next-patch bump (e.g. `0.17.4` → propose `0.17.5`) and ask the user to confirm or override before continuing.

Normalise to the bare form internally (`0.17.5`) and the tag form externally (`v0.17.5`). If the user gives a version that is **older than or equal to** the current `__version__`, stop and ask — never silently roll backwards.

## Hard rules (do not violate)

These map to [CLAUDE.md](CLAUDE.md). Re-read them whenever this skill runs.

- **No Claude attribution anywhere** — not in PR titles, PR bodies, commit messages (no `Co-Authored-By: Claude ...` trailer), release notes, code comments, or the Discord announcement.
- **No silent fallbacks** — if a validator fails, a step times out, or a workflow run isn't found, stop with an actionable error. Do not retry blindly. Do not "proceed anyway."
- **Match house style for release notes** — factual, not opinionated. No "finally", "silently", "no more crashes", "we're excited to announce". Read the **last 2–3 release notes** before drafting. Patch releases do **not** include a `pip install` block. Use the `Why upgrade:` framing with a short bullet list, then `## What's New`, then `## Bug Fixes`, then `## Full Changelog`.
- **Match the previous release PR body shape exactly** — read the most recent merged `Release vX.Y.Z` PR (e.g. `gh pr list --repo amd/gaia --state merged --search "Release v" --limit 3`). Open with `# GAIA vX.Y.Z Release Notes` (no MDX frontmatter in the PR body), end with a `Release checklist` section. Style drift here costs review cycles.
- **Bulletproof commits only** — every change made by this skill must satisfy the four criteria in CLAUDE.md (validated, critiqued, scope-clean, no half-finished work) before being committed.
- **Pushing tags is irreversible.** Always confirm the SHA the tag will point to and the green status of the pre-tag verification run before `git push origin v<version>`.
- **Manual approval gate at the publish step** is human-only — Claude cannot click the GitHub environment "approve" button. Surface the run URL and stop.

---

## Phase 1 — Draft release notes (PR-ready)

**Goal:** produce `docs/releases/v<version>.mdx` matching house style; update navigation and the UI package.json.

### Steps

1. **Survey commits since previous tag.**
   ```bash
   PREV=$(git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+' | head -1)
   echo "Previous tag: $PREV"
   git log "$PREV..HEAD" --pretty=format:'%h  %s'
   git log "$PREV..HEAD" --pretty=format:'%h  %s' | wc -l
   ```
   Group commits by theme (features, fixes, infra, docs). Extract the PR number from each subject (`(#NNN)`). For each non-trivial entry, open the linked PR or commit body to get the *why* — release notes need motivation, not just titles.

2. **Read the last 2–3 release notes** to match style and length.
   - [docs/releases/v0.17.4.mdx](docs/releases/v0.17.4.mdx)
   - [docs/releases/v0.17.3.mdx](docs/releases/v0.17.3.mdx)
   - [docs/releases/v0.17.2.mdx](docs/releases/v0.17.2.mdx)

   Cross-check: same frontmatter shape, same section headings, same tone, same level of detail per entry. Patch releases are short; minor/major releases include `pip install` and may have a "Highlights" block.

3. **Create [docs/releases/v<version>.mdx](docs/releases/)** with this skeleton (adapt to whether it's patch / minor / major):

   ```mdx
   ---
   title: "v<version>"
   description: "<one-line elevator pitch — what shipped, who benefits>"
   ---

   # GAIA v<version> Release Notes

   <One-paragraph overview: what kind of release this is and what it covers.>

   **Why upgrade:**
   - **<short title>** — <one-line value-and-mechanism>.
   - **<short title>** — <one-line value-and-mechanism>.

   ---

   ## What's New

   ### <Feature title>

   <Two short paragraphs: what changed, why it matters, link the PR(s) inline.>

   ---

   ## Bug Fixes

   - **<title>** (PR [#NNN](https://github.com/amd/gaia/pull/NNN)) — <one-line description of fix and impact>.

   ---

   ## Full Changelog

   **N commits** since v<previous>:

   - `<sha>` — <subject>
   - ...

   Full Changelog: [v<previous>...v<version>](https://github.com/amd/gaia/compare/v<previous>...v<version>)
   ```

4. **Update [docs/docs.json](docs/docs.json):**
   - Add `releases/v<version>` to the Releases tab.
   - Bump the navbar label (e.g. `v0.17.4 · Lemonade 10.2.0` → `v0.17.5 · Lemonade <version>`). Read [src/gaia/version.py](src/gaia/version.py) for `LEMONADE_VERSION`.

5. **Sync the UI package version.**
   ```bash
   node installer/version/bump-ui-version.mjs
   ```
   Confirm [src/gaia/apps/webui/package.json](src/gaia/apps/webui/package.json) now reads the new version.

6. **Confirm `__version__` is correct.**
   ```bash
   grep -E '^__version__' src/gaia/version.py
   ```
   The post-prior-release bump usually handles this, but a squash-merge can revert it silently. If it's wrong, edit it. If it's right but reverted later (see Phase 3), the validator will catch it.

7. **Validate.**
   ```bash
   python util/validate_release_notes.py
   ```
   Must exit 0 — this is the gate the publish workflow runs on tag push. Fix any errors before continuing. If it fails for reasons unrelated to your changes, stop and surface that — do not silently bypass.

### Gate 1 — show the user the draft

Show the diff (`git diff --stat` plus the new `.mdx` file inline). Ask: **"Approve these release notes and continue to Phase 2 (open PR)?"** Wait for explicit yes. Iterate on tone/wording before continuing — much cheaper than fixing on `main`.

---

## Phase 2 — Open the release PR

**Goal:** branch, commit, push, open PR, request review.

### Steps

1. **Branch and commit.**
   ```bash
   git checkout -b v<version>-release
   git add docs/releases/v<version>.mdx docs/docs.json src/gaia/version.py src/gaia/apps/webui/package.json
   git status              # confirm scope-clean — no drive-by edits
   git diff --cached --stat
   git commit -m "Release v<version>"
   git push -u origin v<version>-release
   ```

   If `git status` shows anything outside the four expected files, stop and ask. The release PR must be scope-clean.

2. **Read the most recent release PR body to match shape.**
   ```bash
   gh pr list --repo amd/gaia --state merged --search "Release v in:title" --limit 3 \
     --json number,title,body | jq -r '.[0]'
   ```
   Use that as the structural template for *this* PR body — same opening, same checklist, same section order. Do not invent a new shape.

3. **Open the PR.** Title: `Release v<version>`. Body: paste the release notes (the body of the `.mdx` file with the frontmatter stripped) plus a `## Release checklist` section copied from the previous release PR.

   ```bash
   gh pr create --repo amd/gaia \
     --title "Release v<version>" \
     --body-file <path-to-prepared-body.md> \
     --reviewer kovtcharov-amd
   ```

   Surface the resulting PR URL.

### Gate 2 — wait for merge

Stop. Tell the user: **"PR opened at <url>. Waiting for review/merge before pre-tag verification. Re-invoke this skill (or `/loop`) to continue once merged."**

Do not poll, do not auto-merge. Do not push the tag from the un-merged branch.

---

## Phase 3 — Pre-tag verification (the hard-won gate)

**Goal:** prove the merged commit on `main` actually builds clean before the tag locks it in. **Do not skip this.** Two release-blocking bugs in v0.17.4 would have shipped without this step.

### Steps

1. **Sync local main to the merged commit.**
   ```bash
   git checkout main && git pull
   MERGED_SHA=$(git rev-parse HEAD)
   echo "Will tag: $MERGED_SHA"
   ```

2. **Re-verify `__version__` post-merge.** Squash-merges have silently reverted this. If it doesn't match the target version, **stop** — open a follow-up PR to fix it before tagging. Never tag a wrong version.
   ```bash
   grep -E '^__version__' src/gaia/version.py
   ```

3. **Re-run the release notes validator on the merged tree.**
   ```bash
   python util/validate_release_notes.py
   ```

4. **Trigger Build Installers via `workflow_dispatch` against the merged SHA.** This builds the same artifacts the tag push will build, on the same code, *before* the tag exists.
   ```bash
   gh workflow run "Build Installers" --repo amd/gaia --ref main
   sleep 5
   gh run list --repo amd/gaia --workflow "Build Installers" --limit 1
   ```
   Watch it. **AppImage smoke jobs (`distro-matrix`, `userns-restricted`) are the most flaky** — `userns-restricted` has a 90s `state: ready` poll that can race a model download. On real failure, **stop** and fix root cause; on transient flake (timeout-only, no logic error), `gh run rerun <run-id> --failed` is acceptable.

### Gate 3 — green run on the merged commit

Required before continuing:

- `__version__` matches the target.
- `validate_release_notes.py` passes.
- Build Installers run on the merged commit is **green** (not yellow, not "mostly green except smoke tests").

Show the user the run URL and the SHA. Ask: **"Pre-tag verification green on `<sha>`. Push tag `v<version>`?"**

---

## Phase 4 — Tag and trigger the publish pipeline

**Goal:** push the annotated tag; do nothing else.

### Steps

1. **Confirm you are on `main` at the verified SHA.**
   ```bash
   git checkout main
   git pull
   test "$(git rev-parse HEAD)" = "$MERGED_SHA" || { echo "main moved — re-verify"; exit 1; }
   ```

2. **Tag and push.**
   ```bash
   git tag v<version>
   git push origin v<version>
   ```

3. **Confirm the publish workflow picked it up.**
   ```bash
   sleep 10
   gh run list --repo amd/gaia --workflow publish.yml --limit 3
   ```

   The flow is: `validate → build (pypi + npm + electron) → approve (manual gate) → publish (PyPI + npm) → github-release`. Surface the run URL.

---

## Phase 5 — Monitor and approve

**Goal:** watch the run, distinguish flake from real failure, surface the manual approval gate to the human.

### Steps

1. **Watch the run.**
   ```bash
   gh run watch <run-id> --repo amd/gaia
   ```

2. **On flake** (timeout-only, no logic error in the failing step's logs): `gh run rerun <run-id> --failed` is non-destructive — re-runs only failed jobs and their downstream. Do not rerun the whole workflow; that wastes the publish budget.

3. **On real failure** (logic error, missing artifact, validator failure that wasn't there before): **stop**. Do not push past a red build. Do not delete and re-tag — that path is messy. Surface the failing step + log to the user.

4. **Manual approval gate** — when the run reaches the `approve` step in the `publish` GitHub environment, surface the URL to the user. Tell them: **"Manual approval required at <url>. Claude cannot click this — please approve in browser when ready."** Then wait.

5. **After approval**, PyPI + npm + GitHub Release jobs run in parallel. Watch to completion.

---

## Phase 6 — Post-release verification + announcement

**Goal:** confirm artifacts are live, draft the Discord announcement.

### Steps

1. **Smoke test the published wheel.**
   ```bash
   pip install --upgrade amd-gaia==<version>
   gaia -v
   ```
   Must report `<version>`. If it reports the previous version, the squash-merge `version.py` regression slipped through — escalate immediately.

2. **Verify the GitHub release page.**
   ```bash
   gh release view v<version> --repo amd/gaia
   ```
   Required artifacts: `.whl`, `.tar.gz`, `.deb`, `.AppImage`, `.dmg`, `.exe`, and the `latest*.yml` files for the Electron auto-updater. If any are missing, the corresponding build job didn't run or didn't upload — investigate.

3. **Draft the Discord announcement** using the template below. Read the just-shipped release notes (`docs/releases/v<version>.mdx`) to populate the highlight list — one bullet per "What's New" entry plus any Bug Fix worth surfacing, written in the same voice as the notes (factual, no marketing).

   **Template (copy verbatim, fill the bracketed fields):**

   ```
   GAIA v<version> Release

   Hi all, v<version> is <one-clause framing — examples: "a patch release with X and Y", "out — focused on X, Y, and Z", "a hotfix for X">. Upgrade in one command:
   npm install -g @amd-gaia/agent-ui
   gaia-ui

   Currently tested on Strix Halo w/ 32GB+ or Radeon GPU w/ 24GB+ on Windows and Ubuntu.

   What's new in v<version>:

   <Highlight title> — <One-sentence what + why, plain English. 1–2 sentences max per bullet.>
   <Highlight title> — <...>
   <Highlight title> — <...>

   The agent can search files, run commands, and use MCP tools — but only after you approve each action.

   Agent UI guide: https://amd-gaia.ai/docs/guides/agent-ui
   v<version> release notes: https://amd-gaia.ai/docs/releases/v<version>

   Note this is a beta release of the UI, if you notice any bugs or issues please report them here or create an issue!
   ```

   **Format rules** (these match the v0.17.3 / v0.17.4 announcements):
   - The `npm install` line and `gaia-ui` line are *not* in a fenced code block in the announcement — they're plain lines after the colon. Discord renders them as code due to the channel formatting; do not wrap them in backticks.
   - Highlight bullets have **no leading bullet marker** — each is its own paragraph with `**Title** — Description` style. The bold formatting carries the visual hierarchy.
   - 3–5 highlights for a patch, 5–7 for a minor. Anything more becomes a wall of text.
   - The "agent can search files…" sentence and the "Note this is a beta release…" sentence are **fixed boilerplate** — copy them verbatim every release.
   - First-line framing reflects the release character: patches = "a patch release with X and Y"; minor/major = "out — focused on X, Y, and Z"; hotfixes = "a hotfix for X". Match the actual scope, don't oversell.

### Gate 6 — present, do not auto-post

Show the user the smoke-test output, the artifact list, and the drafted Discord announcement in a fenced markdown block. Ask: **"Post the announcement to Discord?"** Wait for explicit yes — Discord posting is human-only (Claude does not have Discord access here, and announcements are visible to all users).

---

## Output between phases

After every phase, output:

1. **Phase N complete.**
2. **What changed / what was verified** (1–3 bullets).
3. **Concrete artifact** (URL, SHA, file path, or fenced draft).
4. **Gate question** (always ends with `?`, names the next destructive action).

Do not bundle two phases into one user prompt. The gates exist for review.

## Notes

- The argument-passing convention is the *target tag*, not the previous tag. If the user says "release v0.17.5", that is what gets created — the previous tag is derived via `git tag --sort=-v:refname | head -1`.
- Hotfix releases (`v0.15.4.1`) follow the same flow; the `validate_release_notes.py` check accepts the four-part form.
- Minor/major releases (`v0.18.0`, `v1.0.0`) need a richer notes structure — a "Highlights" block, the `pip install` instructions, and migration notes if breaking. The skeleton above is patch-shaped; expand for non-patch releases by mirroring the prior minor/major release notes.
- If the publish run fails partway through (e.g. PyPI publishes but npm doesn't), do **not** delete the tag and start over. Resolve the failing job, rerun only that job (`gh run rerun <run-id> --failed`), and let the rest of the pipeline complete idempotently. The tag is the source of truth — preserve it.
