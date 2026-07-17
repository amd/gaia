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

## Resume detection (run before any phase)

The skill is reinvocation-safe — releases span days, and the user will return mid-flow. Before doing *anything* else, detect where in the flow we are and skip phases that already landed:

```bash
git fetch origin --tags
git checkout main && git pull
NOTES_ON_MAIN=$(git ls-tree -r origin/main --name-only | grep -c "^docs/releases/v<version>\.mdx$" || true)
PR_OPEN=$(gh pr list --repo amd/gaia --search "Release v<version> in:title" --state open --json number --jq '.[0].number' || true)
PR_MERGED=$(gh pr list --repo amd/gaia --search "Release v<version> in:title" --state merged --json number --jq '.[0].number' || true)
TAG_EXISTS=$(git tag --list "v<version>")
RELEASE_EXISTS=$(gh release view "v<version>" --repo amd/gaia --json tagName --jq '.tagName' 2>/dev/null || true)
```

Resume table:

| State | Action |
|-------|--------|
| `RELEASE_EXISTS` matches | Release already shipped. Skip to Phase 6 (smoke test + announcement) only. |
| `TAG_EXISTS`, no release | Tag pushed, publish workflow in progress or failed. Resume at Phase 5 (monitor). |
| `PR_MERGED`, no tag | Notes on `main`. Resume at Phase 3 (pre-tag verification). **Do not re-run Phase 1** — never overwrite merged notes. |
| `PR_OPEN` | PR still open. Tell user "PR #N already open at <url> — waiting for merge." Exit. |
| `NOTES_ON_MAIN` ≥ 1 but no PR | Half-finished prior attempt landed notes without a PR (rare). Stop and ask the user before continuing. |
| Nothing matches | Fresh release. Start at Phase 1. |

Always announce the resume decision before continuing: *"Detected `v<version>` state: PR #831 merged, no tag yet. Resuming at Phase 3 (pre-tag verification)."*

## Hard rules (do not violate)

These map to [CLAUDE.md](CLAUDE.md). Re-read them whenever this skill runs.

- **No Claude attribution anywhere** — not in PR titles, PR bodies, commit messages (no `Co-Authored-By: Claude ...` trailer), release notes, code comments, or the Discord announcement.
- **No silent fallbacks** — if a validator fails, a step times out, or a workflow run isn't found, stop with an actionable error. Do not retry blindly. Do not "proceed anyway."
- **Match house style for release notes** — see *Generation parameters* in Phase 1. In short: value-prop first, **local agents are the headline** (not the SDK), one agent/command per highlight, plain language, engaging but factual, and **no emoji, no fluff** (full banned-phrase list under *Generation parameters*). Read the **last 2–3 release notes** before drafting. Patch releases do **not** include a `pip install` block. Use the `Why upgrade:` framing with a short bullet list, then `## What's New`, then `## Bug Fixes`, then `## Full Changelog`.
- **Match the previous release PR body shape exactly** — read the most recent merged `Release vX.Y.Z` PR (e.g. `gh pr list --repo amd/gaia --state merged --search "Release v in:title" --limit 3`). Open with `# GAIA vX.Y.Z Release Notes` (no MDX frontmatter in the PR body), end with a `Release checklist` section. Style drift here costs review cycles.
- **Bulletproof commits only** — every change made by this skill must satisfy the four criteria in CLAUDE.md (validated, critiqued, scope-clean, no half-finished work) before being committed.
- **Pushing tags is irreversible.** Always confirm the SHA the tag will point to and the green status of the pre-tag verification run before `git push origin v<version>`.
- **Manual approval gate at the publish step** is human-only — Claude cannot click the GitHub environment "approve" button. Surface the run URL and stop.

---

## Phase 1 — Draft release notes (PR-ready)

**Goal:** produce `docs/releases/v<version>.mdx` matching house style; update navigation and the UI package.json.

### Steps

