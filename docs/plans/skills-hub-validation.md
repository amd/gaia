# Skills hub validation

**Status:** plan. Nothing built.
**Goal:** every skill published to the hub is proven usable by the flagship agent: the agent can find it in the hub, install it, pick it for a plain request, and do the job with it, on each model tier GAIA ships.

Today's checks prove a skill is well-formed and safe to merge. They do not prove the agent can find or use it. Six of the 17 published skills have no behavioural eval, almost every task eval tells the agent which skill to load, and the last baseline scored the skill categories at 50–57%.

## What exists today

| Layer | When | What it proves |
|---|---|---|
| `gaia skill audit` (`skill_audit.yml`) | Every PR, required | No known-bad code, permissions match the code, supply-chain checks. Claude review is advisory. |
| Format and load tests (`tests/unit/test_starter_skills.py`, `test_skill_catalog.py`, ~40 `test_skill*` files) | Every PR | Each `hub/skills/*` skill parses, is publishable, names only real tools, discovers, loads into an agent, imports via the CLI from a clean home; the catalogue stays within budget. Parametrized over the lane, so new skills are covered automatically. |
| Publish (`release_skills.yml`) | Final release | Signs and uploads. Nothing afterwards reads the live hub back. |
| Agent evals (`eval_flagship.yml`) | RC tags, Strix Halo pool, fixture hub | `gaia_skills_tasks` (23), `gaia_skills_lifecycle` (14), `gaia_skills_capture` (10). |

## Gaps

1. **No task scenario for six skills:** `image-gen`, `inbox-triage`, `music-gen`, `transcribe-meeting`, `summarize`, `file-ops`. Other categories exercise the underlying tools, never the skill.
2. **Skill selection is unmeasured.** 22 of 23 task scenarios open with "Load the X skill", which skips catalogue discovery. A request like "tell me when this gets cheaper" reaching `price-watch` on its own is never scored.
3. **Hub search never runs against the real catalogue.** Lifecycle evals point `GAIA_HUB_URL` at `tests/fixtures/gaia/fixture_hub`. Whether a query finds the right skill among the 17 real ones, and whether a published, signed skill installs from production, is unchecked.
4. **Tier availability is implicit.** The `compact` tier (Gemma-4-E4B, GPU and FLM — the default on most PCs) withholds `search_skill_hub`/`install_skill`/`remove_skill`, the scratchpad, email and image generation (`src/gaia/agents/base/model_features.py`). So `data-explore`, `inbox-triage` and `image-gen` cannot run there, and the agent cannot browse the hub. No test states which skills each tier can run or what the agent tells the user when one can't.
5. **Stale, partial baseline.** `gaia-flagship/` is from 2026-10-03 and predates most scenarios (18/23 task, 7/14 lifecycle, 4/10 capture). `skills_hub_search` and `skills_install_from_hub` failed. `gaia-flagship-qwen/` is empty.
6. **Skill-only PRs get no behavioural check** before the next RC.
7. **Hub search is a literal substring match.** `search_skills` (`src/gaia/skills/hub.py`) keeps entries whose name, description or tool names contain the whole query string, in catalogue order. "track price" or "meeting notes" return nothing even though `price-watch` and `transcribe-meeting` fit. Whether the agent finds a skill depends on it guessing a phrase that appears word-for-word.

## Plan

Ordered so each phase is useful on its own. Phases 1 and 3 need no eval hardware; Phase 2's scenarios can be written anywhere but only run on the pool.

### Phase 1 — Static coverage guards (CI, every PR)

- **Coverage manifest.** A unit test that fails when a `hub/skills/<name>` has no scenario under `gaia_skills_tasks` that names it in `setup` and exercises it, so a new skill can't ship without one. Mirror the parametrization in `test_starter_skills.py`.
- **Tier matrix test.** For each tier in `model_features.TIERS`, compute which hub skills have all `tools_required` available after `withheld_tools()`. Pin the result in a small checked-in table and assert it, so a tier change or a new skill requirement is a visible diff. The skill catalogue shown to a compact model must omit, or mark unavailable, the skills it cannot run.
Done when: both tests are in `tests/unit/`, green, and the coverage test currently fails on the six uncovered skills (marked `xfail` with the tracking issue until Phase 2 lands).

### Phase 2 — Fill task coverage (scenarios)

One scenario per uncovered skill, using fixtures that already exist where possible, each started from a cold `~/.gaia/skills`:

| Skill | Fixture | Pass means |
|---|---|---|
| `inbox-triage` | `tests/fixtures/gaia/email/eval_inbox.mbox` | Groups planted messages, flags the planted urgent one, invents none |
| `transcribe-meeting` | `tests/fixtures/gaia/media/facilities_update.wav` | Transcript carries the planted fact; brief lists the planted action item |
| `summarize` | a long fixture doc with a fact planted in the back half | Summary keeps the back-half fact |
| `file-ops` | temp tree | Edits only the asked file; confirms before a delete |
| `image-gen` | none (needs SD model) | `generate_image` called; reports the output path that exists |
| `music-gen` | none (needs ACE-Step) | `generate_music` called; WAV exists and is non-empty |

`image-gen` and `music-gen` need models the pool may not have; gate them on model availability and report `BLOCKED_BY_ARCHITECTURE` rather than passing or skipping silently. Each scenario also gets a compact-tier expectation where the skill is unavailable there (see Phase 4).

### Phase 3 — Real-catalogue and publish verification

- **Make search match words, not one substring.** Change `search_skills` to match on query tokens (every token, or a simple score over name/description/tool hits) and order by score. Keep it deterministic and dependency-free.
- **Search findability on the real catalogue.** A deterministic test that builds a catalogue index from `hub/skills/` in the shape the hub serves, injects it through `search_skills(fetcher=..., cache_path=tmp)`, and runs ~3 short, plain queries per skill (what a user or the agent would type: "price drop", "meeting notes", "csv totals"). Assert the intended skill is returned, in the top 3 once ranking exists. No LLM; runs on every PR. Queries live next to the test, so a description edit that breaks findability fails CI. Written first, it documents gap 7 as failing cases.
- **Post-publish smoke** in `release_skills.yml`, after upload: list the production hub and diff names and versions against `hub/skills/`; install every skill into a clean `GAIA_CONFIG_DIR` from the production URL and verify its signature. Fails the release job loudly; never retries into success.
- **Scheduled live check.** The same smoke weekly, so an expired key, a Worker regression or a stale cache is caught between releases.

### Phase 4 — Selection and end-to-end agent evals

New category `gaia_skills_selection`:

- **Pick the skill.** For each hub skill, one or two plain requests that don't name it. Pass: the agent loads the intended skill (`load_skill` in `agent_tools[]`) and completes the turn using its tools. Reuses Phase 2's fixtures.
- **Pick none.** Requests no skill fits; pass if no skill is loaded.
- **Disambiguate overlaps.** `summarize` vs `document-brief` vs `transcribe-meeting`; `source-watch` vs `price-watch` vs `rss-digest`; `research-report` vs `daily-brief`.
- **Find, install, use.** Skill not installed; the request needs it. Pass: search, then install with the user's confirmation, then load and use, all in one conversation. Run against the fixture hub populated from the real `hub/skills/` (signed with the throwaway dry-run key), not the hand-made fixture set.
- **Compact tier honesty.** On Gemma-4-E4B, a request needing an unavailable skill (`data-explore`, `inbox-triage`, `image-gen`) or hub install. Pass: the agent says plainly that this model can't do it and what changes that, without fabricating results. This is also the evidence for turning `skill_hub` back on for compact in `model_features.py`.

Add the category to a lane in `eval/ci_lanes.json` (the `gaia_skills_tasks` lane has room) and to the RC smoke tier for a handful of scenarios.

### Phase 5 — Per-PR targeted eval for skill changes

`eval_flagship.yml` already accepts `categories` / `scenarios` on dispatch. Add a lightweight job to `skill_audit.yml` that, when a PR touches `hub/skills/<name>`, posts the exact scenario list covering that skill and a one-click dispatch link. It does not auto-dispatch; the Strix Halo pool is too scarce for per-PR runs. The scenario list comes from the Phase 1 coverage manifest.

### Phase 6 — Baselines and targets

- Re-capture `gaia-flagship/` for the three skill categories plus `gaia_skills_selection` from a real full sweep on the pool, once Phases 2 and 4 land. Same for `gaia-flagship-qwen/`.
- Target 80% per skill category on each model (a goal, not a gate, matching the flagship target), plus a per-skill pass matrix in the scorecard so a single skill dropping is visible.
- Never edit baseline numbers by hand; replace from a newer real run.

## Success criteria

- Every hub skill has a task scenario and a selection scenario; CI fails when one is missing.
- The real catalogue's search relevance is checked on every PR; each release verifies the live hub matches the repo and every skill installs with a valid signature.
- The tier matrix is checked in, and on the compact tier the agent explains rather than fabricates when a skill is unavailable.
- Fresh Gemma and Qwen baselines exist for every skill category, with a per-skill matrix.

## Open questions

- Should the catalogue hide unavailable skills on compact, or list them as unavailable so the agent can explain? The plan assumes "list as unavailable"; it is the one that makes the honesty eval meaningful.
- Do `image-gen` and `music-gen` models belong on the eval pool image, or do those two stay `BLOCKED_BY_ARCHITECTURE` in CI and get covered by a manual on-hardware run each release?
