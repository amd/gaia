# Skills hub validation

**Status:** Phases 1–5 built; Phase 6 (baselines) waits on a real sweep on the eval pool.
**Goal:** every skill published to the hub is proven usable by the flagship agent: the agent can find it in the hub, install it, pick it for a plain request, and do the job with it, on each model tier GAIA ships.

Before this work, checks proved a skill was well-formed and safe to merge, not that the agent could find or use it. Four of the 17 published skills had no behavioural eval, 22 of 23 task evals told the agent which skill to load, hub search missed most plain-language queries, and the last baseline scored the skill categories at 50–57%.

## What existed

| Layer | When | What it proves |
|---|---|---|
| `gaia skill audit` (`skill_audit.yml`) | Every PR, required | No known-bad code, permissions match the code, supply-chain checks. Claude review is advisory. |
| Format and load tests (`tests/unit/test_starter_skills.py`, `test_skill_catalog.py`, ~40 `test_skill*` files) | PRs touching skills or code | Each `hub/skills/*` skill parses, is publishable, names only real tools, discovers, loads into an agent, imports via the CLI from a clean home; the catalogue stays within budget. |
| Scenario coverage (`tests/unit/eval/test_skill_scenario_coverage.py`) | PRs touching code | Each skill has `skill_<name>_*.yaml` scenarios or a `DEFERRED` reason. Only four skills were credited; 13 were deferred. |
| Publish (`release_skills.yml`) | Final release | Signs and uploads. Nothing read the live hub back. |
| Agent evals (`eval_flagship.yml`) | RC tags, Strix Halo pool, fixture hub | `gaia_skills_tasks` (23), `gaia_skills_lifecycle` (14), `gaia_skills_capture` (10). |

## Gaps and what closed them

| # | Gap | Fix |
|---|---|---|
| 1 | No scenario exercised `image-gen`, `inbox-triage`, `music-gen` or `transcribe-meeting`; `coding` and `recommendations` had one each. The coverage test credited only `skill_*` filenames, so it deferred skills the task scenarios already covered. | Ten task scenarios (two per uncovered skill, one edge case each for `coding` and `recommendations`). The coverage test now also credits a `gaia_skills_tasks` scenario whose first turn is "Load the X skill."; `DEFERRED` is empty and every skill has at least two. |
| 2 | Skill selection was unmeasured. | New `gaia_skills_selection` category (9 scenarios): pick the skill from a plain request, pick none, choose between overlapping skills, and decline honestly on a compact model. |
| 3 | Hub search was never checked against the real catalogue, and nothing read the live hub after a publish. | `tests/unit/test_skill_hub_findability.py` publishes every hub skill to an in-memory hub through the real publish path and checks 35 plain queries. `util/verify_published_skills.py` installs every skill from the live hub and checks its version and AMD signature; `release_skills.yml` runs it after each publish. |
| 4 | Which skills each tier can run was implicit. | `tests/unit/test_skill_tier_matrix.py` pins it (compact cannot run `data-explore`, `image-gen`, `inbox-triage`) and checks the compact catalogue hides them and `load_skill` refuses them with "needs a larger model". |
| 5 | Stale, partial baseline. | Open — Phase 6. |
| 6 | Skill-only PRs got no pointer to behavioural checks. | `skill_audit.yml` lists the changed skills' scenarios and the `scenarios` input for `eval_flagship.yml` in its summary and PR comment. |
| 7 | Hub search matched the whole query as one substring. | `search_skills` matches per word (stopwords dropped, one suffix stemmed) and ranks name hits and exact phrases first. Against the live catalogue, the intended skill was found for 11 of 28 plain queries before and ranks first for 28 of 28 after. |

## Run only when relevant

Nothing here adds work to a PR that doesn't touch skills:

| Check | Runs when | Mechanism |
|---|---|---|
| Tier matrix, findability, verify-script tests | A PR touches `src/**`, `hub/skills/**` or `tests/**` | `tests/unit/`, under `test_unit.yml`'s existing `paths:` filter and per-change test selection. |
| Scenario coverage test | Also when a PR touches `eval/scenarios/**` | One extra step in `test_eval.yml`, whose filter already covers scenarios. `test_unit.yml` is unchanged. |
| Scenario list and dispatch input | A PR touches a skill directory | A step in `skill_audit.yml` gated on its existing changed-skill detection. Never dispatches. |
| Live hub verification | After a real (non-dry-run) skills publish | A `verify` job in `release_skills.yml`. No schedule. |
| Agent evals | RC tags, the nominated RC, or manual dispatch | The existing `eval_flagship.yml` triggers. Never on PRs. |

## Model-dependent scenarios

Scenarios that need a model the machine may lack skip with `SKIPPED_NO_MODEL` and the reason, like `requires_asr` already did:

- `requires_sd` (SDXL-Turbo) and `requires_music` (ACE-Step-Music).
- `requires_full_tier` / `requires_compact_tier`: run only when the agent model has that feature tier, so a skill's use scenario and its honest-refusal scenario never score against the wrong model.

## Phase 6 — Baselines and targets (open)

- Re-capture `gaia-flagship/` for `gaia_skills_tasks`, `gaia_skills_lifecycle`, `gaia_skills_capture` and `gaia_skills_selection` from a real full sweep on the pool; same for `gaia-flagship-qwen/`.
- Target 80% per skill category on each model (a goal, not a gate).
- Never edit baseline numbers by hand.

## Open questions

- Compact models hide unavailable skills from the catalogue and refuse them at load. Should the catalogue instead list them as unavailable, so the agent can explain before trying? The honesty scenarios measure today's behaviour either way.
- Should SDXL-Turbo and ACE-Step-Music be on the eval pool image? Until they are, `image-gen` and `music-gen` scenarios skip in CI.
