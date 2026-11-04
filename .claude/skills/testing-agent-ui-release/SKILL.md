---
name: testing-agent-ui-release
description: Release-test the GAIA desktop Agent UI end to end with computer use. Plan from what merged since the last RC or final release, drive the real desktop app through the Claude desktop app's computer use, keep a live report of what passed, failed and was never tested, file GitHub issues for what broke, fix them in parallel sessions, merge, retest until a round finds nothing, and optionally run the ai-pc-evals benchmark through the window. Use when asked to test, QA, validate or bug-bash a GAIA release, an RC, or the desktop app as a whole. For a single UI change use reviewing-agent-ui; for one feature's tiered proof use gaia-testing.
---

# Release-testing the Agent UI

**A release pass is finished when a retest round finds nothing new, not when the
plan runs out.** Every round can surface the next bug, and a fix is not proven until
someone has looked at it in the rebuilt app. `test_report.py gate` holds this rule.

The loop: **scope → plan → test → file → fix in parallel → merge → retest**, repeated
until a retest finds nothing, then publish the final report.

The scripts are repo tooling in `util/agent_ui_release/`, so the skill audit's install-time
permission checks don't apply to them. All are standard-library Python and print `--help`:

| Script | Job |
|---|---|
| `release_scope.py` | What merged since the last RC or release → `scope.json` + `plan.md` |
| `test_report.py` | The live report: items, findings, rounds, benchmark, `gate` |
| `launch_isolated.py` | Download an RC, unpack it without installing, run it on a throwaway profile, stop it, check for leaks |
| `capture_window.py` | Save the window to disk for evidence (`--cdp` = a fresh frame) |
| `cdp.py` | Drive the page over DevTools: `shot`, `click "<text>"`, `eval`, `text` (the fallback driver) |
| `app_state.py` | Read the backend behind the window: answer text, tool steps, idle/busy |
| `pc_evals.py` | Optional ai-pc-evals benchmark through the window |

Set `SK=util/agent_ui_release` and keep one run directory
per pass (`RUN`, e.g. `C:\gaia-test\v0.25.4-rc3`). **Keep test paths short:** long
paths break model downloads on Windows.

## Ground rules

These come from earlier passes that went wrong:

- **Test the desktop app, not the CLI or the backend alone.** "No, I meant the app" is
  the correction this skill exists to prevent. The backend API is the second witness,
  never the test.
- **Never touch the person's own GAIA.** That means their install, their `~/.gaia`, any
  window another session opened, and the pinned eval session. Isolation leaks have
  happened twice: a test wrote into the real `memory.db`, and an "isolated" home
  copied in nine real memories. Always run through `launch_isolated.py`, and check
  `isolation` when you stop.