1. **Survey commits since previous tag, then sanity-check the version request against scope.**
   ```bash
   PREV=$(git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+' | head -1)
   echo "Previous tag: $PREV"
   git log "$PREV..HEAD" --pretty=format:'%h  %s'
   echo
   echo "Total: $(git log $PREV..HEAD --oneline | wc -l) commits"
   echo "Feat:  $(git log $PREV..HEAD --oneline | grep -cE '^[a-f0-9]+ feat') feat commits"
   echo "Fix:   $(git log $PREV..HEAD --oneline | grep -cE '^[a-f0-9]+ fix')  fix commits"
   ```
   Group commits by theme (features, fixes, infra, docs). Extract the PR number from each subject (`(#NNN)`). For each non-trivial entry, open the linked PR or commit body to get the *why* — release notes need motivation, not just titles.

   **Scope-vs-version sanity check (do not skip):** apply this rubric against the requested target version:

   | Commit shape | Suggested release shape |
   |--------------|--------------------------|
   | Only `fix`/`docs`/`chore`/`ci`, no `feat` | Patch (`vX.Y.Z+1`) ✓ |
   | 1–2 `feat` commits, small scope | Patch acceptable, mention them as "What's New" |
   | 3+ `feat` commits, or any commit titled `... vX.Y.Z milestone`, default-model swap, breaking change, package layout change | **Minor** (`vX.Y+1.0`) — push back |
   | Removal of a public API, CLI flag deletion, config schema break, version-pin floor raised in a non-additive way | **Major** (`vX+1.0.0`) — push back |

   If the requested version doesn't match the rubric, **stop and surface the mismatch**: *"You asked for `v<requested>` (patch). I see N feat commits since `<prev>` including `<one or two examples>` — this looks minor-shaped. Continue as patch, or bump to `v<suggested>`?"* Do not silently proceed.

