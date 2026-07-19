# Port triage — the 5 external-dependency agents

**Cohort:** `docker`, `blender`, `jira`, `sd`, `emr` — the legacy agents whose core
capability lives *outside* the GAIA process (Docker daemon, Blender + MCP bridge,
Atlassian REST, GPU/VRAM for diffusion models, a VLM + a watched directory).

**Baseline:** `origin/main` at the post-#2060 hub layout, `hub/agents/<id>/python/`.
**Reference for "publish-ready":** the email agent (`hub/agents/email/`).
**Sibling audit:** [`port-audit-6-agents.md`](port-audit-6-agents.md) (chat/doc/file/browser/analyst/code).
That document was written against the **pre-#2060** paths (`hub/agents/python/<id>/`);
every path it cites still exists, one directory level rearranged. Nothing here
contradicts it — this audit adds the PORT/MERGE/DISCARD layer its cohort did not need.

Every claim below was verified by reading the file at `origin/main`. Nothing is inferred
from naming.

---

## Executive summary

1. **Only one of the five is a straightforward port.** `docker` and `sd` fill genuine
   gaps in the 23-agent program; `blender` fills a gap nobody asked for; `jira` and
   `emr` both have a *named successor* already in flight and should not ship as
   standalone hub packages.

2. **`emr` must not publish as-is.** It declares `category: healthcare`
   (`emr/python/gaia-agent.yaml:8`) and its README claims **"100% on-device,
   HIPAA-friendly"** (`emr/python/README.md:27`), while the code stores plaintext SSNs
   (`constants.py:137`) and raw form images (`constants.py:207`) in an unencrypted
   SQLite file at a CWD-relative path (`agent.py:94`). The same README declares it a
   proof-of-concept two lines earlier (`README.md:6-7`). Milestone #48's A12 `health`
   (#1496) explicitly names EMR as its *foundation*, and the VLM-extraction mechanism
   is already slated to become shared layer AH-L4.2. See §5.

3. **Four of five have dead capability shipped in the wheel.** `blender` has 4 of its 9
   tools commented out (`agent.py:256, 327, 380, 409`) and three whole `core/` modules
   unreachable; `jira` ships a 219-line `jql_templates.py` the agent never imports;
   `sd`'s only registered tool is a *storytelling* tool, not image generation; `emr`
   ships a React+Electron dashboard that cannot render from a wheel (§5).

4. **`tools_count: 0` in all five manifests, against 4/5/3/1/5 real tools.** Same
   unguarded drift the sibling audit found (`port-audit-6-agents.md:18-20`). The
   cohort-wide count is now **0-declared vs 18 real across 11 of 11 legacy agents**.

5. **`api_server: true` in all five, with no REST surface in any.** Per the sibling
   audit this is a known repo-wide false claim, not a fresh finding
   (`port-audit-6-agents.md:531-532, 546-548`) — the correct framing is that it now
   extends to **11 of 11**. Notably `interfaces:` has **no consumer at all** in
   `src/gaia/agents/registry.py`; it is inert manifest text.

6. **Eval coverage is zero for all five, and cannot be otherwise today.** The
   `agent_type:` values in the scenario corpus are ChatAgent prompt-profile branches
   (`chat`/`doc`/`data`/`file`/`email`), not hub package ids — verified by
   `grep -rho '"agent_type"...' src/gaia/eval/ tests/`. The only `SCORECARD.md` in the
   repo is `hub/agents/email/npm/SCORECARD.md`. All five are **completely dark**.

7. **The strongest cross-audit conclusion: build the factory first.** The sibling audit
   put the parity kit at ~40–60 files per agent × 6 (`port-audit-6-agents.md:808-810`).
   Under this triage only **2.5 of my 5** are actually port targets — which is the
   point: triage first, then automate, then port. Porting all 11 blindly is ~500 files
   of which several hundred would be for agents that should not ship.

---

## Per-agent findings

Each section: **verdict** → generalization scope → fail-loudly violations → test/eval
reality → v2 contract gaps.

---

### 1. `docker` — **VERDICT: PORT** (re-scope required)

**Why:** the only cohort member filling a domain with no planned successor and no
serious blocker. A repo-wide grep of both `agent-hub-22-agents-spec.md` and the #48
issue bodies finds **no** infrastructure / DevOps / container agent. The nearest
candidates miss: A4 `code` (`agent-hub-22-agents-spec.md:231`) is repo-context only
(`repo_status`, `restore_context`, `review_prs`, `dep_advisories`), and A20 `security`
(`:287`) is identity/filesystem. Small surface (626 LOC, 4 tools), no credential
handling, no compliance exposure, and the cheapest generalization in the cohort.

*Do not confuse with `docs/plans/docker-containers.mdx`* — that is about shipping GAIA
itself in Docker images, not this agent.

#### Generalization scope

**Capability-truth failure — the manifest advertises a verb that has no tool.**
`gaia-agent.yaml:4` says *"Container management — build, run, and **inspect** Docker
containers"*. The registered tools are `analyze_directory` (`agent.py:146`),
`save_dockerfile` (`:163`), `build_image` (`:186`), `run_container` (`:203`). There is
no inspect, no `ps`, no `logs`, no `stop`, no `rm`, no `exec`, no image listing. The
agent is a **Dockerfile generator that can build and detach once** — it cannot manage a
container after it starts. `build_registration()`'s first conversation starter is
*"List my running containers"* (`__init__.py:43`) — a prompt with no tool behind it.

**Missing capability a user would obviously expect:** container lifecycle (`ps`, `logs`,
`stop`, `rm`, `restart`), image management (`images`, `rmi`, `pull`, `push`),
`docker-compose` (the agent *detects* `docker-compose.yml` at `agent.py:317` and then
does nothing with it), and multi-stage / non-root Dockerfile generation that its own
system prompt recommends (`agent.py:91`) but never verifies.

**Hardcoded assumptions:**
- `agent.py:26` `DEFAULT_PORT = 8080`, then used as the port for **Flask, Django and
  FastAPI alike** (`:258, :261, :264`) — the system prompt itself says Flask is 5000
  (`:104, :123`), so the analyzer and the prompt disagree. `save_dockerfile`'s signature
  defaults to `port: int = 5000` (`:164`), a third value.
- Framework detection is a substring match on the lowercased `requirements.txt`
  (`:254-264`) — a `pytest-flask` dev dependency classifies a Django app as Flask. No
  `pyproject.toml`, no `Pipfile`, no `poetry.lock`, no `uv.lock` support: a modern
  Python project with no `requirements.txt` is detected as `app_type: "unknown"`
  (`:240`).
- Only Python and Node are detected. Go, Rust, Java, .NET, Ruby → `"unknown"`.
- `_build_image` preflights `docker --version` (`:401-407`) — that checks the **binary**,
  not the **daemon**. With Docker Desktop installed but stopped, the check passes and
  the build fails 5 minutes later with a raw stderr dump. `_run_container` has **no
  preflight at all** (`:450-502`).
- `_run_container` also skips the `path_validator` gate that all three other tools apply
  (`:228, :343, :393`) — no image-name validation, and the `port` string is passed
  straight to `docker run -p` (`:460`).

#### Fail-loudly violations

- `agent.py:311-312` — `except Exception as e: logger.warning(f"Could not parse package.json: {e}")`.
  A malformed `package.json` silently yields a result dict with no `entry_point` and no
  framework, and the LLM then generates a Dockerfile from a partial analysis it has no
  way to know was partial.
- `agent.py:413-414` / `:447-448` / `:501-502` — three bare `except Exception` handlers
  converting every failure (including `FileNotFoundError` on the docker binary) into a
  `{"status": "error", ...}` string dict. These are tool-boundary translations, which
  CLAUDE.md permits — but they catch `Exception`, not a named class, so a bug in the
  agent is indistinguishable from a Docker failure in the result.
- `agent.py:385-386` — `except Exception` around the Dockerfile write; a read-only
  filesystem and a disk-full condition produce the same opaque string.

#### Test reality

**The weakest suite in the cohort: 55 lines, 5 tests, 0 tool invocations.**
`tests/test_docker_agent.py` checks import (`:15`), construction (`:27`), the default
model id (`:31`), two substrings in the system prompt (`:35`), and that four tool
*names* are present in `agent._tools_registry` (`:42`). **0/4 tools behaviorally
exercised (0%); 4/4 (100%) presence-checked.** No test calls `_analyze_directory`
against a fixture tree — which is the one thing here that is trivially deterministic to
test and is currently the source of the port/framework bugs above.

#### Eval readiness

Completely dark. Zero scenarios, zero ground truth, zero baseline. A deterministic
vehicle is unusually easy here and should be built before the port: fixture app trees
(flask / express / django / go / bare) → assert the analyzer's `app_type`, `port`,
`entry_point` by exact match; then `docker build` the generated Dockerfile in CI and
assert exit 0. That is a *build-passes* metric, not an LLM judge — the pattern the
`adding-eval-scorecard` skill asks for.

#### v2 contract gaps

- `gaia-agent.yaml:11` `tools_count: 0` vs **4** real. `build_registration()` hardcodes
  the same 0 (`__init__.py:53`). The registry reads `AGENT_TOOLS_COUNT` off the class
  (`src/gaia/agents/registry.py:835, 1027`); `DockerAgent` does not define it.
- `gaia-agent.yaml:31` `api_server: true` — no `server.py`, no `api_routes`, no OpenAPI.
  (Cohort-wide; see Executive summary §5.)
- `gaia-agent.yaml:32` `mcp_server: true` — **this one is partly true and the exception
  in the cohort.** `DockerAgent` extends `MCPAgent` and implements
  `get_mcp_tool_definitions` (`agent.py:505`) and `execute_mcp_tool` (`:529`), exposing
  a single `dockerize` tool. That MCP tool then re-enters the LLM via
  `process_query` (`:587`) rather than calling the tools directly — so the MCP surface
  is non-deterministic by construction.
- Entry point `docker = "gaia_agent_docker:build_registration"`
  (`pyproject.toml:16`) — registers under the singular `gaia.agent` group, matching
  `AGENT_ENTRY_POINT_GROUPS`; **resolves**.
- No `permissions:` block, despite the agent shelling out to `docker run`.

---

### 2. `blender` — **VERDICT: DISCARD** (keep as the in-repo MCP-passthrough example)

**Why:** three converging reasons.

1. **Nothing in the 23-agent program touches 3D, and nothing will.** A13 `photo`
   (`agent-hub-22-agents-spec.md:264`) is 2D library *retrieval*; A21 `presentation`
   (`:290`) is text/narrative; layer L4 (`:173-180`) is VLM *comprehension* of existing
   media, not authoring. There is no pull.
2. **Nearly half the shipped agent is dead.** Four tools are commented out —
   `_get_object_info` (`agent.py:256`), `_delete_object` (`:327`),
   `_execute_blender_code` (`:380`), `_diagnose_scene` (`:409`) — leaving 5 live tools.
   `core/rendering.py` (225 LOC), `core/objects.py` (316 LOC) and `core/view.py` (146
   LOC) are exported by `core/__init__.py:4-8` and imported by **nothing**; only
   `SceneManager` (`agent.py:162`) and `MaterialManager` (`:248`) are reachable.
3. **Its actual residual value is as a reference, not a product.** It is the repo's only
   worked example of an agent driving an external MCP server, which A3 `smarthome`
   (`agent-hub-22-agents-spec.md:228`, Home Assistant MCP) will need as a template.
   Keeping it in-repo preserves that at ~zero cost; publishing it costs the full parity
   kit for a demo nobody in the program is waiting on.

*Fallback if the creative category needs a headline agent:* re-classify as **DEFER**,
not PORT — it should not be sequenced before `docker` or `sd` under any ordering.

#### Generalization scope

**Capability-truth failure — the registration advertises rendering, which has no tool.**
`__init__.py:46` ships the conversation starter *"Render the current scene"*. There is
no render tool. `core/rendering.py` contains a `RenderManager` and is never imported by
the agent. A user clicking the agent's own suggested prompt gets nothing.

**Second capability-truth failure — the agent cannot delete an object.** Its own system
prompt tells the LLM to "clear a scene" via `clear_scene` (`agent.py:128`), but
`_delete_object` is commented out (`:327`), so there is no way to remove a single
object. The agent can create and recolor, and it can nuke everything. That is the whole
scene-editing surface.

**Hardcoded assumptions:**
- `agent.py:32` `base_url: str = "http://localhost:13305/api/v1"` hardcoded in the
  constructor signature.
- `agent.py:53` `self.mcp = mcp if mcp else MCPClient()` — no host/port parameter is
  surfaced. Blender must be on the same machine, at the client's compiled-in default,
  with the GAIA addon already installed and its server started. None of that is checked,
  documented in the manifest, or expressible in `requirements:`
  (`gaia-agent.yaml:23-25` declares only memory and platforms).
- `agent.py:15` imports `MCPClient` from `gaia.mcp.blender_mcp_client` — **the "hub
  package" is not self-contained**; its transport lives in the core wheel. A published
  `gaia-agent-blender` is coupled to a core internal module path.
- `agent.py:18` `logging.basicConfig(level=logging.INFO)` at module scope — a library
  mutating the root logger on import. This reconfigures logging for the whole host
  process (Agent UI daemon included) the moment the registry imports the agent module.
- The system prompt is 57 lines of emoji-heavy, all-caps prompt-engineering scaffolding
  for one specific weakness — that the model forgets `set_material_color`
  (`agent.py:93-126`). It hardcodes an 8-colour vocabulary (`:95`) and 5 primitive types
  (`:182`). No lights, cameras, modifiers, transforms beyond loc/rot/scale, no import or
  export, no animation.

**Missing capability a user would obviously expect** from something named "3D scene
automation": render to file, import/export (`.obj`/`.fbx`/`.gltf`), lighting and camera
placement, modifiers, and materials beyond a flat RGBA base colour.

#### Fail-loudly violations

The dominant pattern: **every tool wraps its MCP call in a bare `except Exception` that
returns a soft error dict** — `agent.py:166, 216, 252, 281, 323, 352, 376, 405, 432`.
Because these catch `Exception`, a `ConnectionRefusedError` (Blender not running — the
single most likely failure) is indistinguishable from a bad argument, and the agent
loops on it: the base agent sees a tool result, not a raised exception, so it retries
against a server that is not there until `max_steps` is exhausted.

- `agent.py:496-508` — `_track_object_name` catches `Exception`, logs, and
  `return None` (`:508`), with a second `return None` on the non-match path (`:505`).
  The caller (`:456`) then silently skips the plan-rewriting that keeps subsequent
  steps pointed at the real Blender-assigned object name — the exact failure the method
  exists to prevent, now silent.
- `core/materials.py:383`, `core/view.py:106, 127`, `agent_simple.py:96, 130`,
  `app.py:123` — same pattern in the supporting modules.

#### Test reality

**1,155 lines of tests that never touch a tool.** `tests/test_agent.py` (597 lines) is
entirely about the *base agent's* JSON/plan machinery as observed through this
subclass: `test_valid_json_parsing` (`:275`), `test_json_correction_for_single_quotes`
(`:308`), `test_retry_mechanism` (`:350`), `test_graduated_retry_escalation` (`:369`),
`test_extract_json_from_response` (`:419`). **0/5 live tools behaviorally exercised
(0%).** No test injects a fake `MCPClient` and asserts `create_object` forwards the
right payload — which is the entire contract of this agent, and is trivially mockable
given the constructor already accepts an `mcp` parameter (`agent.py:30`).
`test_mcp_client.py` (356 lines) tests the *core* client, not the agent's use of it.

#### Eval readiness

Completely dark, and the hardest of the five to fix — scoring a 3D scene needs either a
headless Blender in CI producing a deterministic scene-graph dump (feasible: assert
object names/types/locations/material RGBA after a prompt) or image comparison
(non-deterministic). The scene-graph assertion route is the only defensible one. This
cost is a further argument for DISCARD.

#### v2 contract gaps

- `gaia-agent.yaml:11` `tools_count: 0` vs **5** real (9 written).
- `gaia-agent.yaml:31-32` `api_server: true`, `mcp_server: true` — both false.
  `mcp_server: true` is especially misleading: this agent is an MCP **client**, not a
  server. It consumes a Blender MCP server; it exposes nothing. `BlenderAgent` does not
  set `CONSUMES_MCP_SERVERS`, which is the field the registry actually reads
  (`src/gaia/agents/registry.py:1043`).
- Entry point `blender = "gaia_agent_blender:build_registration"`
  (`pyproject.toml:16`) — **resolves**.
- **Note for the factory:** the sibling audit's §(c) prescription to derive
  `tools_count` by AST introspection (`port-audit-6-agents.md:637-639`) works fine here
  — blender's tools are static `@tool` wrappers, not runtime-discovered from the remote
  server. The commented-out decorators are exactly what an AST pass would catch.

---

### 3. `jira` — **VERDICT: DEFER, then MERGE INTO the L8 TaskStore as a connector**

**Why:** there is no issue-tracker agent in the 22 — A4 `code`'s `review_prs`
(`agent-hub-22-agents-spec.md:231`) is PRs, A16 `freelance`'s `track_project` (`:272`)
is invoices and hours. But the milestone carries an epic *outside* the spec that
collides directly: **#1521 `EPIC: L8 Task & To-Do Store (cross-agent, core)`**, with
#1522 `TaskStore backend`, #1523 `TaskToolsMixin (tasks in KNOWN_TOOLS)`, #1524
`Cross-agent task wiring (source_ref back to originating item)`, #1525 `Unified Tasks
panel`. #1524's `source_ref` is precisely *"this task came from JIRA-123"*.

Shipping `gaia-agent-jira` as a standalone agent now creates a **second task model** in
the product, weeks before L8 defines the first one. The right move is to hold, let L8
land, and re-land Jira as an external-tracker backend behind `TaskToolsMixin` — which
also gets Jira into `morning`, `code` and `freelance` for free, instead of stranding it
in one agent card.

The code survives either way: this is the cohort's **best-tested** package and its HTTP
layer is the part L8 would reuse verbatim.

#### Generalization scope

**The single strongest "only works on the author's machine" finding in the cohort.**
`agent.py:189-193`, the prompt used whenever discovery has not run or has failed:

```
- Types: issuetype = "Idea" (Note: This Jira instance uses "Idea" not "Bug"/"Task"/"Story")
- Priority: priority = "Critical", priority = "High", priority = "Medium"
- Status: status = "Parking lot", status = "In Progress", status = "Done"
```

`"Idea"` and `"Parking lot"` are one specific Atlassian site's custom workflow, asserted
to the LLM as fact for *every* user. It leaks again into an error message the agent
returns to the model — `agent.py:776` (*"try 'Idea', 'Bug', 'Story', 'Task', or
'Subtask'"*) and `:781` (*"Try using 'Idea' instead"*). A user on a stock Jira Software
project gets told to file an issue type that does not exist on their instance.

**Capability-truth failure — `jira_update`'s `status` parameter cannot work.**
`agent.py:859-860` writes `fields["status"] = {"name": status}` into a
`PUT /rest/api/3/issue/{key}` payload. Jira does not permit status changes through the
field-edit API; a transition requires `POST /rest/api/3/issue/{key}/transitions`. Every
`status=` update 400s. The tool's docstring advertises it (`:367`), the manifest
advertises "update issues" (`gaia-agent.yaml:4`), and
`test_update_payload_with_all_fields` (`tests/test_jira_agent.py:700`) asserts the
broken payload shape is produced — the test **pins the bug**.

**Dangerous default:** `agent.py:744` `project = projects[0]["key"]` — when the LLM omits
a project, the agent creates the issue in whatever project the API returns first. On a
site with 40 projects that is effectively random, and it is a **write**. There is no
confirmation gate (contrast the email agent's `needs_confirmation` pattern,
`port-audit-6-agents.md:596-601`).

**Other hardcoded assumptions / missing capability:**
- Auth is env-vars only (`agent.py:398-400`: `ATLASSIAN_SITE_URL`, `ATLASSIAN_API_KEY`,
  `ATLASSIAN_USER_EMAIL`). GAIA has a connectors framework with OAuth and a grant ledger
  (`src/gaia/connectors/`); Jira bypasses it entirely. No token refresh, no per-agent
  grant, no multi-site support, and credentials must be exported into the daemon's
  environment.
- Jira **Cloud** only — `/rest/api/3/` is hardcoded in all five call sites
  (`:452, :461, :470, :477, :626, :739, :760, :868`). Jira Server/Data Center is `/2`.
- No pagination. `jira_search` sends `maxResults` if given (`:630-631`) and never reads
  `startAt` or `nextPageToken`, so results silently truncate at Jira's default.
- Three tools total. No comments, no transitions, no attachments, no worklogs, no
  delete, no assign, no link, no sprint/board operations — despite `jira_specialist`
  being a first-class Claude agent in this repo and the manifest tagging `atlassian`.
- `jql_templates.py` (219 lines, `generate_jql_from_templates` at `:219`) is exported
  from `__init__.py:19` and **never imported by `agent.py`** — dead code in the wheel.
  Its tests live in the *core* repo (`tests/test_jira_agent.py:18` points at
  `tests/unit/agents/test_jql_templates.py`), so the published package ships a module
  with no tests inside it.
- `agent.py:32-34` is a comment referencing "see agent.py:635" — the line it describes is
  now at `:643`. Already-rotted line-number comment; exactly what CLAUDE.md's
  "Code Comments — Short or Skip" rule exists to prevent.

#### Fail-loudly violations

- **`agent.py:287-296` — the textbook case.** `initialize()` catches `Exception`, logs,
  and then:
  ```python
  # Set empty config so we don't retry
  self._jira_config = {"projects": [], "issue_types": [], "statuses": [], "priorities": []}
  return self._jira_config
  ```
  Bad credentials, a wrong site URL, an expired token, a network failure — all produce a
  *successful-looking* return of an empty config. The agent then proceeds with the
  hardcoded "Idea"/"Parking lot" prompt above and fails on every subsequent call with an
  auth error the user has no way to trace back to setup. This is simultaneously
  default-to-empty and a swallowed retry.
- **`agent.py:483-484`** — inside `_discover_jira_config`, `except Exception as e:
  logger.warning(...)`. Each of the four discovery calls is guarded only by
  `if response.status == 200` (`:454, :463, :472, :481`); a 401 on projects and a 200 on
  priorities yields a *partially* discovered config that is indistinguishable from a
  complete one. The agent then tells the LLM those are the available projects — an empty
  list — and the LLM concludes the user has no projects.
- `agent.py:515-516`, `:552-553`, `:584-585` — the three sync wrappers catch `Exception`
  and return `{"status": "error", "error": f"Async execution failed: {str(e)}"}`. This
  also swallows `concurrent.futures.TimeoutError` from the 30s `future.result(timeout=30)`,
  reporting a timeout as a generic execution failure.
- `agent.py:778-782` — `except Exception:` (bare, no binding) around parsing a 400 body,
  falling through to the hardcoded "try 'Idea'" message.

#### Test reality

**The cohort's best suite, by a wide margin: 782 lines, ~33 tests, all three tools
behaviorally exercised against a fake HTTP layer.** It asserts *outgoing call shape*,
which is exactly what CLAUDE.md's "mocks prove we called it, not that the call is valid"
rule demands: `test_search_request_shape_defaults` (`:356`),
`test_create_request_shape_with_explicit_project` (`:475`),
`test_update_payload_contains_only_provided_fields` (`:673`),
`test_discovery_request_shape_and_parsing` (`:248`). It also covers credential handling
properly (`test_missing_env_raises_actionable_error` `:194`, `test_partial_env_raises`
`:209`, `test_bare_host_gets_https_prefix` `:223`). **3/3 tools behaviorally exercised
(100%).**

Two caveats: `test_initialize_returns_empty_config_on_failure` (`:333`) **enshrines the
fail-loudly violation as intended behavior** — fixing `initialize()` requires deleting
this test, not just editing it. And `test_update_payload_with_all_fields` (`:700`) pins
the broken `status` payload, as noted above.

#### Eval readiness

Completely dark. But of the five, jira has the clearest deterministic vehicle: the
existing fake-HTTP fixture already produces exact request shapes, so a natural-language →
JQL scorecard (fixed prompt set → exact-match expected JQL string) needs no live
Atlassian instance and no LLM judge. If jira is revived under L8, that harness should be
built there.

#### v2 contract gaps

- `gaia-agent.yaml:11` `tools_count: 0` vs **3** real.
- `gaia-agent.yaml:31-32` `api_server: true`, `mcp_server: true` — both false; `JiraAgent`
  extends plain `Agent` (`agent.py:61`) with no MCP mixin.
- Entry point `jira = "gaia_agent_jira:build_registration"` (`pyproject.toml:16`) —
  **resolves**.
- No `permissions:` and no credential declaration. A hub card for an agent that performs
  authenticated **writes** to a corporate issue tracker carries no machine-readable
  signal that it does so.

---

### 4. `sd` — **VERDICT: PORT the capability; DISCARD the current package shape**

**Why PORT:** image *generation* is a capability gap across **all 23** planned agents.
Layer L4 (`agent-hub-22-agents-spec.md:173-180`) is read-side only — AH-L4.1 photo index,
AH-L4.2 document extraction, AH-L4.3 `media_search`. A13 `photo` (`:264`) organizes an
existing library; A21 `presentation` (`:290`) emits `slide_outline` and `talking_points`,
i.e. text. Nothing generates a pixel. `presentation`, `photo`, `family` and `writing`
would all consume a generation layer.

**Why DISCARD the shape:** the `sd` *package* is not an image-generation agent. Its 222
lines are a config dataclass plus **one** registered tool — `create_story_from_image`
(`agent.py:161`), which writes a short story. The actual generation comes from
`SDToolsMixin`, which lives in the core wheel (`gaia.sd`, `agent.py:17`) and is already
registered in `KNOWN_TOOLS`. So the published wheel's own contribution is a storytelling
feature that appears **nowhere** in its manifest description
(`gaia-agent.yaml:4`: *"Image generation — LLM-enhanced prompts for Stable Diffusion"*),
its tags (`:9`), or its conversation starters (`__init__.py:43-46`).

Port target: promote SD generation to a shared layer alongside L4, keep a thin `sd`
agent over it, and **delete `create_story_from_image`** (or move it to a demo).

*Cross-reference:* the sibling audit found the *other* SD surface —
`port-audit-6-agents.md:87-89` reports that inside ChatAgent, SD tools are off unless
`config.enable_sd_tools=True` and the SD prompt section is dropped unless
`sd_default_model` is set, so *"generate an image" silently has no tool*. SD capability
therefore exists in two places, one of which is dead by default. **The port must declare
which is canonical.** This audit's recommendation: the layer is canonical; ChatAgent
composes it; the standalone `sd` agent becomes a thin card over the same layer.

#### Generalization scope

**Hardcoded assumptions:**
- `agent.py:36` `base_url: str = "http://localhost:13305/api/v1"` — hardcoded in the
  config dataclass default.
- `agent.py:28` `output_dir: str = ".gaia/cache/sd/images"` — **relative**. Resolved
  against the process CWD, so images land in a different place depending on where the
  daemon was started, and `create_story_from_image`'s recovery hint (`:174`) points at
  whichever `.gaia` happens to be under the current directory.
- `agent.py:28` `sd_model: str = "SDXL-Turbo"` and `agent.py:140` pins
  `model="SDXL-Turbo", size="512x512", steps=4` **inside the system prompt**. Any other
  model (SD 1.5, SDXL base, Flux) needs a different step count and resolution; the prompt
  will steer the LLM to Turbo's parameters regardless of `config.sd_model`.
- `agent.py:120` `self.init_vlm(model="Gemma-4-E4B-it-GGUF")` — the VLM model is
  hardcoded at the call site and **ignores** `config.model_id` (`:37`), so a user who
  switches models gets a silently mismatched VLM.
- `agent.py:42` `ctx_size: int = 16384` with `min_context_size=config.ctx_size` (`:109`).
  Per CLAUDE.md the context window is now pinned per **device profile**
  (`GPU_CTX_SIZE`/`NPU_CTX_SIZE`), not per agent — this agent still asserts its own,
  and `save_options=True` (`:90`) **persists** it, so constructing `SDAgent` mutates the
  machine's saved Lemonade options for every other agent.
- `gaia-agent.yaml:25` `gpu_vram_gb: 6` is the **only** hardware requirement declared in
  the whole cohort — good, but it is unenforced (see §Install-time checks) and the
  manifest still lists `platforms: [win-x64, linux-x64, darwin-arm64]` (`:26`) with no
  statement about which SD backends actually work on which.

**Missing capability a user would obviously expect** from an image-generation agent:
img2img, inpainting, ControlNet, negative prompts, seeds/reproducibility, batch
variations, upscaling, LoRA selection. The manifest's own value proposition —
"LLM-enhanced prompts" — has no dedicated tool either; enhancement happens implicitly
inside the LLM turn, so there is nothing to test or score.

#### Fail-loudly violations

- **`agent.py:95-96`** — the important one:
  ```python
  except Exception as e:
      logger.warning(f"LLM load warning: {e}")
  ```
  wrapping `llm_client.load_model(...)` (`:84`). If the model fails to download, the
  server is unreachable, or VRAM is insufficient, construction *succeeds* and the agent
  proceeds to `super().__init__()` with `min_context_size=16384` against a model that was
  never loaded. The user's first prompt fails somewhere deep in the agent loop with an
  error that names neither the model nor the load failure. This is the exact "config
  loader defaults a missing value" anti-pattern CLAUDE.md calls out, applied to a model
  load.
- `agent.py:182-183` — `except (OSError, AttributeError): hint = ""`. Narrower and
  defensible (it degrades a *hint*, not a result), but it is still default-to-empty and
  the comment at `:170-173` explicitly rationalizes it. It should log at debug rather
  than vanish.

#### Test reality

**396 lines that largely test the test's own mocks.** `test_story_file_creation`
(`tests/test_sd_agent.py:28`) defines `mock_create_story` (`:47`), assigns it onto the
agent (`:55`), and then calls **the mock** (`:66`) and asserts on its output — the real
tool is never invoked. `test_system_prompt_extraction` (`:98`),
`test_model_specific_prompts` (`:122`) and
`test_system_prompt_pins_literal_PREV_directive` (`:375`) are string assertions on the
prompt.

Two tests do exercise the real tool, and both only its error path:
`test_create_story_error_includes_recent_png_hint` (`:308`) and
`test_create_story_error_defensive_on_empty_dir` (`:345`), reached via `_TOOL_REGISTRY`
(`:335, :366`). **1/1 own tool partially exercised — success path never.** The headline
capability, `generate_image` from `SDToolsMixin`, has **zero** coverage in this package,
which is consistent with it not living here.

#### Eval readiness

Completely dark, and there is a specific trap: the scenario corpus contains `vision`
scenarios, but those are VLM *comprehension*. They are **not** sd coverage. Scoring
generation deterministically is genuinely hard (CLIP-similarity to a reference caption is
the usual approach and is not exact-match). Per the `adding-eval-scorecard` skill's
"stop rather than invent numbers" rule (`port-audit-6-agents.md:604-606`), the honest
position is: **sd has no eval vehicle today**, and the port should score the *prompt
enhancement* step (deterministic: does the enhanced prompt contain the required
quality/style tokens for the selected model?) rather than the image.

#### v2 contract gaps

- `gaia-agent.yaml:11` `tools_count: 0` vs **1** own tool (plus the mixin's, which the
  manifest cannot express — a real gap: there is no field for "tools contributed by a
  composed mixin").
- `gaia-agent.yaml:32-33` `api_server: true`, `mcp_server: true` — both false.
- Entry point `sd = "gaia_agent_sd:build_registration"` (`pyproject.toml:16`) —
  **resolves**.
- `SDAgent.__init__` takes a positional `config: Optional[SDAgentConfig]` (`agent.py:60`)
  while `class_factory(SDAgent)(**kwargs)` (`__init__.py:35`) passes only kwargs. It
  works — kwargs are merged onto a default config at `:72-74` — but any kwarg that is
  not an `SDAgentConfig` field is **silently dropped** (`if hasattr(config, key)`), so a
  typo'd option from the UI vanishes with no error.

---

### 5. `emr` — **VERDICT: MERGE INTO A12 `health` (#1496) + layer AH-L4.2. Do not publish.**

**Why:** the only cohort member with an *explicitly named* successor in the 23-agent
program. `agent-hub-22-agents-spec.md:259` (A12 `health`) lists
`import_wearable`, `analyze_labs`, `med_tracker`, `doctor_summary` and marks
**"Existing: EMR base (#770)"**. Issue #1496's body: *"GAIA's EMR agent (#770) is a
foundation; this extends it to consumer wellness."* Separately, the agent's core
mechanism is already scheduled to become a shared layer —
`agent-hub-22-agents-spec.md:179`, `AH-L4.2 Document/receipt structured extraction via
VLM (key-value, totals)`, and `:634`.

So: the extraction engine → AH-L4.2; the records/health domain → A12. What is *not*
covered is clinical patient-intake specifically, because A12 is scoped to consumer
wellness. **That scope decision needs an explicit call**, and the compliance findings
below argue strongly for dropping the clinical framing rather than publishing it.

**The publishing blocker.** The manifest declares `category: healthcare`
(`gaia-agent.yaml:8`) with a description containing no disclaimer (`:4`), and the README
claims **"Local Processing - 100% on-device, HIPAA-friendly"** (`README.md:27`). What the
code actually does:

- `constants.py:137` — `ssn TEXT` stored in plaintext.
- `constants.py:207` — `file_content BLOB`, the raw intake-form image, stored in the same
  file.
- `constants.py:201` — `raw_extraction TEXT`, the unredacted VLM output.
- `agent.py:94` — `db_path: str = "./data/patients.db"`, **CWD-relative**, no encryption,
  no file-mode restriction, no access control, no audit log beyond the `intake_sessions`
  table (`constants.py:232`).

"HIPAA-friendly" is not a claim this code can support, and the README itself calls the
package a proof-of-concept two lines earlier (`README.md:6-7`) — the package's own docs
contradict each other. Publishing a `healthcare`-category agent to a public hub on those
terms is a legal review item, not an engineering one. Recommend: keep in-repo as the
demo it says it is, harvest the extraction pipeline into AH-L4.2, and let A12 own the
domain with a real privacy design.

#### Generalization scope

**Constructing this agent has side effects that make it unusable as a v2 registry
citizen.** `MedicalIntakeAgent.__init__` defaults `auto_start_watching: bool = True`
(`agent.py:96`) and then, at `:151-152`, calls `_start_file_watching()` → which at
`:215` calls `_process_existing_files()` → which at `:341` calls `_on_file_created()`
→ which runs **VLM inference** on every pre-existing file. It also creates a directory
as a side effect at `:148` (`self._watch_dir.mkdir(parents=True, exist_ok=True)`).

In Agent UI v2, instantiating an agent to render a card, resolve a tool list, or answer a
capability probe would therefore: create `./intake_forms/` wherever the daemon was
launched, spawn a watcher thread, and start GPU inference. No other agent in either
cohort does work in its constructor.

**Hardcoded assumptions:**
- `agent.py:93-94` — `watch_dir="./intake_forms"`, `db_path="./data/patients.db"`. Both
  CWD-relative, so `gaia-emr watch` from two different directories silently uses two
  different databases. The README documents `./intake_forms/` as *the* location
  (`README.md:51`).
- `agent.py:95` — `vlm_model="Gemma-4-E4B-it-GGUF"` hardcoded, duplicated at `:89` in
  `AGENT_MODELS`.
- `constants.py:129-241` — the schema is a fixed 60-column table modelled on **one
  clinic's intake form** (`pain_location`, `pain_onset`, `pain_progression`,
  `work_related_injury`, `car_accident` at `:185-190` — this is a pain-management or
  orthopaedic practice). `EXTRACTION_PROMPT` (`:248-330`) hardcodes the same field set.
  There is no template mechanism: a dental, paediatric, or veterinary form would dump
  everything into `additional_fields` JSON (`agent.py:1165-1171`) and be unqueryable.
- `constants.py:28-30` — `TYPING_SECONDS_PER_CHAR = 0.3`, `BASE_SECONDS_PER_FIELD = 10`,
  `VERIFICATION_OVERHEAD = 0.15`, plus a hand-tuned per-field complexity table
  (`:33-54`). These invented constants drive the agent's headline
  **`time_saved_minutes/percent`** metric (`agent.py:1369`) that the dashboard reports as
  a benefit figure. The comment at `:14-26` cites "research" and "studies" with no
  citation. A published agent should not report a fabricated ROI number as a statistic.
- `agent.py:1094` — PDFs are processed **first page only** (`page=0`). Multi-page intake
  forms — the norm — silently lose pages 2+.
- Patient matching (`_find_existing_patient`, `agent.py:784`) is exact-equality on
  name + DOB (`:791-801`) with an **exact-name-only fallback** when the form has no DOB
  (`:805-811`). It fails in both directions: "Bob" vs "Robert" creates a duplicate
  record, and two distinct patients sharing a name are **merged into one record** —
  `_update_patient` (`:831`) then overwrites the first patient's contact, insurance and
  medication fields with the second's.

**Capability-truth check on the manifest:** `gaia-agent.yaml:4` — *"VLM extraction of
patient forms into a records database"* — is accurate for what the code does. The
inaccuracy is in what it **omits** (proof-of-concept status, the plaintext-PHI storage
model) and in the README's HIPAA claim.

#### Fail-loudly violations

This is the densest of the five. The critical ones:

- **`agent.py:227-228`** — `except Exception as e: logger.warning(f"File watching not available: {e}")`.
  The agent's single advertised capability — automatic processing of dropped forms —
  degrades to *nothing* on a warning. The agent then reports its normal status; the user
  drops forms into the directory and nothing ever happens.
- **`agent.py:353-355`** — `_get_vlm` catches `Exception`, logs, and `return None`. The
  extraction engine failing to initialize returns `None` to callers rather than raising.
- **`agent.py:1181-1184`** — `_store_patient` catches `Exception`, logs, `return None`.
  In a **medical records** agent, a failed database write is a swallowed exception and a
  `None`. Combined with the stats counters this is how a form can be reported processed
  while no record exists.
- `agent.py:208-210` — historical stats load swallowed on `Exception` with an inline
  rationalization comment (*"Don't fail if historical stats can't be loaded (e.g., schema
  mismatch)"*). A schema mismatch is precisely the thing that should fail loudly on a
  medical database.
- `agent.py:310-312` and `:323-324` — directory scan and processed-hash query both
  swallowed; `:324` degrades to `logger.debug`, so a failed hash query silently
  **reprocesses every form** (duplicate records, duplicate VLM cost).
- `agent.py:426-427` — progress-callback errors swallowed at debug level; the dashboard's
  SSE feed can go dark with no signal.
- `agent.py:378-392` — a 3-attempt retry loop over `PermissionError`. The final failure is
  logged and printed but **not raised**, and `_on_file_created` returns normally — the
  caller cannot tell the file was skipped. This is CLAUDE.md's "retry loops that swallow
  the final failure" verbatim.
- `dashboard/server.py:27-43` — FastAPI imports guarded, assigning
  `FileResponse = None` (`:40`) and `StaticFiles = None` (`:43`) on failure.
- `dashboard/server.py:478-479`, `:534`, `:1189` — three literal `except Exception: pass`
  blocks. Plus ~30 further `except Exception` handlers across the same file
  (`:218, :576, :646, :758, :787, :876, :933, :986, :1038, :1050, :1096, :1133, :1155, :1262, :1317, …`).
- `cli.py:248`, `:686` — bare `except Exception:` among ~16 handlers in that file.

#### Test reality

**The largest suite in the cohort — 1,812 lines across 4 files — and genuinely
behavioral.** `test_emr_agent.py` (863), `test_emr_cli.py` (769),
`test_init_status_context_size.py` (100), `test_dashboard_port.py` (80). All 5 tools
exist against a real SQLite fixture path, and the extraction/parse/store pipeline is
exercised. This is the one package in the cohort whose tests would largely survive the
port.

What it does **not** cover: the compliance surface. No test asserts that PHI is
encrypted, that the DB is created with restrictive permissions, that `raw_extraction` is
redacted, or that the "HIPAA-friendly" claim holds — because none of those behaviors
exist.

#### Eval readiness

Completely dark as a hub package, but it is the **best-positioned of the five** for a
deterministic scorecard, and this is a strong argument for harvesting rather than
discarding: form image → expected field dict is exact-match scoreable, no LLM judge
needed. Per-field precision/recall over a fixture set of synthetic forms is the natural
metric. That harness belongs to **AH-L4.2**, where it would score document extraction
for every consumer, not just this agent.

Note the fixture set must be **synthetic** — a real-patient corpus cannot go in the repo,
which is itself a reason the extraction layer should own the eval rather than a
healthcare-branded agent.

#### v2 contract gaps

- `gaia-agent.yaml:11` `tools_count: 0` vs **5** real (`search_patients` `agent.py:1216`,
  `get_patient` `:1271`, `list_recent_patients` `:1299`, `get_intake_stats` `:1322`,
  `process_file` `:1332`). This agent *does* set the modern class attributes
  (`AGENT_ID`…`AGENT_MODELS`, `agent.py:77-89`) — but **not** `AGENT_TOOLS_COUNT`, which
  is the one the registry reads (`src/gaia/agents/registry.py:835, 1027`).
- **`api_server: true` (`gaia-agent.yaml:31`) is the most misleading instance in the
  cohort**, because `emr` *does* ship an HTTP server — `dashboard/server.py` (90 KB).
  But it is a bespoke dashboard API, not the `/query` + SSE + OpenAPI contract the field
  implies and the email agent implements (`hub/agents/email/python/openapi.email.json`,
  `CONTRACT.md`). Declaring `api_server: true` on the strength of a dashboard would let a
  v2 consumer expect a contract that is not there.
- **Packaging is broken: the dashboard cannot ship in the wheel.**
  `pyproject.toml:24-25` is `[tool.setuptools.packages.find] include = ["gaia_agent_emr*"]`
  with **no `package-data`, no `include-package-data`, and no `MANIFEST.in`** (verified
  absent). `dashboard/electron/` and `dashboard/frontend/` contain no `__init__.py`, so
  setuptools does not treat them as packages, and their 19 non-Python assets
  (`main.js`, `App.jsx`, `index.html`, `vite.config.js`, `amd.png`, the `package.json`
  pair…) are excluded. Worse, `server.py:2173` serves
  `Path(__file__).parent / "frontend" / "dist"` — a build output that **does not exist in
  the repo** and that no packaging step produces. A `pip install gaia-agent-emr` therefore
  yields the `no_frontend()` placeholder at `server.py:2187-2191`
  (*"frontend": "not built (run npm build in dashboard/frontend)"*) while the README
  advertises a "Web Dashboard - Real-time monitoring with live feed" (`README.md:26`).
- Entry point `emr = "gaia_agent_emr:build_registration"` (`pyproject.toml:19`) —
  **resolves**. `gaia-emr` console script (`:16`) also resolves.
- `pyproject.toml:13` is the only cohort member depending on an extra
  (`amd-gaia[api]>=0.20.0`) — correct, since the dashboard needs FastAPI.
- No `permissions:` block for an agent that reads a user directory, writes a database,
  and binds a network port.

---

## (b) Summary table

Effort scale matches the sibling audit (`port-audit-6-agents.md:611`):
**S = days, M = 1–2 weeks, L = multi-week.** Per agent, per workstream.
"—" means the workstream should not be done at all under the stated verdict.

| Agent | Verdict | Generalization | Tests | Eval | Packaging | The one-line reason |
|---|---|---|---|---|---|---|
| **docker** | **PORT** | **M** — re-scope from "Dockerfile generator" to real container management: add `ps`/`logs`/`stop`/`rm`, fix the three conflicting default ports (`agent.py:26, 164, 258`), replace substring framework detection with real manifest parsing, add a daemon (not binary) preflight, apply `path_validator` to `run_container` | **M** — 55 lines, 0/4 tools behavioral; but a fixture-tree analyzer suite is straightforward | **M** — no vehicle today, but a deterministic one is obvious: fixture app trees → exact-match analysis, then `docker build` exit-0 in CI | **M** — full parity kit; nothing structurally broken | Uncovered domain with no planned successor, smallest surface, cheapest generalization, zero compliance exposure |
| **blender** | **DISCARD** (keep as in-repo MCP-passthrough example) | — (would be **L**: 4 dead tools, 3 unreachable `core/` modules, no render/import/export/lighting, `basicConfig` at import, transport lives in the core wheel) | — (1,155 lines that test the base agent's JSON machinery, 0/5 tools behavioral) | — (**L**; needs headless Blender + scene-graph assertions in CI) | — | Nothing in the 23-agent program touches 3D, half the shipped agent is commented out, and its real value is as the template A3 `smarthome` will copy |
| **jira** | **DEFER** → then **MERGE INTO** the L8 TaskStore (#1521–#1525) as an external-tracker connector | **M** (deferred) — strip the author's instance from the prompt (`agent.py:189-193, 776, 781`), fix `jira_update(status=)` to use `/transitions`, remove the `projects[0]` write default (`:744`), move auth to `src/gaia/connectors/`, add pagination, support Jira Server `/rest/api/2/` | **S** — already the cohort's best (3/3 tools behavioral); needs 2 tests *deleted* because they pin bugs (`tests/test_jira_agent.py:333, 700`) | **S** (deferred) — NL→JQL exact-match over the existing fake-HTTP fixture; no live instance, no judge | **M** (deferred) | L8 is defining the cross-agent task model *now* and #1524's `source_ref` is exactly the Jira seam — shipping a standalone agent first creates a second, competing task model |
| **sd** | **PORT** the capability as a layer; **DISCARD** the current package shape | **M** — promote SD generation to a shared layer (nothing in the 22 generates images), delete `create_story_from_image`, un-hardcode `output_dir`/`base_url`/VLM model/Turbo params, stop persisting `ctx_size` globally (`agent.py:90`), and resolve the two-places problem the sibling audit flagged (`port-audit-6-agents.md:87-89`) | **M** — 396 lines, but the headline test asserts on its own mock (`tests/test_sd_agent.py:47-66`); success path never exercised | **L** — no honest deterministic vehicle for generated images; score the *prompt-enhancement* step instead and say so, per "stop rather than invent numbers" | **M** | Image generation is a capability gap across all 23 planned agents — but the `sd` wheel's own contribution is a storytelling tool its manifest never mentions |
| **emr** | **MERGE INTO** A12 `health` (#1496) + layer AH-L4.2 · **do not publish** | — (would be **L**: constructor does inference and creates directories, one clinic's 60-column fixed schema, first-PDF-page-only, fabricated ROI constants) | **S** — 1,812 lines, genuinely behavioral, mostly survives the harvest | **M** — best-positioned of the five (form → expected fields is exact-match), but the harness belongs to AH-L4.2, and the corpus must be synthetic | — (dashboard cannot ship: no `package-data`/`MANIFEST.in`, serves a nonexistent `frontend/dist`) | The spec already names EMR as A12's foundation, and a `healthcare` package claiming "HIPAA-friendly" while storing plaintext SSNs must not go to a public hub |

**Net:** 1 clean port (`docker`), 1 port-as-layer (`sd`), 1 deferred merge (`jira`),
2 not shipping (`blender`, `emr`).

---

## (c) Install-time dependency checks

No manifest field expresses an external-system dependency today.
`requirements:` accepts only `min_memory_gb`, `gpu_vram_gb` and `platforms`
(`sd/python/gaia-agent.yaml:23-26` is the richest instance in the cohort), and
`interfaces:` has **no consumer in `src/gaia/agents/registry.py` at all**.

**Recommended manifest addition** — a `requirements.external:` block, each entry with a
`check` the installer runs and a `remedy` string surfaced verbatim on failure. Per
CLAUDE.md, the failure must name *what failed*, *what to do*, and *where to look*.

### Per agent, what the check must assert

**`docker`**
1. `docker` binary on `PATH` — `shutil.which("docker") is not None`.
2. **The daemon is running and reachable** — `docker info` exits 0 (not
   `docker --version`, which is what `agent.py:401-407` checks today and which passes
   with Docker Desktop stopped).
3. The current user can reach the socket without sudo — `docker ps` exits 0 (catches the
   Linux `docker` group case, whose error is otherwise a permission-denied deep in a
   build).
4. API version floor — `docker version --format '{{.Server.APIVersion}}'` ≥ the minimum
   for BuildKit.
5. Non-blocking warning: available disk for the image cache.

**`blender`** (only if the DISCARD verdict is overturned)
1. `blender` executable resolvable, and its version ≥ the minimum the addon supports.
2. **The GAIA Blender addon is installed and enabled** — currently assumed, never
   verified anywhere in the package.
3. **The MCP server is listening and answers a handshake** — the check must be a real
   round-trip (e.g. `get_scene_info`), not a TCP connect, because a stale socket
   satisfies a connect and then hangs. Today `MCPClient()` is constructed at
   `agent.py:53` with no reachability check at all, and the first failure surfaces as a
   soft error dict from a tool.
4. Host/port must be **parameters**, asserted present — the agent currently offers no way
   to point at a non-default Blender.

**`jira`**
1. All three of `ATLASSIAN_SITE_URL`, `ATLASSIAN_API_KEY`, `ATLASSIAN_USER_EMAIL` present
   — `_get_jira_credentials` already raises correctly for this (`agent.py:402-405`); the
   check moves that to install time.
2. **Credentials actually authenticate** — `GET /rest/api/3/myself` returns 200. This is
   the check that would have prevented the `initialize()` empty-config swallow
   (`agent.py:287-296`): the difference between "no projects" and "bad token" must be
   established at install, not inferred at query time.
3. **Deployment type** — probe `/rest/api/3/serverInfo` and assert Cloud, or fail with
   the explicit message that Server/Data Center (`/rest/api/2/`) is unsupported.
4. At least one project is visible, and **a default project is configured** — so
   `jira_create` never falls back to `projects[0]` (`agent.py:744`).
5. Write scope confirmed (the token can create in the default project) — checked once, at
   install, rather than discovered by a failed user-visible write.

**`sd`**
1. **Actual free VRAM ≥ the model's requirement**, measured — not the static
   `gpu_vram_gb: 6` in `gaia-agent.yaml:25`, which nothing enforces. A machine with a
   6 GB card and 5 GB already resident passes the declared requirement and OOMs.
2. The SD backend/runtime is present and the selected model
   (`SDAgentConfig.sd_model`, `agent.py:28`) is downloadable/resolvable.
3. **Lemonade reachable and the LLM loadable at the required context** — this is the
   check that replaces the swallowed `except Exception` at `agent.py:95-96`. It must
   fail install, not warn.
4. Writable, **absolute** output directory — the current `.gaia/cache/sd/images`
   (`agent.py:28`) is CWD-relative and must be resolved and asserted writable.
5. Platform/backend compatibility asserted explicitly, since the manifest currently
   claims all three platforms (`:26`) with no backend qualification.

**`emr`** (checks stated for completeness; the verdict is do-not-publish)
1. VLM model resolvable and loadable — replaces the `return None` at `agent.py:353-355`.
2. `watch_dir` and `db_path` supplied as **absolute** paths and asserted writable;
   install must **refuse to create them implicitly**, unlike `agent.py:148`.
3. SQLite available with the required extensions; schema migration state verified rather
   than warned past (`agent.py:208-210`).
4. **PDF backend present** — `pdf_page_to_image` (`agent.py:1094`) has a native
   dependency that is assumed everywhere and checked nowhere.
5. **Dashboard assets built** — assert `dashboard/frontend/dist/index.html` exists, or
   fail; today its absence silently degrades to the `no_frontend()` placeholder
   (`dashboard/server.py:2187-2191`) while the README promises a dashboard.
6. **An explicit, recorded acknowledgement of the PHI storage model** before the database
   is created — plaintext SSN (`constants.py:137`) and raw form images (`:207`) at rest.

### Cross-cutting

Two checks belong in the framework, not per agent, and would cover the whole hub:

- **`gaia doctor <agent-id>`** — runs the manifest's `requirements.external` checks and
  prints pass/fail with the remedy string. Without a runner, a declarative block is just
  more inert metadata like `interfaces:`.
- **A `tools_count` drift guard**, as the email agent has (its manifest declares 55 and
  fails CI on drift). All 11 legacy agents across both audits declare 0. An AST pass over
  `@tool` decorators is sufficient — and, contrary to a first impression, it works for
  `blender` too: its tools are static `@tool` wrappers around `MCPClient` calls, not
  runtime-discovered from the remote server, so the commented-out decorators at
  `agent.py:256, 327, 380, 409` are exactly what such a pass would catch.