- **One GPU user at a time.** Other sessions' `llama-server`s and evals may be running.
  Loading a model in your test can evict theirs, and theirs can evict yours. Check
  `app_state.py find` and the running evals first (CLAUDE.md, "Run agent evals
  SERIALLY"). Coordinate over SendMessage, or wait. When the GPU is taken, chat on a
  **cloud model** instead: point `--lemonade` at the person's running Lemonade that
  already has Fireworks registered (`GET /api/v1/models` lists `fireworks.*` ids). Cloud
  models don't take the GPU slot, but RAG indexing of PDFs and images still loads a local
  vision model, so skip document tests while someone else's eval runs.
- **Look before you pass.** Read every screenshot before you mark an item passed. A
  pass needs what you saw (`--note`) and, for anything visual, a saved image
  (`--evidence`).
- **Match the evidence to the claim.** A check for "the answer kept its text" must look in
  the answer, not the whole page. A page-wide match once found the word in the question
  and passed an answer that had no text at all.
- **Look as hard before you fail.** Before filing, rule out three things: your own
  test setup (a missing Desktop folder, `GAIA_DISABLE_UPDATE`), invalid input (`*` is
  not a legal Windows path character), and UI that appears only on hover or focus
  (each answer's footer does).
  Check a "should be" number against the code before you file it. A 34% context meter
  looked wrong but was GAIA's deliberate 64K cloud budget, and the issue had to be corrected.
- **A finding a person reports is a gap in this skill.** Add a check to
  `references/checklist.md` in the same change.

## 1. Scope the build

```bash
git fetch --tags origin
python $SK/release_scope.py --out $RUN                   # newest tag vs. the tag before it
python $SK/release_scope.py --out $RUN --since release   # newest tag vs. the last final release
```

Pick the range you were asked for. When the request doesn't say, test the newest RC
against the last **final** release (`--since release`), because that is what users
upgrade across. The script reads each PR's title, linked issues and "Test plan", sorts
it by the surface it touches, and ranks it:

- **P1**: a fix or feature a person can reach in the app. Must test.
- **P2**: a refactor or chore in reachable code. Spot check.
- **P3**: not reachable from the Agent UI (TUI-only, CLI-only). Marked out of scope, with the reason.
- **skip**: docs, tests or CI only.

## 2. Plan

`plan.md` is a draft, not a plan. Before testing:

1. **Merge items that share a screen.** Ten model-picker PRs become one model-picker
   session that checks all ten.
2. **Give every P1 a concrete expected result.** Example: "picking an undownloaded
   model downloads it *and switches to it*", not "model picker works".
3. **Add the always-check areas** from [references/checklist.md](references/checklist.md).
   These are the defect classes earlier passes kept finding, whatever the diff says.
4. **Say what you can't test on this machine.** Examples: NPU paths without an NPU,
   macOS, a cloud provider without a key. Those items go `not-tested` with the reason,
   up front.

Then start the report:

```bash
python $SK/test_report.py init $RUN --build "v0.25.4-rc3 Windows installer" \
    --machine "Strix Halo 128 GB, Windows 11" --scope $RUN/scope.json
python $SK/test_report.py add $RUN --title "Arabic: settings and model picker" --area i18n --priority P1 \
    --expect "no English left, sizes read 9.7 MB, names truncate at their end"
```

Tell the person, in a few lines: the build, the range, the counts, what is out of
scope, and whether the benchmark will run. Then start. They asked for the whole loop,
so don't wait for approval unless something in the plan touches their own setup.

## 3. Get the build running, isolated

```bash
python $SK/launch_isolated.py fetch v0.25.4-rc3 C:/gaia-test        # unpack only; needs 7-Zip or `pip install py7zr`
python $SK/launch_isolated.py start --app C:/gaia-test/app-v0.25.4-rc3/gaia-desktop.exe --home C:/gaia-test/h-rc3
export GAIA_UI_PORT=$(python $SK/launch_isolated.py port --home C:/gaia-test/h-rc3)   # waits for the backend
```

`app_state.py` and `pc_evals.py record` read `GAIA_UI_PORT` (or `--port`) and have no
default, because the person's own app may be listening on 4200 and they'd read its chats.

- **An empty `--home` is a true first run.** Setup installs the backend and runs
  `gaia init`. That is the path new users hit, so test it at least once per pass, and
  from a fresh home, never your warm one (CLAUDE.md, "Test from the user's real initial
  state").
- **`--lemonade http://127.0.0.1:59999` tests the no-model-server path** without
  touching the shared Lemonade. Omit it to chat for real.
- The app gets its own `--user-data-dir`, a DevTools port (`--cdp`, default 9333) and
  `GAIA_DISABLE_UPDATE=1`, so an updater relaunch can't escape into the real profile.
- Two runs at once need different `--cdp` ports; `start` refuses a port in use.
- Stop it with `stop`. That kills only the process tree `start` launched, then checks
  isolation: the test home must hold its own chat database, and no data file in the
  real profile may have changed. Other GAIA sessions also write there, so read a
  change as "check it", not as proof of a leak.

## 4. Drive it with computer use

Load the `anthropic-skills:computer-use` skill before the first computer-use step.

**Ask for access to the exact executable.** A display name like "GAIA" resolves to the
installed app, and "Electron" can resolve to another worktree's build. A full path is
granted as itself:

```text
request_access(apps=["C:\\gaia-test\\app-v0.25.4-rc3\\gaia-desktop.exe"], clipboardWrite=true)
```

`open_application` can still refuse a path grant, so bring the window forward by
clicking it instead. The tray and taskbar need "File Explorer" (click tier only). The installer window is
granted by its exe name. The Claude app itself is never grantable.

| Do | Because |
|---|---|
| Batch predictable steps in one `computer_batch`, ending in a screenshot | Each round trip costs seconds |
| Click the window before typing; put focus-then-act in one batch | Windows *Click to Do* and other windows steal focus; press Escape if it opens |
| Paste long text: `write_clipboard` → click composer → Ctrl+V → screenshot → Enter | Remote-desktop clipboard sync can clear it; confirm the paste landed |
| Wait with `app_state.py wait`, not screenshots | It returns when no answer is being written or indexed; a timeout is a finding |
| Screenshots at `scale` 0.5–0.6; `zoom` for small text | Full-size screenshots burn context |
| Save evidence with `capture_window.py --cdp 9333 $RUN/shots/NNN_what.png` | Computer-use images are masked (other apps go black) and land in the session folder |
| For native chrome (title bar, tray, dialogs, crash box) use `screenshot(save_to_disk=true)` or `capture_window.py --pid` **with the window in front** | Electron can stop painting a covered window, so PrintWindow may return an old frame; the script warns when the window isn't in front, and a `--cdp` capture of the same moment is the cross-check |
| Crop full-desktop screenshots to the app before they go anywhere public | They show the person's other windows: file names, folders, chats |
| Keep Settings › Model out of evidence | It shows the tail of the person's cloud API key |

**Check every chat result twice: the pixels and the backend.**
`app_state.py last` prints the answer text, every tool step and whether it
succeeded, and timing. A reply that looks right on screen but shows no tool call
behind it was made up. To prove retrieval, plant facts nobody could guess, for
example a passphrase like `violet-otter-92`. Then `app_state.py grep violet-otter-92`
exits non-zero when the answer lacks it.

**Cover states, not just the happy path.** For each screen, check:

- empty, running, failed, stopped mid-answer, long content, and a modal over a banner
- English and Arabic, light and dark
- a restart: quit from the tray, relaunch on the same `--home`

Arabic layout rules are in the `reviewing-agent-ui` skill.

**When access is denied:** dev builds and the taskbar have been denied before. Fall
back, and say so in the report:

1. Drive through DevTools: `cdp.py --port 9333 click "<button text>"` (or Playwright
   `chromium.connectOverCDP`). This also reaches a control the app hides from a person,
   such as a link under the window buttons. Record the bug first, then mark the step
   "driven through DevTools".
2. Or open `http://127.0.0.1:<port>/` in the built-in browser pane.

Either way you lose the window controls, tray, native dialogs and installer, so mark
those items `not-tested` with "computer use denied". **Never call a browser-pane run
a desktop test.**

Record each result the moment you have it, not in a batch at the end. `add` prints the
new item's id; use that id:

```bash
python $SK/test_report.py set $RUN PR6123 in-progress
python $SK/test_report.py set $RUN PR6123 passed --note "picked Qwen, it downloaded and became active" \
    --evidence shots/014_model_switched.png
python $SK/test_report.py find $RUN --title "Picking an undownloaded model never switches to it" \
    --severity high --items PR6123 --repro "1. Model picker 2. pick Qwen3.6 (not downloaded) 3. wait" \
    --expected "Qwen becomes active" --actual "download finishes, Gemma stays selected" \
    --evidence shots/015_still_gemma.png
python $SK/test_report.py status $RUN
```

Severity: **critical** means data loss, a crash, or setup can't finish. **high** means
a P1 feature is broken. **medium** means it's wrong but there's a workaround. **low**
means cosmetic. Testing never stops at the first failure: record it and keep going.
`report.md` re-renders on every command; [publish it as an Artifact](#9-publish-the-report)
early and update it as you go.

## 5. Benchmark (optional)

Run this only when the person points at an ai-pc-evals package. Never assume a path:
it changes between versions.

```bash
export AI_PC_EVALS_DIR=<the unzipped package>
python $SK/pc_evals.py check                  # version, clean copy, grading set up?
python $SK/pc_evals.py prepare $RUN --suite smoke
```

`pc_evals.py drive $RUN --cdp 9333` runs every prepared task through the window over
DevTools: one chat each, the prompt typed in. It allows a permission prompt only when the
prompt names the task folder or is a relative command with no `..`, records each
answer, and logs every approval and denial to `bench/approvals.json`. Read that log
before trusting a failure: the first version of this rule denied a task's own
`python tools/…` run and failed a task GAIA had done right.

To do it by hand instead, for each task, one at a time:

1. Open a new chat and set the folder chip to the task folder (native picker: paste
   the path).
2. Paste the prompt from `$RUN/bench/<task>.prompt.txt`.
3. Approve tool prompts only inside the task folder.
4. Run `app_state.py wait`, then `pc_evals.py record $RUN <task> --port P`. This takes
   the answer and timing from the backend.
5. If the agent stopped to ask you something, record it with
   `--outcome stalled-asked-human`.

If the folder picker can't be reached (no screen access), send the prompt anyway: it
names the task folder, and GAIA asks "Let GAIA use this folder?". Allow it only when
the path is the task folder, and allow writes only inside it.

Record every task while the app is still running (`record` reads its backend). Then
close the app, then run `pc_evals.py grade $RUN` and copy its headline into the report:
`test_report.py bench $RUN --suite smoke --agent gaia-agent-ui --summary <path> --headline "4/5 passed"`.

**Scores come only from the grader.** Without the answers passphrase the result is
`--status not-graded`. Ask the person to set it themselves. Never estimate a score, and
never open `secrets/`, `scorecard.json` or `judge-input.txt`; the package's own
`CLAUDE.md` says what you may read. Start with `smoke`, and run a bigger suite only
when asked.

## 6. File the issues

When the first round is done (`status` shows no `planned` or `in-progress` items), turn
findings into issues:

1. **Search first, closed issues included:**
   `gh issue list -R amd/gaia --state all --search "<2-3 key terms>"`.
   For a match, comment on it or reopen it, and record it with
   `link F3 --issue <url>` (or `--status duplicate --note "same as #NNNN"`).
2. **One issue per root cause, not per symptom.** Three screens failing from one bug is
   one issue that lists all three.
3. **Show the person the list (titles and severity) and get one go-ahead** for filing
   them, and for the fix sessions to push branches and open PRs. Both are public.
4. Write each issue to [references/issue-template.md](references/issue-template.md):
   plain-language impact first, then steps, expected vs. actual, build, and evidence.
   For screenshots that must render, use the `raw.githubusercontent.com` evidence
   branch from the `gaia-testing` skill. Redact anything from the person's own files.
5. **Security findings are never filed publicly.** Use a private advisory and tag the
   maintainer (CLAUDE.md, "Issue Response Guidelines").
6. Record each one: `test_report.py link $RUN F3 --issue https://github.com/amd/gaia/issues/NNNN`.

## 7. Fix in parallel

**Check for auto-fix PRs first.** Filing with the `bug` label starts the repo's auto-fix
workflow, which opens a draft PR per issue on an `autofix/issue-<N>` branch within
minutes: `gh pr list -R amd/gaia --search "autofix/issue-<N> in:head"`. Before starting
fixes, decide per issue: leave it to the auto-fix draft and retest that branch, or fix it
yourself and comment on the draft with your retested branch. Never open a second PR
without saying so on the first.

Start **one session per issue** (`start_session`), in its own worktree, with
[references/fix-brief.md](references/fix-brief.md) filled in. Send them all at once,
and give them independent issues. Two fixes in the same component go to one session,
so they don't conflict.

- Each session writes a failing test first, fixes, lints, commits, opens a PR that says
  `Closes #N`, and reports the branch and PR. It does not edit the report.
- A turn started by a hook or another session can't start sessions. Then run the
  fixes as background subagents with `isolation: "worktree"`, one per component, under
  the same brief. They commit to their own worktree branches, which you merge the same way.
- As each one reports, check its diff and test result before you believe it. Record
  progress with `link $RUN F3 --pr <url>`.
- A fix session that finds the bug is not what the issue said updates the issue. It
  doesn't widen its own scope.

## 8. Merge and retest

**Merging and pushing need the person's go-ahead** (CLAUDE.md). Ask once, listing the
PRs.

1. **Fast local retest.** Merge every fix branch into a local
   `integration/<target>` branch, using full refs (`refs/heads/...`). Build it:
   - `uv build --wheel`
   - `cd src/gaia/apps/webui && npm ci && npm run build`. If
     `node_modules/electron/dist/electron.exe` is missing afterwards (npm sometimes
     skips it), run `node node_modules/electron/install.js`.

   Mark each merged finding fixed:
   `test_report.py link $RUN F3 --status fixed --note "in integration@abc1234"`.
   Then launch it from a **fresh** home:
   `launch_isolated.py start --app <wt>/src/gaia/apps/webui/node_modules/electron/dist/electron.exe --electron-app <wt>/src/gaia/apps/webui --wheel <wt>/dist/amd_gaia-*.whl --home ...`

   This covers frontend and backend fixes. The flagship agent ships as a downloaded
   binary, so agent-side fixes aren't in this build: mark those "retest on the next
   RC", not passed.
2. **Authoritative retest.** Once the PRs are merged, retest the packaged build users
   will get. That means the next RC (ask the person to cut one; this skill never tags),
   or a `build-installers.yml` run on `main`
   (`launch_isolated.py fetch <root> --run <id>`).

   Before then, a packaged build of the integration branch is close enough to catch
   packaging-only bugs. Run
   `cd src/gaia/apps/webui && npx electron-builder --win dir --config electron-builder.yml --publish never`
   to get an unsigned `dist-app/win-unpacked/gaia-desktop.exe`, with no installer, version
   file or signing needed. Launch it with `--wheel` set to the integration wheel. Computer
   use is often denied for unsigned or dev executables, so plan to drive it with
   `cdp.py` and take native captures with `capture_window.py --pid`, and say so in the report.

Every retest is a new round:

```bash
python $SK/test_report.py round $RUN --label "retest: integration@abc1234 with fixes for F1-F6"
python $SK/test_report.py retest $RUN          # one retest item per finding marked fixed
```

Each retest replays the finding's steps **and** the P1 items on the same screen,
because fixes break neighbours. A retest that passes moves the finding to `verified`
(`link F3 --status verified --note "R2-F3 passed, shots/2xx"`). A retest that fails
moves it to `reopened`, and the loop goes back to step 7. New bugs found in a retest
get findings like any other, and they force another round.

**Stop when `test_report.py gate $RUN` exits 0.** At that point every item has a
result, every failure has a finding, every finding is verified, `wontfix` or
`duplicate` (with a reason), and the last round found nothing new. If you have to stop
early, the gate's output is the honest list of what's left. Put it in the report.

## 9. Publish the report

`report.md` is the deliverable. Publish it as an Artifact (load `artifact-design`
first), give the person the link, and update it after every round. If the person asks
for "just the table in chat", do that instead. The report must say:

- the verdict in one sentence, first
- which build was tested, and how: packaged app, dev Electron, or browser pane
- what was **not** tested, and why
- every finding, with its issue, fix PR and retest result
- the benchmark headline, or "not run" or "not graded", with the reason

Keep tables tight, and drop anything that wasn't measured.

Finally, `launch_isolated.py stop` every home you started, confirm `isolation` reports
the real profile untouched, and delete the throwaway homes unless the person wants
them.