2. **Read the last 2–3 release notes** to match structure and length (not tone — see *Generation parameters*).
   - [docs/releases/v0.17.4.mdx](docs/releases/v0.17.4.mdx)
   - [docs/releases/v0.17.3.mdx](docs/releases/v0.17.3.mdx)
   - [docs/releases/v0.17.2.mdx](docs/releases/v0.17.2.mdx)

   Cross-check: same frontmatter shape, same section headings, same *structure* and length per entry — but **not** the prior tone. The last few releases predate the *Generation parameters* below; match their shape, not their dryness. Patch releases are short; minor/major releases include `pip install` and may have a "Highlights" block.

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

   ### <What the user can now do> — `<gaia command>`

   <Lead with the outcome and why it matters, in plain language that makes the reader
   want to try it. Then one line on how to run it, PR linked inline. One agent or
   command per entry — add another `### ` block for the next one. Not every highlight
   is a command — for UI / SDK / perf items, use a plain title with no trailing
   command.>

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

   **Generate the changelog by introspecting git, and escape it for MDX.** Do not
   hand-transcribe subjects, and do not pipe `git log` output in raw — a subject
   containing `<` or `{` is valid git and invalid MDX, which fails CI's `mintlify
   validate`. v0.22.0 hit this on `#1791`'s subject (*"fix Mintlify MDX validation
   (unescaped '<' breaks the docs check)"*) — the one commit whose subject describes
   the bug it causes.

   ```bash
   # Generate the `- `<sha>` — <subject>` lines, escaping the two characters MDX
   # treats as syntax. `\<` and `\{` render as literal `<` / `{` (verified against
   # `mintlify validate`), and shas never contain them, so escaping the whole line
   # is safe. Subjects like "AMD <> SpecificAI" survive correctly.
   git log v<previous>..HEAD --pretty=format:'- `%h` — %s' \
     | sed -e 's/</\\</g' -e 's/{/\\{/g' > /tmp/changelog.txt

   # Guard: nothing hazardous may survive.
   grep -nE '(^|[^\\])[<{]' /tmp/changelog.txt && echo "MDX HAZARD — fix before continuing" || echo "changelog MDX-safe"
   ```

   Sanity-check the count against `git log --oneline | wc -l` **after** writing the file
   (`wc -l` under-counts by one when the last line has no trailing newline — v0.22.0
   claimed 197 when the real count was 198). The claimed count, the listed lines, and
   `git log` must all agree.

   **Generation parameters (apply to every entry — this is the point of the skill).**
   GAIA's notes have historically read dry and engineering-first: they say *what
   changed* but not *why a user should care or want to try it*. Generate against these
   every time:

   - **Value-prop first.** Open each entry with what the user can now do and why it
     matters — the outcome, not the implementation. "Triage your inbox in one command"
     before "added EmailAgent with Gmail polling".
   - **Local agents are the headline.** Lead with the agents that solve real problems
     (`gaia browse`, `gaia analyze`, email triage, …); SDK / infra / refactors are
     supporting detail. People come for the agents, not the SDK.
   - **One agent or command per highlight.** `gaia browse` and `gaia analyze` each get
     their own `### ` entry with its own one-line utility — never lumped together.
   - **Plain, human language.** Write like you're telling a colleague what they can do
     now. Short sentences; plain words over jargon.
   - **Engaging, still factual.** Make the reader want to try it without overselling —
     no invented benchmarks, no "fastest ever". The pull comes from a clear, real
     capability, not adjectives.
   - **No fluff, no emoji.** Banned: emoji in headings or body, "we're excited to
     announce", "finally", "blazing(-fast)", "Here's the good stuff", "no more
     crashes", "silently", "game-changer".

   **Example — one highlight, done right:**

   > **Bad** (dry, implementation-first, no reason to care):
   > ### EmailAgent
   > Adds an EmailAgent with Gmail polling and a rules engine for classification.

   > **Good** (value-first, plain, makes you want to try it):
   > ### Triage your inbox from the terminal — `gaia email`
   > Point GAIA at your inbox and it sorts the noise from what needs you: drafts
   > replies to routine mail, flags what's urgent, leaves the rest. Runs locally, so
   > your mail never leaves your machine. Try it: `gaia email`.

4. **Update [docs/docs.json](docs/docs.json):**
   - Add `releases/v<version>` to the Releases tab.
   - Bump the navbar label (e.g. `v<previous-version> · Lemonade <previous-lemonade>` → `v<version> · Lemonade <current-lemonade>`). Read [src/gaia/version.py](src/gaia/version.py) for the `LEMONADE_VERSION` constant — it is the source of truth, and the navbar may be drifted from it (Lemonade bumps land outside release PRs).

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

7. **Validate — both checks.** Run from the repo's activated venv (`source .venv/bin/activate` on Linux/macOS, `.venv\Scripts\activate` on Windows; the bare-`python` Microsoft Store stub will fail). If you're working from a git worktree without its own venv, run from the parent checkout's venv.
   ```bash
   python util/validate_release_notes.py docs/releases/v<version>.mdx --tag v<version>
   (cd docs && npx -y mintlify@latest validate)   # the docs.yml `validate` job — MUST also pass
   ```
   Both must exit 0. Fix any errors before continuing. If either fails for reasons unrelated to your changes (missing dep, broken import), stop and surface that — do not silently bypass. `validate_release_notes.py` prints the first failing check (missing/renamed section, absent `compare/` link, tag mismatch) — read that line to localise the fix; it has no `--verbose` flag.

   **`validate_release_notes.py` passing is not sufficient — it is not an MDX parser.** CI's
   `validate` job additionally runs `mintlify validate` from `docs/`, and v0.22.0 failed it
   after the notes passed the Python validator (see the escaping rule in step 3). Its error is
   misleading: an unparseable `.mdx` surfaces as `"releases/v<version>" is referenced in the
   docs.json navigation but the file does not exist` — the file exists, it just never parsed.
   Pre-existing parse errors under `docs/plans/` and `docs/superpowers/` are reported but
   non-fatal; leave them alone.

### Gate 1 — show the user the draft

Show the diff (`git diff --stat` plus the new `.mdx` file inline). Ask: **"Approve these release notes and continue to Phase 2 (open PR)?"** Wait for explicit yes. Iterate on tone/wording before continuing — much cheaper than fixing on `main`.

---

## Phase 2 — Open the release PR

**Goal:** branch, commit, push, open PR, request review.

### Steps

1. **Branch and commit.**
   ```bash
   git checkout -b v<version>-release
   git add docs/releases/v<version>.mdx docs/docs.json src/gaia/version.py \
           src/gaia/apps/webui/package.json src/gaia/apps/webui/package-lock.json
   git status              # confirm scope-clean — no drive-by edits
   git diff --cached --stat
   git commit -m "Release v<version>"
   git push -u origin v<version>-release
   ```

   If `git status` shows anything outside those five files, stop and ask (`bump-ui-version.mjs` rewrites `package-lock.json` too — the prior release commit carries all five). The release PR must be scope-clean.

2. **Read the most recent release PR body to match shape.**
   ```bash
   gh pr list --repo amd/gaia --state merged --search "Release v in:title" --limit 3 \
     --json number,title,body | jq -r '.[0]'
   ```
   Use that as the structural template for *this* PR body — same opening, same checklist, same section order. Do not invent a new shape.

3. **Prepare the PR body file.** Build it by stripping the MDX frontmatter from the release notes and appending the `Release checklist` section copied from the previous release PR.

   ```bash
   # Strip the YAML frontmatter (everything between the first two '---' lines)
   awk '/^---$/{c++; next} c>=2' docs/releases/v<version>.mdx > /tmp/release-body.md

   # Append the Release checklist section from the previous release PR
   PREV_PR=$(gh pr list --repo amd/gaia --state merged --search "Release v in:title" --limit 1 --json number --jq '.[0].number')
   gh pr view "$PREV_PR" --repo amd/gaia --json body --jq '.body' \
     | awk '/^## Release checklist/{found=1} found' \
     >> /tmp/release-body.md

   # Sanity-check the body before opening the PR
   head -3 /tmp/release-body.md   # should start with "# GAIA v<version> Release Notes"
   tail -10 /tmp/release-body.md  # should end with the checklist
   ```

   If the awk pipeline produces an empty file, the previous PR didn't have a `## Release checklist` heading — fall back to copying the entire body and editing it manually before continuing.

4. **Open the PR.** Title: `Release v<version>`. Body: the file you just built.

   ```bash
   gh pr create --repo amd/gaia \
     --title "Release v<version>" \
     --body-file /tmp/release-body.md \
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

1. **Sync local main to the merged commit, and capture the SHA from the release PR (not just the local HEAD).**
   ```bash
   git checkout main && git pull
   # Re-derive from the release PR — survives gate pauses across sessions/shells.
   RELEASE_PR=$(gh pr list --repo amd/gaia --state merged --search "Release v<version> in:title" --json number --jq '.[0].number')
   MERGED_SHA=$(gh pr view "$RELEASE_PR" --repo amd/gaia --json mergeCommit --jq '.mergeCommit.oid')
   echo "Will tag: $MERGED_SHA (from PR #$RELEASE_PR)"
   test "$(git rev-parse HEAD)" = "$MERGED_SHA" || { echo "Local main ($( git rev-parse HEAD)) does not match merged release PR SHA ($MERGED_SHA) — pull, or main has moved past the release commit"; exit 1; }
   ```

   When you reach Gate 3 below, **carry both `$RELEASE_PR` and `$MERGED_SHA` into the gate question text** so Phase 4 can re-derive them from the answer rather than depending on shell variables that don't survive the pause.

2. **Re-verify `__version__` post-merge.** Squash-merges have silently reverted this. If it doesn't match the target version, **stop** — open a follow-up PR to fix it before tagging. Never tag a wrong version.
   ```bash
   grep -E '^__version__' src/gaia/version.py
   ```

3. **Re-run the release notes validator on the merged tree.**
   ```bash
   python util/validate_release_notes.py docs/releases/v<version>.mdx --tag v<version>
   ```

4. **Trigger Build Installers via `workflow_dispatch` against the merged SHA.** This builds the same artifacts the tag push will build, on the same code, *before* the tag exists.
   ```bash
   gh workflow run "Build Installers" --repo amd/gaia --ref main
   sleep 5
   RUN_ID=$(gh run list --repo amd/gaia --workflow "Build Installers" --limit 1 --json databaseId -q '.[0].databaseId')
   echo "Watching run $RUN_ID"
   gh run watch "$RUN_ID" --repo amd/gaia
   ```
   **AppImage smoke jobs (`appimage-distro-matrix`, `appimage-userns-restricted`) are the most flaky** — `appimage-userns-restricted` has a 300s `state: ready` poll (raised from 90s to cover a first-run model download). On real failure, **stop** and fix root cause; on transient flake (timeout-only, no logic error), `gh run rerun $RUN_ID --failed` is acceptable.

### Gate 3 — green run on the merged commit

Required before continuing:

- `__version__` matches the target.
- `validate_release_notes.py` passes.
- Build Installers run on the merged commit is **green** (not yellow, not "mostly green except smoke tests").

Show the user the run URL, the **release PR number** (`#$RELEASE_PR`), and the **merged SHA** (`$MERGED_SHA`). Ask: **"Pre-tag verification green on `<sha>` (release PR #N). Push tag `v<version>`?"** — the SHA and PR number being in the question text means Phase 4 can re-derive them even if the user resumes in a fresh shell.

---

## Phase 4 — Tag and trigger the publish pipeline

**Goal:** push the annotated tag; do nothing else.

### Steps

1. **Confirm you are on `main` at the verified SHA.** Re-derive `$MERGED_SHA` from the release PR (the SHA from Gate 3) — do not trust shell state across the gate pause.
   ```bash
   git checkout main
   git pull
   # Re-derive from the release PR — the PR number was in the Gate 3 question.
   MERGED_SHA=$(gh pr view <release-pr-number> --repo amd/gaia --json mergeCommit --jq '.mergeCommit.oid')
   test "$(git rev-parse HEAD)" = "$MERGED_SHA" || { echo "main moved past verified SHA $MERGED_SHA — re-verify (Phase 3) before tagging"; exit 1; }
   ```

2. **Tag and push.**
   ```bash
   # Annotated, on the verified SHA — every prior release tag is annotated ("Release vX.Y.Z").
   # A bare `git tag v<version>` creates a lightweight tag and breaks convention.
   git tag -a v<version> <merged-sha> -m "Release v<version>"
   git rev-list -n1 v<version>   # MUST equal <merged-sha> before pushing
   git push origin v<version>
   ```

3. **Confirm the publish workflow picked it up.**
   ```bash
   sleep 10
   gh run list --repo amd/gaia --workflow publish.yml --limit 3
   ```

   The flow is: `validate → build (build-pypi + build-npm + build-desktop-installers) → approve-publish (manual gate) → publish (publish-pypi + publish-npm) → post-publish-smoke → github-release → refresh-context7`. Surface the run URL.

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

4. **Manual approval gate** — when the run reaches the `approve-publish` job (gated on the `publish` GitHub environment), surface the URL to the user. Tell them: **"Manual approval required at <url>. Claude cannot click this — please approve in browser when ready."** Then wait.

5. **After approval**, PyPI + npm + GitHub Release jobs run in parallel. Watch to completion.

6. **`refresh-context7` is the terminal job and may legitimately fail — that is not a release failure.** This job runs *after* PyPI, npm, GitHub Release, and the desktop installers are already published. Context7's API rejects refresh requests inside a short cooldown window (observed: ~3–6 days between releases) with HTTP 429 (rate-limited); the workflow tolerates 429 and treats any other status as a hard failure. If the job is red, the release is still live. Open the job log, read the `Response body:` block between the `::stop-commands::` markers, and either accept the cooldown reason or file a follow-up about a new rejection cause. Do **not** delete or re-tag — see the recovery guidance in the Notes section below.

7. **Never add `set -x` or `curl -v` to the `refresh-context7` step** to "debug" a failure. GHA only masks the verbatim secret value; `curl -v` prints the `Authorization: Bearer <token>` header, which is a transformed form that GHA's masking does not catch. Read the captured response body instead — it carries the same diagnostic signal without the leak risk.

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

3. **Draft the Discord announcement** using the template below. Read the just-shipped release notes (`docs/releases/v<version>.mdx`) to populate the highlight list — one bullet per "What's New" entry plus any Bug Fix worth surfacing, written in the same voice as the notes — apply the same *Generation parameters* (value-prop first, plain, engaging, no fluff/emoji).

   **Template (copy verbatim, fill the bracketed fields).** This is the v0.22.0 shape —
   the format the maintainer actually posts. Reproduce the markdown exactly: the role
   mention, the backticked version, the fenced install block, and the `- ` bullets are
   all load-bearing.

   ````
   @gaia **GAIA v<version> Release**

   Hi all, `v<version>` is <one-clause framing — examples: "a patch release with X and Y", "out — focused on X, Y, and Z", "a hotfix for X">. Upgrade in one command:

   ```
   npm install -g @amd-gaia/agent-ui
   gaia-ui
   ```

   Currently tested on Strix Halo w/ 32GB+ or Radeon GPU w/ 24GB+ on Windows and Ubuntu.

   **What's new in v<version>**

   - **<Highlight title>** — <One-sentence what + why, plain English. 1–2 sentences max per bullet.>
   - **<Highlight title>** — <...>
   - **<Highlight title>** — <...>

   The agent can search files, run commands, and use MCP tools — but only after you approve each action.

   Agent UI guide: https://amd-gaia.ai/docs/guides/agent-ui
   v<version> release notes: https://amd-gaia.ai/docs/releases/v<version>

   Note this is a beta release of the UI and the Email Agent, if you notice any bugs or issues please report them here or create an issue!
   ````

   **Format rules** (these match the v0.22.0 announcement — the current house format):
   - **Open with the `@gaia` role mention**, then the bold title: `@gaia **GAIA v<version> Release**`.
   - The version in the "Hi all" line is **backticked** — `` `v0.22.0` ``.
   - The `npm install` / `gaia-ui` lines **go in a fenced code block**, with a blank line before it.
   - `**What's new in v<version>**` is **bold with no trailing colon** — it is not a `##` heading.
   - Highlights **are `- ` bullets**, each `- **Title** — Description`.
   - 3–5 highlights for a patch, 5–7 for a minor. Anything more becomes a wall of text.
   - The "agent can search files…" and "Note this is a beta release of the UI and the Email Agent…" sentences are **fixed boilerplate** — copy them verbatim every release.
   - First-line framing reflects the release character: patches = "a patch release with X and Y"; minor/major = "out — focused on X, Y, and Z"; hotfixes = "a hotfix for X". Match the actual scope, don't oversell.
   - Carry the release notes' beta framing into the announcement where it applies — this channel is where users hit the rough edges first, so don't oversell here.

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
