# Scope: hybrid mode — simple turns on the local model, hard ones in the cloud

**Goal:** the user picks one mode, `hybrid`. GAIA answers on the local model by
default and moves a turn to the user's chosen cloud model — Fireworks AI or the AMD
LLM Gateway — only when the local model can't do it well and the user has agreed that
this conversation may go to that provider. Privacy controls apply to everyone, not
just enterprises:

- **Privacy mode** (on by default in hybrid) keeps anything sensitive local.
- A three-level **network policy**, from least to most private:
  1. **Cloud allowed**;
  2. **Local only**: no GAIA LLM request reaches a cloud model;
  3. **Offline**: GAIA makes no outbound network connection at all.
- **On the AMD network, nothing is ever sent to Fireworks.**

**What hybrid is for depends on the machine, and each case is measured separately:**

| Machine class | Local model | What hybrid buys | What it does not buy |
|---|---|---|---|
| Small PC | `Gemma-4-E4B-it-GGUF` | Cloud-level quality on the turns E4B fails, while everyday turns stay private and work offline | — |
| Strix Halo / 32 GB GPU | `Qwen3.6-35B-A3B-GGUF` | Most turns never reach a cloud model; cloud is a safety net | Meaningful savings against today's cloud defaults (~$0.10 per 14-task run, `tui/internal/lemonade/cloud.go`) |
| Either, with a premium cloud model | either | The above, **plus** real spend reduction | — |

So the headline metric is **less conversation content sent to cloud LLMs at equal
quality**. Cost is secondary, and it matters only with a premium cloud model.

**Recommendation:**

1. **Ship the network policy first** ("Local only", then "Offline") and the
   AMD-network rule, all enforced where requests are made, not in routing. They are
   useful on their own, and hybrid depends on them.
2. **Then build hybrid as a per-turn decision made *before* the turn runs,** by a pure
   policy in core. It decides in two fixed-order stages: whether the turn wants cloud,
   then which consented, available cloud model (primary, or the user's optional backup
   on the other provider) takes it (§2.3).
3. **At most one escalation per turn.** It continues the turn's existing message list
   on the cloud model and never replays a tool.
4. **v1 ships rules only;** a learned scorer waits for production evidence.
5. **The UI and TUI configure the mode, render the agent's per-turn route event, and
   show option state** computed from one specification. The Python backend computes
   it for the UI, and Go computes it for the TUI, pinned by shared golden fixtures.
   Neither client routes.

Builds on [`lemonade-routing-integration.md`](lemonade-routing-integration.md)
(tier at the skill boundary; Lemonade's router as a guardrail only) and
[`skill-bound-task-execution.md`](skill-bound-task-execution.md) (`requires.class`).

---

## 1. What exists today, and what does not

| Piece | Reality | Consequence |
|---|---|---|
| Cloud models | The Agent UI and TUI reach Fireworks (`fireworks.*`) and the AMD gateway (`amd.*`) **through Lemonade**, on the same client as local models. Availability is checked in `ui/routers/providers.py` (registered in `/system-info`, working key, models discovered, model listed) and mirrored in Go in `tui/internal/lemonade/cloud.go`. `ClaudeProvider` is reachable in the UI only through eval env vars | **v1's cloud tier is Lemonade-registered Fireworks/AMD models.** Claude-direct is out of v1. The availability check moves into `gaia.llm` so routing can use it |
| Model switch | `switch_model` / `_apply_switch` (`hub/agents/gaia/python/gaia_agent/stdio.py`) swap client, config, `model_id` and rebuild the prompt as one unit. The HTTP path reuses them (`session_registry.py`). `AgentSDK.update_config` doesn't rebuild the client. A switch costs `list_models` + `create_client` + catalog refresh + prompt rebuild | Move the rebind into `src/gaia/agents/base/` as the only rebind, with a fast path for a model-id change on the same Lemonade client. Its latency counts toward the p50 bar |
| Agent loop | The system prompt is re-read every step (`agent.py` steps loop), and orphan tool results are sent as text (`chat/sdk.py`) | A mid-turn rebind takes effect and a cloud request won't fail on local history. **Confirmed safe mechanically**; the remaining risk is quality |
| Prompt and location | The location appears **twice**, both in `_get_system_prompt`: the "You are GAIA… running locally" opener and the WHERE block (`gaia_agent_chat/agent.py`). The volatile-fragment mechanism skips `_get_system_prompt` by design. Both default local models are `tool_calling=True` | Local and cloud have **separate** caches, and each always sees its own location text. So a flip doesn't break the local prefix. The real cost is the cloud cache **expiring during local turns**. Measure it before changing the prompt (§2.6) |
| History | `_restore_model_history` trims history to the bound model's `history_budget` **before** the preflight. Cloud admission is capped at `GPU_CTX_SIZE` (64K), the same as local | Route on the **untrimmed** size, then rebind, then trim (§2.3). A context rule is meaningless unless the cloud model's real window is used |
| UI turn path | The agent exists (cached or built) before `_maybe_load_expected_model` takes a broker lease. The transient retry calls `process_query` again **from scratch**, replaying any tools that already ran. The agent cache is keyed by model id | Route before the preflight; key the cache on mode; use call-level retry once a tool has run (§2.4) |
| Daemon | Optional at runtime: the UI only *tries* to start it, `model_lease` is a no-op without it, and SDK users have none | **Nothing in hybrid may depend on the daemon.** The ledger is a local SQLite file (§2.5) |
| Internal LLM calls | `_make_extraction_chat` deep-copies the live chat config. The memory router and the RAG SDK/VLM build their own clients | Each call site needs an explicit tier, and **every** one must pass the kill-switch check |
| Prices | `eval/bench/metering.py::RATES` + `eval/config.py::MODEL_PRICING` (duplicate). `cache_pricing.py` holds ratios only. Runtime code can't import `eval/` | One rate card in `src/gaia/llm/pricing.py`; price **per LLM call** |
| Turn log | `TurnRecorder` runs only with `GAIA_TURN_LOG` and records one model per turn | It isn't a ledger |
| Config | `GaiaConfig` is flat: unknown keys are dropped, `save()` writes only declared fields, and dotted keys are rejected. A UI turn runs from SQLite `custom_model` | Flat fields, kept in sync with `custom_model` |
| Consent | Session `private` means incognito. Documents have no flag. `PrivacySettings.tsx` only displays a location | Add `cloud_allowed`; don't overload incognito |
| Skill tier | `requires.class` exists only in plans. Skills load mid-turn | A declared tier is usually seen mid-turn, so it acts as an escalation trigger |
| Events | The UI SSE has `status` / `answer` / `agent_created`. The stdio `status` event carries `model_backend` / `model_remote` | Add `route` to `status` in both transports, and document it in `docs/spec/agent-ui-query-sse-contract.md` |
| Eval harness | `gaia eval tasks` meters cost. `everyday` ∪ `core` ∪ `adversarial` = **23 unique single-prompt dev tasks**. The nightly `flagship-tasks` job runs one local model once, with no cloud arm | A chat suite, a multi-turn paired set, and a new workflow arm are all new work (§4) |

---

## 2. Design

### 2.1 Network policy: Cloud allowed › Local only › Offline

**One three-level setting, `network_policy`,** in Settings › Privacy, the TUI
`/provider` screen, and first-run setup. It is available to every user. Each level
includes everything the level before it blocks:

| Level | `network_policy` | What GAIA may send off the machine |
|---|---|---|
| Cloud allowed (default) | `cloud_allowed` | Cloud LLM requests per mode, consent and privacy mode, plus non-LLM features (web, connectors, hub, …) |
| **Local only** | `local_ai_only` | **No LLM request to any cloud model.** Non-LLM features still work |
| **Offline** | `offline` | **No outbound network connection from GAIA at all** (§2.1.7) |

Sections 2.1.1–2.1.6 specify **Local only**. Offline (§2.1.7) builds on it. The
AMD-network rule (§2.1.8) is separate and applies at every level. While the policy is
**Local only** or stricter:

- no LLM request from GAIA reaches a cloud model;
- `hybrid` and `cloud` can't be selected;
- every setting and option that would lead to the cloud is shown as unavailable
  (§3.2, §3.3).

It is the top of the precedence order (§2.3).

**Enforcement is at the request layer, not in routing,** so paths that never go
through routing are covered too.

#### 2.1.1 Fail closed: an allowlist, not a blocklist

The current classifier `cloud_model_provider()` recognises only `fireworks.` / `amd.`
ids, or catalog metadata when it's passed. Anything else returns "not cloud": Claude
ids, a provider such as `vercel.*`, or a Lemonade router alias with no dot. A blocklist
built on it would leak, so the switch uses an **allowlist** instead.

- **`gaia.llm.local_only.assert_permitted(model_id)`.** While the switch is on, a
  model is permitted only if Lemonade's cached `/v1/models` lists it as **local**: a
  non-cloud, non-router recipe.
  - Unknown ids, unlisted ids, and an unreadable catalog are all refused with
    `CloudDisabledError`. The error names the model, says "Local only is on", and says
    where to turn it off.
  - The catalog cache refreshes on a miss, so a newly pulled local model works on its
    first request.
- **`ClaudeProvider` is blocked by provider,** at `create_client`, whatever the model
  id.

#### 2.1.2 Every outbound LLM path, named

| Path | Today | Change |
|---|---|---|
| `LemonadeClient` | **No single request funnel.** `chat_completions`, `completions` and `responses` each make their own `requests.post`, and the streaming paths build their own `OpenAI(...)` client (`_stream_chat_chunks`, `_stream_completions_with_openai`) | `@local_only_guard` decorates an **explicit list of inference methods**: `chat_completions`, `completions`, `responses`, `embeddings`, the VLM/ASR inference calls, and the streaming helpers they use. **Model management is exempt by name** (`pull_model`, `pull_model_stream`, `load_model`, `unload_model`, `delete_model`), so downloading a local model still works while Local only is on. A test fails if a method that issues an inference request (it touches an inference endpoint or builds an `OpenAI(` client) isn't on the guarded list, so a new one can't skip the check |
| `create_client("claude")` | Builds `ClaudeProvider` | Refused while on |
| Session auto-titling (`ui/_chat_helpers.py`) | Raw `POST /chat/completions` with the session's model | Goes through `LemonadeClient`, **pinned local in every mode**. A title never needs the cloud, and today it would leak a local turn's text in hybrid |
| Eval judge and scenario runner | `anthropic.Anthropic` (`eval/claude.py`), a `claude -p` subprocess (`eval/judge_client.py`, `eval/runner.py`), an `OpenAI` client (`eval/tool_cost.py`) | Each calls `assert_permitted` before its request or subprocess |
| Frozen sidecars (email) | Bundle their own `gaia` and expose an auth-exempt `GET /version` (`hub/agents/email/python/CONTRACT.md`) | Sidecars built against a `gaia` with the guards add `"policy_enforced": ["local_only", "offline", "amd_network"]` to their `/version` body. They read the same `~/.gaia/policy.json`. While the switch is on, the launcher (daemon or UI) probes `/version` **before routing any request** to a sidecar. If an entry the current policy needs is missing, it stops the sidecar and reports "*<agent>* can't enforce Local only — update it with `gaia agent install <id>`". Turning the switch on also re-probes running sidecars |
| Go TUI | Makes no LLM inference calls, but posts keys to Lemonade (`lemonade/cloud.go` `Configure`) | Its key paths check the switch (§2.1.3) |

**Census test:** a static scan fails CI when any of the following appears outside the
allowlisted modules (the guarded `LemonadeClient` methods, `ClaudeProvider`, and the guarded eval transports):

- `/chat/completions`, `/completions`, `/responses` or `/embeddings` URL literals used
  in an **outbound** request;
- `anthropic.`, `openai.OpenAI(`, or `AsyncOpenAI(`;
- a subprocess argv starting with `claude`;
- a `cloud/auth` POST outside the gated key functions (§2.1.3), in both Python and Go.

GAIA's **inbound** OpenAI-compatible servers declare those same paths as routes
(`api/openai_server.py`, `api/app.py`, `agents/base/server.py`). The exemption is
**by pattern, not by file**: route-decorator lines (`@app.post("/v1/chat/completions")`
and the like) are skipped, and any outbound call in those same files is still scanned.
A new bypass therefore can't land silently.

#### 2.1.3 Keys

- While the switch is on, GAIA doesn't send stored keys to Lemonade, and it clears the
  runtime keys it set.
- **Every place that posts `cloud/auth` today is gated:**
  - **Python:**
    - `cloud_keys.ensure_authenticated` / the re-send in `cloud_keys.py`. This is
      reached by `GET /api/providers` (on every list), by the daemon's
      `/daemon/v1/providers/<p>/authenticate`, and by the model-readiness step in
      `agents/base/readiness.py` that every front-end goes through;
    - `gateway.py`'s auth call, reached by `/daemon/v1/gateway/authenticate` and by
      `gaia gateway`;
    - `ui/routers/providers.py`'s `connect_provider`, which posts directly.
  - **Go:** the TUI checks `policy.json` itself before posting, in `Configure`
    (`tui/internal/lemonade/cloud.go`) and in `SetToken` (`cloud/auth`) and `Install` (provider registration; gated too, so no gateway is registered while Local only is on)
    (`tui/internal/ui/gateway/client.go`). `ui/providers/persist.go` and
    `ui/gateway/persist.go` only call the daemon, which is already gated.
- The census (§2.1.2) fails on any new `cloud/auth` post outside these sites.
- A key in **Lemonade's own environment** (`env_var_set` in `/system-info`) is beyond
  GAIA's control. Settings warns that a direct request to Lemonade could still reach
  the cloud. GAIA's own requests are still blocked by §2.1.1.

#### 2.1.4 Storage: its own file, written atomically

- **The user policy lives in `~/.gaia/policy.json`, not `config.json`:**
  `network_policy`, plus the privacy-mode and stats settings (§2.8, §4.5).
  - `GaiaConfig.save()` rewrites only the fields it declares, so an older GAIA would
    **erase** a policy stored in `config.json`. Older versions never touch
    `policy.json`.
- **A managed policy file** (written by an admin, read-only to the user) sets a
  **floor** and the AMD-network markers (§2.1.8):
  - Windows: `%ProgramData%\GAIA\policy.json`
  - Linux: `/etc/gaia/policy.json`
  - macOS: `/Library/Application Support/GAIA/policy.json`

  The effective level is the **stricter** of the user and managed settings, so a user
  can tighten a floor but never loosen it. The UI shows "Set by your organization" on
  anything the floor locks.
- **Writers:** the UI, the TUI, and `gaia config set network_policy
  cloud_allowed|local_ai_only|offline` (the `config` command routes `policy.json` keys
  there).
  - Each writes atomically (temp file + rename).
  - Readers re-read on mtime change.
- **Fails closed:**
  - an unreadable or unparseable user `policy.json` counts as **Local only**;
  - an unreadable managed file counts as **Offline**, because it exists only on
    machines whose admin chose to restrict them.

  Either way, the error names the file.
- **Stated limit:** downgrading to a GAIA that predates the switch means that version
  doesn't enforce it. The release notes say so.

#### 2.1.5 Turning it on: every stored cloud id, not just the current one

A cloud id can be stored in several places:

- `default_model`, which `gaia gateway use` and the TUI gateway screen
  (`tui/internal/ui/gateway/gaiaconfig.go`) set, and which
  `resolve_default_chat_model()` hands to `gaia chat`, schedules and sidecars;
- `last_model` / `last_provider`, which the UI's `restore_last_model` and the TUI's
  `preflight/lastmodel.go` restore;
- the UI's `custom_model`, including the free-text field in Settings;
- each session row's `model`.

Turning the switch on:

1. lists the stored cloud ids it found;
2. asks **once**: "Switch these to *<local model>*? Hybrid/Cloud mode will be set to
   Local.";
3. in the same operation, rewrites them all and sets `routing_mode=local`.

Further rules:

- **Kept but inert:** `routing_cloud_model`, `routing_cloud_backup_model` and the
  gateway's own `gateway.json`. They aren't rewritten and aren't used while the switch
  is on. Turning it off later restores nothing automatically, but no setup is lost.
- **Local only always wins at load.** If the stored state still says `hybrid`/`cloud`
  while the switch is on — a crash between the two file writes, a `policy.json` read as
  on because it's unreadable, or a hand edit — the **effective** mode is `local`. A
  visible notice says why, and nothing fails. This is precedence, not a fallback: the
  switch is the user's explicit, higher-ranked choice.
- If no local chat model is downloaded, the switch can't be turned on yet, and the
  message points to the download.
- **Defense against stale or hand-edited state:** if a restore at startup finds a cloud
  id while the switch is on, it returns `local_only` and opens the model picker. It
  never fails the first turn.
- Every write path that would store a cloud id **refuses** while the switch is on:
  `select_model`, `connect_provider`, the Settings custom-model field, `gaia gateway
  use`, `gaia config set default_model|routing_*`, the TUI gateway screen, and the
  TUI's `/model` and `/provider`.
- **Every `config.json` writer becomes atomic** (temp file + rename):
  `GaiaConfig.save()`, `gateway/gaiaconfig.go`, `preflight/fullaccess.go`,
  `preflight/lastmodel.go`.

#### 2.1.6 Turning it off

The options come back, but **no mode or model changes.** Hybrid or cloud has to be
chosen again explicitly, and stored keys are re-sent to Lemonade only then.

**Scope (in the help text):** every LLM call GAIA makes, including the eval judge,
which is refused with the same error. It does **not** cover non-LLM network tools (web
search, connectors, MCP servers that call external services), which keep their own
permissions. That's why the label says "cloud AI models", not "nothing leaves your PC".

**Default:** Cloud allowed. First-run setup asks.

#### 2.1.7 Offline: zero network

**Promise:** while the policy is Offline, GAIA makes **no outbound connection to any
non-loopback host**. It includes everything Local only does. Lemonade and the daemon
stay reachable on loopback.

**Enforced in three layers:**

1. **Capabilities are declared, so the agent never sees network tools.**
   - `@tool` gains a `network=True` tag. Tagged tools aren't registered, and the
     `_policy_refusal` dispatch choke point refuses any that slip through (for example
     MCP or skill tools). That covers:
     - web search, page fetch and download;
     - the Playwright browser;
     - email and connector tools;
     - skill-hub search and install;
     - `install_cli` / `sign_in_cli`.
   - Skills that declare a `network:*` or `mcp:connect` permission
     (`skills/tiers.py`) are **unavailable**. This finally enforces a permission that is
     declared today but not enforced.
   - Shell commands that `agents/base/denied_effects.py` classifies as network (curl,
     git fetch, `gh`, pip, …) are refused.
2. **Background paths check one `is_offline()`.** None of these go through tool
   dispatch, so each refuses with an actionable error:
   - hub catalog, install and publish;
   - model pulls (`pull_model*`, `load_model(auto_download=False)`), diarization
     downloads and the Lemonade installer;
   - OAuth token exchange and refresh, Slack and Telegram adapters, schedule sinks;
   - the ngrok tunnel;
   - the Electron auto-updater, which isn't initialized at all;
   - remote MCP (HTTP transport).

   `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set for GAIA and its children.
   External doc links show the URL to copy instead of opening a browser.
3. **A loopback-only guard on every GAIA process**, so a path nobody listed is still
   caught:
   - **Python:** `sys.addaudithook`, installed when `gaia` is imported, so
     `multiprocessing` spawn children get it too. Audit hooks can't be removed once
     installed.
     - It refuses non-loopback targets on `socket.connect`, `socket.getaddrinfo`,
       `socket.sendto`, `socket.sendmsg` and `socket.bind`.
     - **Covered:** everything built on the `socket` module (requests, httpx, aiohttp,
       asyncio, ssl).
     - **Not covered:** C-extension sockets (grpc, libcurl bindings), raw libc through
       `ctypes`, and subprocesses. A dependency census lists any C-extension network
       library GAIA ships; each is either turned off in Offline or runs only under OS
       confinement.
   - **Go TUI:** one `http.Transport` whose `DialContext` allows only loopback, used
     everywhere. A census check fails CI if `http.DefaultClient` is used.
   - **Electron:**
     - `session.webRequest` cancels non-loopback requests from the renderer and
       Electron's `net` module.
     - Node's `http`/`https`/`net` in the main process aren't covered by that, so a
       census rule allows them only with loopback targets (`main.cjs` already uses
       `require("http")` for loopback).
   - **Separate GAIA processes** (daemon, sidecars) import `gaia`, so they get the same
     hook. A frozen sidecar must prove it in `/version` (§2.1.2), or it isn't
     started.

**Child processes are the honest limit.** The shell tool, `run_python`, and stdio MCP
servers spawn processes that the in-process guard can't reach. So in Offline:

- **Without OS-level confinement** (default), these features are **disabled**, and
  the UI says why.
- **With OS-level confinement** (per platform, unprivileged), they're allowed,
  because each child is started **without network access**. The mechanisms:
  - **Linux:** the child runs in a new network namespace (`bwrap --unshare-net`, or
    `unshare -rn`).
    - Before the child runs, GAIA checks that its namespace contains only `lo`.
    - Ubuntu 24.04+ restricts unprivileged user namespaces through AppArmor. Where the
      namespace can't be created, the features stay disabled; they never run
      unconfined.
    - systemd `IPAddressDeny=` is **not** used: unprivileged `--user` managers can't
      attach its eBPF program, and systemd then starts the unit **without**
      firewalling, which would be a silent no-op.
  - **Windows:** the child runs in an **AppContainer** without the `internetClient`
    capabilities. This is unprivileged and needs no firewall rule, so it doesn't break
    the user's own Python. These children don't need Lemonade, so no loopback exemption
    is needed either (open question 9).
  - **macOS:** no supported unprivileged mechanism, so these features stay disabled in
    Offline.
- **Badges are honest:** the UI shows "Offline — children confined by the OS" only
  after the per-child check passes. Otherwise it shows "Offline — shell and MCP turned
  off".
- **Lemonade (`lemond`) itself isn't confined in v1;** that needs root. Its own
  background egress is open question 8, and the help text says so.
- **Verification makes no outbound probe.** The indicator checks that the guards are
  installed and the confinement checks pass, so a strict user never sees a test
  packet leave.

**Switching to Offline requires a downloaded local chat model,** because pulls are
blocked. The UI checks this first.

#### 2.1.8 AMD network: never Fireworks

**Rule:** while the machine is on the AMD network (office or VPN), **no request goes to
Fireworks**, whatever the mode, the selection, or a configured backup. It applies at
every network-policy level.

- **Detection.** The gateway hostname can't be the signal:
  `DEFAULT_GATEWAY_BASE_URL` (`gateway.py`) is set on **every** install, and its host
  resolves on public DNS. Instead:
  - **Markers are internal-only hostnames** (split-horizon DNS: they resolve only
    inside AMD). They come only from:
    - the **managed policy file** (`network_markers`), installed by IT on AMD-managed
      devices;
    - the user's own `policy.json`, for AMD developers not under device management.
      The AMD internal setup guide gives them the value.
  - **No marker hostname is ever committed to the public repo.**
- **What the check returns, per marker:**
  - resolves → **on** the AMD network;
  - a definitive NXDOMAIN → **off**;
  - anything else (timeout, resolver error) → **on**. That is fail-closed: failing to
    tell can only cost a Fireworks turn, never leak one.
  - Off the VPN with working DNS, the answer is a clean NXDOMAIN, so "AMD gateway, else
    Fireworks" works as intended. With no network at all, Fireworks is unreachable
    anyway.
- **Stricter options** (managed file or user `policy.json`):
  - `treat_as_corporate: always`: this machine never uses Fireworks, on or off the VPN;
  - `deny_providers: ["fireworks"]`: the plain user-level version.
- **No marker configured** (every external user): the rule is inactive.
- **Enforced as an allowlist, like Local only:** while the rule applies, the request
  guard (§2.1.1) allows only catalog models whose recipe is local or whose cloud
  provider is `amd`.
  - Fireworks is blocked, and so are a dotless router alias and any other cloud
    provider (for example `vercel.*`) that could proxy to it.
- **Checked per request, not cached:** the markers are re-checked before **every**
  cloud request (OS-cached DNS, a few ms), so connecting to the VPN mid-turn takes
  effect on the very next call. Fireworks keys aren't sent to Lemonade while the rule
  applies.
- **Everywhere it shows** (reason code `blocked_on_corporate_network`):
  - Fireworks options show "Not allowed on the AMD network";
  - stage B skips a Fireworks candidate (§2.3), so "gateway, else Fireworks" never
    falls through to Fireworks on the AMD network; the turn stays local;
  - in cloud mode with a Fireworks primary, stage B uses an AMD-gateway backup if one
    is configured. Otherwise the turn fails with
    `cloud_blocked_on_corporate_network` and a message to switch to the AMD gateway.

### 2.2 The cloud tier: Fireworks or the AMD LLM Gateway

- **Selected, not inferred.** The cloud half is the model the user chose,
  `routing_cloud_model`: a `fireworks.*` or `amd.*` id, where the prefix *is* the
  provider.
  - Optionally there is a **backup**, `routing_cloud_backup_model`, which must be on the
    *other* provider. A typical AMD setup: "AMD gateway when it's reachable, otherwise
    Fireworks".
  - The order is the user's explicit choice, and enabling a backup asks for consent for
    **both** providers up front.
- **Availability is shared.** It moves into `src/gaia/llm/cloud_availability.py`
  (registered, working key, models discovered, model listed). Routing, the UI router
  and stdio call it. The Go copy in `tui/internal/lemonade/cloud.go` is pinned by a
  parity test.
  - Results are cached with a 60 s TTL and invalidated on any cloud error, so the pure
    decision never waits on the network.
- **The AMD gateway is offered only when available.** It needs a base URL, a key, and
  a reachable endpoint, usually the AMD network or VPN. Otherwise it is shown as
  unavailable, with the reason.
- **Default on first enabling hybrid:**
  - the provider of the current cloud `last_model`, if there is one;
  - else the AMD gateway, if available, because traffic stays inside AMD's gateway;
  - else Fireworks, if it has a key;
  - else hybrid can't be enabled yet, and the UI links to the key setup.

  The user can change it at any time.
- **No unconsented provider switch.** If the primary becomes unavailable (VPN dropped,
  key revoked, model delisted):
  - **with a backup** that is available *and* covered by the session's grant, the turn
    uses the backup. The route note **names the provider every time**, e.g. "Sent to
    Fireworks — AMD gateway unreachable (off VPN?)". The user chose this order in
    advance, so it isn't silent.
  - **without one**, or when the backup isn't granted for this session, the turn runs
    **locally** and the note names the cause, with a one-click switch.

  GAIA never reaches a provider the user didn't select and consent to.
- **Consent is per provider.** A session or document grant names the providers it
  covers (`amd`, `fireworks`, or both). A grant for one is not a grant for the other,
  because the privacy notices differ.
- **Determinism:** which provider is used is a pure function of the availability
  snapshot, which `RouteDecision` records (§2.3).
- **Capabilities per provider:**
  - for both providers, tool calling comes from Lemonade's catalog labels and the
    context window from the catalog's `context_length`. (`~/.gaia/gateway.json`
    holds only the base URL, enabled/active models and non-streaming models, not
    capabilities.);
  - an unknown capability is treated as not supported, and admission says so;
  - the cloud model's **real** window replaces the `GPU_CTX_SIZE` cap for cloud
    admission. Otherwise "too long for local" has nowhere bigger to go.
- **Prices per provider.** Fireworks is priced. An unpriced AMD gateway reads `n/a`
  (never `$0`). A **dollar** cap can't apply to an unpriced provider. So the gateway is
  bounded by a **token** cap instead, `routing_cloud_cap_tokens_month` (§2.5).

### 2.3 Precedence: two stages, each a fixed order

`route_turn` answers two separate questions, so every decision carries two reasons:

- the **tier** (`tier`, `reason_code`): does this turn want local or cloud?
- the **provider** (`provider`, `provider_reason`): which allowed cloud model takes it?

**Mode first:**

- `local` → always local.
- `cloud` → stage A is skipped (the tier is cloud).
- `hybrid` → both stages.
- Network policy **Local only** → always local, `reason_code=local_only`. **Offline**
  → always local, `reason_code=offline`. In both, the mode is local by construction
  (§2.1.5).

**Stage A, tier (hybrid only).** The first rule that decides wins:

| # | Rule | Tier, `reason_code` |
|---|---|---|
| A1 | Capability: local can't serve the turn (tools needed and unsupported, images with no vision, **untrimmed** history + attachments over the local window but within the cloud window) | cloud, `capability_tools` / `capability_vision` / `capability_context` |
| A2 | Declared tier from a skill matched **before** the turn | local `skill_small_fast` / cloud `skill_reasoning` |
| A3 | Hysteresis: one of the 2 turns after an escalation | cloud, `hysteresis` |
| A4 | v1 rules (§4.4) | `rule_local` / `rule_cloud` |
| A5 | Default | local, `default_local` |

**Stage B, provider (only when the tier is cloud).** Try the candidates in the user's
order: `routing_cloud_model`, then `routing_cloud_backup_model` if one is set. Take
the **first candidate that passes all the gates**, checked against **that
candidate's own provider**.

- **Turn-level gate P, privacy mode (§2.8), hybrid only.** If the content that would
  be sent is sensitive, the turn stays local (`sensitive_source`, `sensitive_content`
  or `sensitive_check_failed`). Cloud mode skips this gate.

Then, per candidate:

0. **N, AMD network (§2.1.8).** While the rule applies, a candidate that isn't an
   `amd.*` model is skipped (`blocked_on_corporate_network`).

1. **Granted.** The session (and every referenced document) has a grant for this
   provider.
   - If consent is `ask` and the caller is interactive, the user is prompted for this
     provider.
   - A non-interactive caller (SDK without a consent callback, schedule, sidecar,
     voice) counts as **not granted**.
2. **Available,** per the availability snapshot (§2.2).
3. **Within the cap.** Headroom is at least the estimated size of a cloud turn
   (§2.5): dollars for a priced provider, tokens for an unpriced one.

The outcome:

- The primary passes → that provider, `provider_reason=primary`.
- The backup passes → that provider, `provider_reason=backup`. The route note names
  the provider and why the primary was skipped.
- **No candidate passes:**
  - **hybrid:** the turn runs **local**. `reason_code` is the gate that failed for the
    primary: a privacy code, `blocked_on_corporate_network`, `no_grant`,
    `consent_declined`, `non_interactive`, `primary_unavailable` or `cap_headroom`.
    The caveat says the turn wanted cloud.
  - **cloud mode:** the turn **fails** with `cloud_no_grant`, `cloud_unavailable`,
    `cloud_cap` or `cloud_blocked_on_corporate_network`, and an actionable message. The user chose cloud explicitly, so running
    locally would be a silent change. When the cap is the cause and the caller is
    interactive, the user is asked whether to approve an overage for this turn (§2.5).

**In cloud mode, choosing the mode is the grant** for the selected primary and backup.
There is no per-session prompt. Gate 1 still checks **documents**: a document marked
not shareable for that provider fails it, which is what `cloud_no_grant` reports in
cloud mode.

- **Order of operations per turn:** route on the untrimmed history → rebind → trim to
  the routed model's `history_budget` → preflight (lease and context check on the
  routed model) → run.
- **Determinism:** `route_turn` is pure over an explicit input snapshot: query
  features, session row (grants, hysteresis), availability snapshot, cap headroom
  value, mode, Local only state, and config. Consent prompts are outside it: an
  answer becomes a grant, then the decision is recomputed. The `RouteDecision`
  records that snapshot, so any decision can be replayed and explained.
- **Decision cost:** < 5 ms for the pure step. Input gathering (a cached
  availability read, a local SQLite read) and the rebind are measured separately and
  count toward the latency bar.

### 2.4 Escalation

- **Mechanism:**
  1. Run **stage B (§2.3) at the moment of escalation**, including the privacy and
     AMD-network gates, to pick the provider: primary, then backup.
  2. Rebind (§2.6).
  3. **Continue the same message list** on that cloud model. Tools that already ran
     keep their results. **Nothing is replayed.**

  In privacy mode, a turn that has already read user data is tainted (§2.8), so it
  doesn't escalate.
  - If no candidate passes, there is no escalation. The turn continues locally, and the
    route note names the gate that failed (or `consent_declined`). This isn't a
    de-escalation, because the turn never left local.
- **Triggers in v1, all pre-answer:**
  - an unparseable tool call twice;
  - the same tool failing twice with the same error;
  - the loop detector firing;
  - local context overflow;
  - the model loading a `reasoning` skill mid-turn.

  Answer-quality triggers are excluded, because they fire after text has already
  streamed to the user.
- **Each cloud call is re-checked, in two layers:**
  - **Local only** is checked by `@local_only_guard` on the `LemonadeClient` inference
    methods (§2.1.2), which needs no session context.
  - **The stage-B gates** (privacy taint that appeared mid-turn, the AMD-network
    rule, a grant revoked mid-turn, the turn's provider lost, the per-call cap
    reservation) are checked by a new agent-loop guard,
    `Agent._pre_llm_call_guard()`. It runs before every LLM call in the steps loop,
    where the session and ledger are in scope.
  - If a check fails mid-turn, the turn ends with what it has, plus a caveat:
    `stop_sensitive`, `stop_corporate_network`, `stop_grant_revoked`,
    `stop_provider_lost` or `stop_cap`. Under Local only or Offline it is
    `stop_local_only` or `stop_offline`.
    - It never silently continues locally, because that would be an unannounced
      de-escalation.
    - **The backup is chosen only at turn start**, never mid-turn, so a turn talks to
      exactly one cloud provider.
  - When the cap is the cause and the caller is interactive, the user is asked whether
    to approve an overage for this turn (§2.5).
- **Consent before a pre-routed cloud turn:** this is stage B's gate 1. The prompt
  appears before the first LLM call. If the user declines, the turn runs locally with
  `consent_declined`.
- **Retry:** once any tool has run in a turn, a retryable LLM error retries **that
  call** inside the loop, on the same tier, never the whole turn. This also fixes
  today's replay of tools on whole-turn retry.
- **Bounds:** at most 1 escalation per turn, no de-escalation within a turn, and the
  existing per-call retry budget. The bound is a test.
- **Consent at escalation time:** for interactive callers with `ask` consent, a prompt
  appears (§3). If the user declines, the turn finishes locally with a caveat; it is
  never blocked.

### 2.5 Cost ledger and spend cap

- **Storage:** `~/.gaia/ledger.db`, SQLite in WAL mode, written directly by every
  process (UI server, TUI's agent subprocess, SDK, schedules). No daemon dependency.
- **The budget is per user:** `routing_cloud_cap_usd_month` belongs to the OS user,
  and so does the ledger, because both live in that user's home. Two people on one PC
  have separate caps. Team or org budgets are out of scope.
- **Unpriced providers (the AMD gateway) get a token budget,**
  `routing_cloud_cap_tokens_month`. It is enforced by the same per-call reservation,
  in tokens instead of dollars. The managed policy file can set it as a floor, so
  gateway use is bounded too.
- **One row per LLM call:** `(session, turn, step, tier, provider, model, input,
  output, thinking, cache-read, cache-write tokens, $ or NULL when unpriced)`.
  `TurnRecorder` links to it by `turn_id`.
- **Cap enforcement, in two steps:**
  - **Routing (stage-B gate 3, read-only):** `route_turn` gets this month's headroom as an
    input value and compares it with the **estimated** size of a cloud turn: p95
    dollars per cloud turn for a priced provider, p95 tokens for an unpriced one. Both
    come from the ledger, seeded from the Phase 1 grid until there is history. `route_turn` stays pure; it never writes.
  - **Per call (hard):** before **each** cloud call, `_pre_llm_call_guard` reserves
    that call's worst case inside a `BEGIN IMMEDIATE` transaction:

    - priced: known input tokens × input price + `max_tokens` × output price;
    - unpriced: known input tokens + `max_tokens`, against the token cap.

    Concurrent sessions can't jointly overshoot, and no call pushes spend past the
    cap **unless** an interactive user approved an overage for that turn. An approved
    overage is written to the ledger as an `override` row, so it shows up in spend
    reports. The actual cost replaces the reservation when the call returns.
  - **`max_tokens`:** GAIA always sends an explicit `max_tokens` on cloud calls (the
    agent's existing `_max_output_tokens`), so the worst case is always defined.
  - **Unpriced providers** (cost `n/a`) use the same headroom gate and per-call
    reservation, counted in **tokens** against `routing_cloud_cap_tokens_month`
    instead of dollars. Their dollar column stays NULL. A priced backup's calls count
    against the dollar cap only.
  - **Settings warning:** when the cap is below one estimated cloud turn, Settings
    warns at the moment the cap is set.
- **Failure:** a locked or corrupt ledger raises an actionable error naming the file.
  It is never a silent "no cap". There is no "unreachable" state.

### 2.6 Core changes

- **One rebind in `base/`:** `_apply_switch` / `switch_model` move from the hub's
  `stdio.py` into the base agent.
  - It also re-resolves context eviction and the tool-schema mode.
  - A fast path handles a model-id change on the same Lemonade client: no new client
    and no full `list_models`.
  - stdio, the session registry and routing all call it.
- **`route_turn`** is a public method that callers invoke before their preflight:
  - the UI before `_maybe_load_expected_model`;
  - stdio before `process_query` in the worker;
  - `process_query` itself only when no caller has routed.
- **`Agent.current_route`** is the read-only decision for the turn in progress. The
  location text, the stdio `status` event and the UI SSE all read it.
  `inference_location.describe()` reports **this turn**: "ran on your PC" or "was sent
  to the AMD gateway / Fireworks".
- **Location text and the prompt cache are measured, not assumed.** Phase 1 records
  the cache-miss cost of local↔cloud alternation, mainly cloud cache expiry during
  local runs. If it matters, both location sites (the opener and the WHERE block) move
  into a new volatile fragment. That is an LLM-affecting change gated by an agent eval.
- **Internal LLM call sites are pinned local:** document extraction, memory
  extraction, embeddings, the RAG SDK and VLM.
  - **Memory extraction and embeddings are local always**, in every mode. They read
    stored memory, which §2.8 never sends to the cloud.
  - Document extraction, RAG answering and VLM may follow the turn's tier only when
    **privacy mode is off**, the turn is cloud, and every document involved is
    `cloud_allowed` for that provider. With privacy mode on, they stay local.
  - Session auto-titling is pinned local in every mode (§2.1.2).
  - The §2.1.2 census test also enforces the pinning: any new outbound LLM path
    outside the allowlisted modules fails CI.
- **Out of scope for hybrid routing in v1** (Local only still applies to all of them):
  - the email agent keeps `resolve_default_email_model()`;
  - schedules and sidecars run on `resolve_default_chat_model()` as they do today. If
    the user set a cloud `default_model` (e.g. with `gaia gateway use`), that is an
    explicit cloud choice and isn't routed;
  - `gaia talk` follows the mode, with consent treated as non-interactive.
- **UI agent cache keyed on mode:** `_get_cached_agent` keys on `routing_mode` and
  the local model, not on the routed model. A tier flip then reuses the agent (the
  rebind swaps the model) instead of evicting it and dropping MCP connections.
- **Local model is global:** in hybrid, `routing_local_model` applies to all sessions,
  so concurrent UI sessions don't thrash Lemonade's single slot with different local
  models.

### 2.7 Config and consent fields

- **`~/.gaia/policy.json`:** `network_policy`
  (`cloud_allowed|local_ai_only|offline`), `privacy_mode` (bool, default true),
  `share_routing_stats` (bool, default false). Written atomically, failing closed
  (§2.1.4). The managed policy file can set a floor and `network_markers`.
- **`GaiaConfig` (flat):** `routing_mode` (`local|cloud|hybrid`),
  `routing_local_model`, `routing_cloud_model`, `routing_cloud_backup_model`
  (optional, must be on the other provider), `routing_cloud_consent`
  (`ask|session|always`), `routing_cloud_cap_usd_month` (priced providers),
  `routing_cloud_cap_tokens_month` (unpriced providers, e.g. the AMD gateway; the
  managed policy file can set it as a floor).
  - Validated at load together with `policy.json`.
    - An unknown provider prefix, or a backup on the same provider as the primary,
      fails loudly.
    - Local only on with mode `hybrid` is **not** an error: the effective mode is
      `local`, with a notice (§2.1.5).
  - `GaiaConfig.save()` and every Go `config.json` writer switch to atomic temp +
    rename (§2.1.5 lists them).
  - An older GAIA drops these keys **and erases them** on its next save. A downgrade
    loses the hybrid settings; it doesn't lose the Local only switch, which lives in
    `policy.json`.
- **DB:** `cloud_allowed` on sessions and documents holds the **set** of providers
  granted (`{}`, `{amd}`, `{fireworks}`, or both), plus the session's hysteresis
  counter.
- **First enabling hybrid** shows what goes to the provider. It depends on privacy
  mode:
  - **Privacy mode on** (the default): the conversation only. No files, documents,
    email, memory, or tool output that reads your data. Sensitive messages are kept
    local.
  - **Privacy mode off:** the conversation, tool outputs read so far, and excerpts of
    documents marked shareable.

  Either way, memory, memory extraction, embeddings and documents that aren't
  shareable never go. Web and MCP tools keep their own egress, and the disclosure says
  so.
- **Shareable documents matter only with privacy mode off.** With it on, user-data
  tools aren't offered to cloud turns (§2.8), so no document reaches the cloud.

### 2.8 Privacy mode: sensitive content stays local

**Promise:** in hybrid with privacy mode on, **content GAIA classifies as sensitive is
never sent to a cloud model**, and the user can see why a turn stayed local. It is
**on by default** whenever hybrid is enabled. Cloud mode doesn't use it: the user chose
cloud for everything, and the disclosure says so.

**What counts as sensitive. Two independent sources; either one keeps the turn
local:**

1. **Taint by source (deterministic, default-tainted).**
   - `@tool` gains a required-in-privacy-mode `reads_user_data` tag. `True` on: file
     read/search/filesystem, documents and RAG, email and connectors, memory recall,
     scratchpad, screenshots, shell and Python output, and the clipboard.
   - **No tag means tainted.** That covers MCP tools, skill tools and any tool without
     an explicit `reads_user_data=False`. Only an explicit `False` clears it: web
     search/fetch of **public** hosts, time, math, and similar.
   - A fetch of a private-range or intranet host is tainted.
   - Every message carrying a tainted tool's output, every user-attached file, and
     **every history message from before taint metadata existed** is tainted.
   - A document the user marked shareable (`cloud_allowed` for that provider) isn't.
2. **Content scan of everything else that would be sent** (the user's own messages,
   untainted context):
   - **Deterministic detectors:**
     - secrets: provider key formats, private-key blocks, `password=`-style
       assignments, high-entropy tokens;
     - PII: email addresses, phone numbers, national IDs, card numbers (Luhn-checked),
       IBANs.
   - **A semantic check on the resident local model:** a short classification prompt
     returning JSON categories (health, financial, legal, HR/employment, personal
     relationships, confidential business, e.g. "internal only" or "confidential").
     - It runs **only** when stage A wants cloud, so purely local turns pay nothing.
     - A parse failure, error or timeout counts as **sensitive**
       (`sensitive_check_failed`); the check fails closed.

**What the cloud prompt never contains in privacy mode.** Prompt content
that doesn't come from a tool needs its own rule. Every system-prompt and per-message
injection section declares `cloud_safe=True|False`, and in privacy mode cloud calls are
assembled **without** the `False` sections.

- **Not cloud-safe:**
  - memory: the stable block of profile, preferences, facts and known errors;
  - the per-turn "relevant memories" and recalled procedures injected into the user
    message (`agents/base/memory.py`, `procedural_memory.py`);
  - environment details (user name, home and working paths, connected accounts).
- **An untagged section counts as not cloud-safe.** A test enumerates every prompt
  section and fails on a missing tag.
- So the cloud model answers without personal memory. The hybrid disclosure says so.
- **Memory can't launder taint:** facts extracted from a tainted turn are stored with
  a taint bit. They aren't sent in any case, and the bit keeps that true if a future
  version starts sending memory.

**Cloud turns can't read user data.** In privacy mode, `reads_user_data` tools
(and untagged ones) **aren't offered** to the cloud model at all.

- As a backstop, the pre-call guard re-checks gate P before every cloud call.
- A taint that appears mid-turn ends the turn with `stop_sensitive`.

**Non-LLM egress is covered too.** Search engines and fetched sites are also "the
cloud". In privacy mode, the arguments of every network tool (a search query, a URL)
go through the content scan, plus an overlap check against tainted messages in the
context.

- On a hit, the user is **asked** ("This search includes text from your files — search
  anyway?"). Non-interactive callers are refused.

**Scope of taint:**

- A tainted message stays tainted. Any cloud call would carry the history, so while a
  tainted message is in the context that would be sent, the **session stays local**.
- Hysteresis and declared `reasoning` skills yield to it.
- The route note says "Kept on your PC — this chat includes your files" and offers
  "Start a new chat" to clear it.
- Redacting a tainted message to send the rest is **not in v1**: a missed span would
  be a leak.

**User override:** interactive users get **"Send anyway (this turn)"**.

- It is offered for **deterministic or semantic content hits only**. It is never
  offered for source taint, and never for `sensitive_check_failed`; otherwise timeouts
  would train users to click through.
- The resulting cloud turn carries `reason_code=user_override`.
- The override is written to the ledger as a count (no content), so it can be measured.

**Measured, not assumed (§5):**

- **Leak check:** in tests and the grid, the cloud provider is a capture server
  registered in Lemonade (a `capture.*` provider pointed at a loopback endpoint), so
  outgoing request bodies are inspected for real, not mocked. Planted
  secrets/PII/semantic items must never appear.
- Detector recall and false-positive rate on a labelled corpus.
- The cost of privacy mode, as cloud fraction and quality, run as its own grid arm.

---

## 3. Surfaces

**One specification for option state, two implementations pinned by shared
fixtures.** Each option carries `{enabled, reason_code, reason}`. `reason_code` is a
**closed enum**, shared by option state and per-turn routes, and every value has one
plain-language `reason` string:

| Kind | Codes |
|---|---|
| Option state | `local_only`, `offline`, `managed_by_org`, `blocked_on_corporate_network`, `needs_os_confinement`, `no_key`, `gateway_unreachable`, `model_delisted`, `no_local_model`, `not_discovered` |
| Route: local | `local_only`, `offline`, `sensitive_source`, `sensitive_content`, `sensitive_check_failed`, `blocked_on_corporate_network`, `default_local`, `rule_local`, `skill_small_fast`, `no_grant`, `consent_declined`, `non_interactive`, `primary_unavailable`, `cap_headroom` |
| Route: cloud | `user_override`, `capability_tools`, `capability_vision`, `capability_context`, `skill_reasoning`, `hysteresis`, `rule_cloud` |
| Provider (`provider_reason`) | `primary`, `backup` |
| Cloud-mode failure | `cloud_no_grant`, `cloud_unavailable`, `cloud_cap`, `cloud_blocked_on_corporate_network` |
| Escalation | `esc_tool_parse`, `esc_tool_repeat_fail`, `esc_loop`, `esc_context_overflow`, `esc_skill_reasoning` |
| Mid-turn stop | `stop_local_only`, `stop_offline`, `stop_sensitive`, `stop_corporate_network`, `stop_grant_revoked`, `stop_provider_lost`, `stop_cap` |

Adding a code takes a change to the enum, the golden fixtures, and both clients'
tests.

- **Agent UI:** the Python backend computes the state (`gaia.llm.option_state`) and
  serves it through `GET /api/providers` and a new `GET /api/routing`. The renderer
  only displays it.
- **TUI:** the provider and model screens run **before any agent worker exists**, and
  they read Lemonade directly (`lemonade/cloud.go`, `ui/providers/model.go`). A stdio
  event can't be their source, and the daemon is optional. So Go computes the same
  state from `policy.json`, `config.json` and its own catalog read.
- **Parity:** both implementations run against **one set of golden fixtures**
  (`tests/fixtures/option_state/*.json`: inputs → expected option state), checked by
  pytest and `go test`. A change to one side that isn't made on the other fails CI.
- **Live updates:** the UI backend watches the mtimes of `policy.json` and
  `config.json` and pushes a `routing_state` SSE event. The TUI watches the same files.
  Either way, options update without a restart, whoever changed the setting (UI, TUI,
  or `gaia config set`).
- **Per-turn routes** (§3.1) still come from the agent, because only the agent knows
  them.

### 3.1 Event contract

- **Per-turn route:** `route: {tier, reason_code, provider, provider_reason, model, reason, escalated,
  escalation_reason}` is added to the per-turn `status` event in UI SSE and in stdio.
- **Option state:** the `routing_state` event above carries it.
- Both are specified in `docs/spec/agent-ui-query-sse-contract.md`.
- `reason` text comes from one table keyed by `reason_code`.

### 3.2 Agent UI

| Element | Local only **off** | Local only **on** |
|---|---|---|
| Settings › Privacy | Three-level **network policy** (Cloud allowed / Local only / Offline). Privacy mode toggle (on). "Share routing stats" (off), with **View what's shared**. The `env_var_set` warning if relevant. Locked rows show "Set by your organization" | Local only selected; summary "No GAIA AI request goes to the cloud". Privacy-mode and hybrid rows disabled. **Offline** adds a list of the features turned off, and the enforcement badge (GAIA / OS) |
| Offline-only effects | — | Web, browser, email/connectors, Slack/Telegram, MCP servers, hub browse/install, model downloads, updates, tunnel: each shown disabled with `offline`. Shell / `run_python` / stdio MCP: disabled with `needs_os_confinement` unless the OS confinement is active |
| AMD network | Fireworks options disabled with "Not allowed on the AMD network" while a marker confirms it | Same |
| Mode selector (Local / Hybrid / Cloud) | All enabled; Hybrid/Cloud need an available provider | **Hybrid and Cloud disabled**, labelled "Turn off Local only to use cloud models" |
| Provider list (Fireworks, AMD Gateway) | Available ones selectable; the gateway shows "not reachable" when unavailable | **Shown disabled** with the `local_only` reason. Connect/key entry disabled; stored keys kept, not replayed |
| Model pickers (settings, `ModelChip` dropdown) | Local + cloud models | **Local models only** (the backend omits cloud ids) |
| Settings free-text custom model field | Any id | Only ids the local catalog lists; anything else is refused inline, with the reason |
| Hybrid settings (cloud model, optional backup model on the other provider, dollar cap, token cap for the AMD gateway, consent default) | Editable. The backup picker lists only the other provider's available models, and enabling it asks consent for both providers | Disabled, values kept |
| Session menu / document "Allow cloud" toggles | Shown | **Hidden** |
| Escalation consent prompt | As designed | Never shown |
| `ModelChip` / answer footer | Turn tier, model, cost | "Local only" badge; tier always local |
| Setup / first run | Offers local, hybrid, cloud | Offers local only |

Turning the switch on lists every stored cloud model id and rewrites them after one
confirmation (§2.1.5).

### 3.3 TUI

| Element | Local only off | Local only on |
|---|---|---|
| `/provider` screen | Local / Hybrid / Fireworks / AMD Gateway, the three-level network policy, the privacy-mode toggle, and "Share routing stats" with a payload preview. Fireworks dimmed on the AMD network | Toggle on; cloud entries and Hybrid **dimmed** with the reason |
| `/model` and its completions | Local + cloud ids | Local ids only; a typed cloud id is refused with the error |
| Hybrid settings in `/provider` (cloud model, optional backup on the other provider, dollar cap, token cap for the AMD gateway, consent default) | Editable. The backup lists only the other provider's available models; enabling it asks consent for both providers | Dimmed, values kept |
| Recommended cloud models (`cloud.go` list) | Shown | Hidden |
| Gateway setup screen (`ui/gateway`) | Usable | Dimmed with the reason; no key is posted |
| Offline-only effects | — | Model-pull action hidden; skills that need the network and MCP servers dimmed with `offline` / `needs_os_confinement`; header shows the enforcement badge |
| AMD network | Fireworks entries dimmed "Not allowed on the AMD network" while a marker confirms it | Same |
| Header | Mode, and the last turn's tier | "Local only" |
| Per-turn route note, consent prompt | As designed | No route note beyond "local"; no prompt |

- **Settings path:** all writes go through the same config fields. Switching uses
  `/model` over stdio; the TUI never shells out to the Python CLI.
- **Control API:** the loopback control API exposes `routing_state` and the last
  `route`, so the driving harness can assert both.

### 3.4 CLI and docs

- **CLI:**
  - `gaia config set network_policy cloud_allowed|local_ai_only|offline`, `privacy_mode`, `share_routing_stats` and the `routing_*` keys go in
    `docs/reference/cli.mdx`;
  - `gaia llm` / `gaia prompt` with a cloud model fail with the kill-switch error when
    it's on.
- **Docs:** a new hybrid + Local only guide in `docs/guides/` (added to
  `docs/docs.json`); the SSE contract spec; the privacy section of the Agent UI guide.
  If `hub/agents/gaia` behavior changes, its `README`, `SPEC` and `CHANGELOG` change
  together.

---

## 4. Deriving the policy

### 4.1 Task sets (unit of analysis: the **task** offline, the **turn** online — never mixed)

- **A new `chat` suite of ~120 tasks from ~60 templates** (two instances each),
  covering the flagship's real surface: document Q&A on planted facts,
  data/scratchpad with exact values, web, memory, writing, and skills.
  - Every task has a **mechanical pass check**, which is what makes this many tasks
    affordable and the labels judge-independent.
  - Instances of one template are correlated, so the statistics treat the
    **template** as the cluster (§5).
- **Multi-turn paired set:** `gaia eval agent` scenarios with user turns **replayed
  from a fixed recorded prefix**, so turn N is scored on each tier from an identical
  history.
- **Frozen holdout, chosen at random by template before any grid run:** 40 whole
  templates (80 tasks).
  - Splitting by template stops a near-duplicate of a holdout task from leaking into
    training.
  - It is evaluated **once**, in Phase 6.
  - Training is the other ~20 templates plus the 23 existing dev tasks.
  - `therock` and the `full`-only tasks have no mechanical check. They run as a
    separate dev-work regression report and are **not part of any bar**.

### 4.2 Grid (new `hybrid-grid` job, manual dispatch only)

- **Arms per local-model class** (`Gemma-4-E4B`, `Qwen3.6-35B-A3B`): local-only and
  cloud-only.
- **The cloud arm runs per provider:**
  - **Fireworks** results are public and drive the policy and the bars.
  - **AMD gateway** results are internal and never go into GAIA PRs, issues, docs or
    committed baselines. They're used only to confirm the Fireworks-derived rules hold
    for the gateway. If the pool can't reach it, that arm runs from an AMD-network
    machine with identical tasks and repeats.
- **Credentials:** cloud keys go to Lemonade (`POST /cloud/auth`), never into the
  agent step.
- **Runs:** 5 repeats with `--meter`, one category at a time per machine, judge
  calibrated with `gaia eval tasks controls`.
- **Also recorded:** cache-read/write tokens per call, so §2.6's cache question is
  answered from data.
- **Hybrid** runs as live arms only in Phase 6: **hybrid + privacy mode** (the
  shipped default, which is gated) and **hybrid without privacy mode** (reported).
  They run together with fresh local-only and cloud-only arms on the holdout, so four
  arms at 5 repeats each. Hybrid can't be stitched from grid rows, because escalation
  continues on top of local side effects.

### 4.3 Labels

- **Local-ok:** the local pass rate on the mechanical check is within the
  pre-registered margin of the cloud pass rate.
- **Judge scores** are secondary and reported as a paired difference with a clustered
  bootstrap: a new `paired_clustered_bootstrap(diffs, clusters, one_sided=True)` in `eval/statistics.py`. It raises on n < 2. The existing `bootstrap_ci` is unpaired, unclustered and two-sided, and silently returns `(0.0, 0.0)` for n < 2, so it isn't used.
- **Oracle:** split the repeats. Pick the cheapest local-ok tier on repeats 1–3, score
  it on 4–5.

### 4.4 v1 rules

- Find training-set features that separate local-ok from not, per machine class. Keep
  a rule only if it is simple, names its evidence rows, and improves the paired metric
  on the training set.
- Escalation triggers: measure precision and recall on the paired grid. Drop any
  trigger below 70% precision, because a false escalation costs money *and* is a
  privacy event.

### 4.5 Shadow mode, then v2

- **Shadow:** Phase 3 ships routing as *log-only*, with `routing_mode` unchanged. Logs
  are local to the machine.
- **Getting a traffic mix: opt-in routing stats, counts only.** The setting is
  `share_routing_stats`, off by default and never sent while Offline.
  - **The payload is a weekly aggregate**, uploaded at a randomized time within the
    week so upload time doesn't reveal a timezone or routine:
    - GAIA minor-version index (an integer), machine class (an enum of 3), mode (an
      enum);
    - **bucketed** counts (`0`, `1–5`, `6–20`, `21–100`, `>100`) per `reason_code`,
      `provider_reason`, escalation trigger and stop code;
    - sensitive turns as **one** bucketed count, with **no categories**, so the upload
      can't reveal health/HR/financial patterns;
    - local and cloud turn buckets, and coarse latency buckets.
  - **What it never contains:** query or response text, file or model names,
    timestamps finer than a week, or any stable identifier. Each upload carries a
    fresh random nonce.
  - **The receiving endpoint must not log or retain the uploader's IP address.** That
    is a requirement on open question 1, verified in its privacy review.
  - **Enforced by schema:** `additionalProperties: false`. Every value is an integer
    or a member of a closed enum; there are **no free-form strings**. A test fails if a
    free-form string or a new key appears, so the payload can't grow content by
    accident.
  - **"View what's shared"** shows the exact JSON that would be sent, before opting in
    and at any time afterwards.
  - The online "kept local" share is reported, weighted by the uploaded `reason_code`
    mix, and labelled by population.
- **What shadow can and can't do:** it measures the mix, trigger firing rates and
  drift. It can't measure escalation precision, because only the tier that ran has an
  outcome. Precision stays an offline metric.
- **v2 scorer:** counts can't train a per-turn model, so v2 can use only features
  available offline (grid tasks) plus the count-weighted mix. It ships only if it beats
  v1 on a *new* holdout by a pre-registered margin. The Phase 6 holdout has already
  been spent.

---

## 5. Measuring success

**Pre-register before the grid:** margins, repeats, holdout membership, and the test
for each row. **Report per machine class.** All arms get identical tasks, repeats and
judge, and table columns sum to totals. Each arm gets a per-trace analysis (tokens incl.
thinking, steps, tool calls, wall time, cloud $, cache $, escalations, non-LLM egress
calls).

**Power, with the arithmetic:**

- **Assumptions:** per-task paired pass-rate differences have SD ≈ 0.3, and the true
  difference is 0.
- **Requirement:** a one-sided non-inferiority test at α = 0.05 with 80% power and a
  0.10 margin needs n_eff ≈ (1.645 + 0.84)² × 0.09 / 0.01 ≈ **56 independent
  tasks**.
- **Clustering:** with 2 tasks per template and an assumed within-template
  correlation of ρ = 0.3, the design effect is 1 + (2 − 1) × 0.3 = 1.3. The
  **80-task holdout** therefore gives n_eff ≈ 62 ≥ 56.
- **Analysis:** a template-clustered bootstrap.
- **Re-check before the holdout is touched:** the Phase 1 pilot measures SD and ρ on
  the *training* templates. If the measured values put n_eff below 56, more templates
  are written and the holdout is re-drawn **before** anything is evaluated on it.
- **Margin:** a 0.05 margin would need ~220 independent tasks, which isn't feasible.
  The margin is 0.10, and that is stated, not hidden.

| Question | Unit | Metric | v1 bar |
|---|---|---|---|
| Quality kept? | task | Paired pass-rate difference, **hybrid + privacy mode (the shipped default)** − cloud-only, on the holdout. Hybrid without privacy mode is reported alongside | Lower 95% one-sided bound ≥ −0.10. Judge difference reported |
| Kept local? (offline) | task | Share of tasks whose LLM calls all stayed local, on the holdout, for the shipped default | Small PC ≥ 50%, Strix Halo ≥ 80% |
| Kept local? (online) | turn | Share of turns with no cloud LLM call, from uploaded shadow/live logs | Reported, labelled by population |
| Non-LLM egress | task | Web/MCP/connector calls per task, all arms | Reported, so "kept local" isn't read as "nothing left the PC" |
| Cloud spend | task | Cloud $ per task vs cloud-only, metered, including cache effects | Reported only in v1. A spend bar needs a named premium-model arm, which isn't in v1's grid (open question 7) |
| Local cost | task | Wh per task (board power × wall time) | Reported, never "$0" |
| Latency | task | p50/p95 submit→answer, including routing inputs, rebind and escalation | p50 ≤ cloud-only + 20%; p95 reported |
| Router quality | task | Absolute regret vs split-repeat oracle; trigger precision and recall | Regret ≤ 0.05 pass rate; precision ≥ 70% |
| Privacy | — | Cloud LLM requests under Local only, without a matching grant, from an unpinned call site, or to Fireworks while on the AMD network | **0**, as an adversarial test |
| Privacy mode leaks | task | Planted secrets, PII and tainted-source text found in **captured** cloud request bodies (capture provider, §2.8) | **0** for deterministic classes and source taint |
| Privacy mode detection | item | Recall of the semantic check on a labelled sensitive set; false-positive rate on the `chat` suite | Recall lower 95% bound ≥ 0.95; false-positive rate reported |
| Privacy mode cost | task | Cloud fraction and paired quality, hybrid + privacy vs hybrid without | Reported. The gated quality row above already uses hybrid + privacy |
| Offline | — | Non-loopback connection attempts from GAIA processes during a full offline suite run, observed at the OS (packet capture / `ss`) | **0**, and every offline-disabled feature refuses with `offline` |
| Stability | — | Replayed snapshot → same decision; flips per session | Determinism test passes; flips reported |

**Kill check, end of Phase 1** (offline, task unit, training set): stop for a machine
class if the **oracle** can't keep the offline "kept local" share at the bar while
staying within the quality margin. A policy can't beat its oracle.

**Regression gates:** unit and adversarial tests on every PR. Any change to the
rebind, the prompt, or the location text needs a `gaia eval agent` re-run of the
touched categories on the pool, serially, listing PASS→FAIL and FAIL→PASS against the
flagship baseline and re-running lone flips.

---

## 6. Tests

- [ ] **Kill switch, adversarial.** With Local only on, each path below fails with
      `CloudDisabledError`, and a **network-level assertion** shows no request reached
      a cloud host:
  - **Fail-closed allowlist:**
    - `fireworks.*`, `amd.*`, `vercel.*`, a `claude-*` id;
    - a dotless Lemonade router alias;
    - an id missing from the catalog;
    - an unreadable catalog;

    **all refused.** A local model pulled a moment ago is allowed.
  - `create_client("claude")` with any model id;
  - every `LemonadeClient` model-taking method (chat, completions, `responses`,
    embeddings, VLM);
  - session auto-titling (now local in every mode);
  - extraction, memory and RAG;
  - a schedule, `gaia chat` with a cloud `default_model`, `gaia llm` with a cloud id;
  - the judge via `anthropic`, the judge and runner via a `claude -p` subprocess, and
    `tool_cost`'s `OpenAI` client;
  - a sidecar whose `/version` `policy_enforced` lacks `"local_only"` (stopped before any request
    reaches it, with a named fix). A running old sidecar is stopped when the switch
    turns on;
  - **model management still works:** `pull_model`, `load_model` and `gaia download`
    of a local model succeed while Local only is on;
  - escalation; the switch toggled on mid-turn (the next call is refused);
  - `policy.json` unreadable or half-written (treated as on, with the file named);
  - Local only on with mode `hybrid` (crash between writes, unreadable `policy.json`,
    hand edit): the effective mode is local, a notice is shown, and nothing fails;
  - turning the switch on sets `routing_mode=local` in the same confirmed operation;
  - the TUI gateway screen refuses to write a cloud `default_model`;
  - **Stored cloud ids:**
    - turning the switch on lists `default_model`, `last_model`, `custom_model` and
      session models, then rewrites them after one confirmation;
    - a cloud id restored at startup returns `local_only` and opens the picker; it
      doesn't fail the first turn;
  - **Refused write paths:** `select_model`, `connect_provider`, the Settings
    custom-model field, `gaia gateway use`, `gaia config set default_model`, and the
    TUI's `/model` and `/provider`;
  - **Key paths:**
    - the providers list, `connect_provider` with a blank key, and both daemon
      authenticate routes don't re-send keys;
    - the Go `Configure` refuses;
    - runtime keys are cleared;
  - the `env_var_set` warning;
  - turning it on with no local model downloaded (refused, with a pointer to the
    download).
- [ ] **Network policy levels:**
  - the effective level is the stricter of user and managed;
  - a user can't loosen a managed floor;
  - an unreadable user file reads as Local only, and an unreadable managed file as
    Offline;
  - `gaia config set network_policy` round-trips.
- [ ] **Offline, adversarial:**
  - every row of §2.1.7's three layers refuses;
  - a third-party library opening a socket in-process is refused by the audit hook;
  - the Go `DialContext` refuses non-loopback, and the census fails on
    `http.DefaultClient`;
  - Electron cancels a renderer `fetch` to a public host;
  - shell/`run_python`/stdio MCP are disabled without OS confinement and allowed with
    it;
  - under confinement a child's network namespace has only `lo` (Linux), or its
    AppContainer has no `internetClient` (Windows). Where creating the namespace fails
    (an AppArmor restriction), the feature stays disabled;
  - the audit hook refuses UDP `sendto`, and a `multiprocessing` spawn child has it;
  - Electron main-process Node `https` to a public host is caught by the census;
  - a frozen sidecar without `"offline"` in `policy_enforced` isn't started in
    Offline;
  - the Offline status indicator makes **no** outbound connection (OS capture while
    it refreshes);
  - a network-tagged skill is unavailable;
  - switching to Offline with no local model is refused;
  - an OS-level packet capture during the offline suite shows zero non-loopback
    connections from GAIA processes.
- [ ] **AMD network:**
  - with a configured **marker** resolving, a Fireworks request is refused at the
    request layer, the backup never falls through to Fireworks, cloud mode with a Fireworks
    primary fails with a clear message, and Fireworks keys aren't sent;
  - a marker check timing out counts as on the AMD network;
  - with no marker configured the rule is inactive;
  - the VPN connecting mid-turn blocks the very next Fireworks call;
  - `treat_as_corporate: always` blocks Fireworks off the VPN;
  - **external user:** no marker is configured, the public gateway default resolves,
    and Fireworks is still allowed. This is the regression test for the
    public-DNS trap;
  - a marker returning NXDOMAIN (off the VPN) allows Fireworks, so the backup works;
  - a frozen sidecar without `"amd_network"` in `policy_enforced` isn't started while
    the rule applies;
  - while the rule applies, `vercel.*` and a dotless router alias are refused;
  - no marker hostname is committed (a check in the internal release pipeline, against
    a denylist that lives outside the repo).
- [ ] **Privacy mode:**
  - a file/RAG/email/memory read taints the session and the turn stays local, with no
    escalation;
  - **memory in the prompt:** with a stored profile and facts, a cloud call's captured
    body contains none of them, from either the stable block or the per-turn injected
    memories;
  - a fact extracted from a tainted turn is stored with the taint bit;
  - every prompt section declares `cloud_safe`, and an untagged one fails the test;
  - **a cloud turn can't read files:** `reads_user_data` and untagged tools are
    missing from the cloud tool list; a forced call ends with `stop_sensitive`;
  - an untagged MCP tool's output taints; a pre-taint-era history row taints;
  - a web search whose query contains planted PII or text from a tainted message
    asks first, and is refused for non-interactive callers;
  - "Send anyway" isn't offered for `sensitive_check_failed`, and a granted override
    carries `user_override`;
  - a shareable document doesn't taint;
  - web results don't taint;
  - each deterministic detector catches its planted item;
  - a semantic-check timeout or bad JSON gives `sensitive_check_failed`;
  - "Send anyway" works for content hits, isn't offered for source taint, and is
    counted;
  - **capture-provider test:** no planted item appears in any captured cloud request
    body across the privacy corpus;
  - "Start a new chat" clears taint.
- [ ] **Routing stats:**
  - the payload validates against the closed schema;
  - a test that adds a string field or a query fragment fails;
  - nothing is sent when the setting is off or while Offline;
  - "View what's shared" matches the bytes actually sent;
  - each upload's nonce differs;
  - counts are bucketed and there is no per-category sensitive count;
  - the upload time is randomized within the week.
- [ ] **Census:** the static scan fails on a planted `/chat/completions` literal, an
      `anthropic.` call, and a `claude` subprocess placed outside the allowlisted
      modules.
- [ ] **Options follow the switch:**
  - for each element in §3.2 and §3.3, the option state matches the table both off and
    on;
  - Python and Go both pass the shared golden fixtures in
    `tests/fixtures/option_state/`;
  - a change made from the UI, from the TUI, and from `gaia config set` each updates an
    open UI window and the TUI without a restart.
- [ ] **Precedence:**
  - mode × Local only × stage A × stage B, with a table test per row of §2.3;
  - pairwise conflicts: Local only vs a `reasoning` skill, a revoked grant vs
    hysteresis, cap vs capability, primary not granted but backup granted;
  - every `RouteDecision` carries a `reason_code` from the enum and, when the tier is
    cloud, a `provider_reason`.
- [ ] **Determinism:** the same input snapshot gives the same `RouteDecision`.
- [ ] **Rebind:** after local→cloud→local, client, config, `model_id`, tool-schema mode
      and eviction match a freshly built agent; the fast path is used for a same-client
      model change.
- [ ] **Order of operations:** routing sees untrimmed history; trimming uses the routed
      model's budget; the preflight lease and context check run on the routed model.
- [ ] **Side effects once:** a file write before escalation runs exactly once,
      including when the cloud call then hits a retryable error.
- [ ] **Bounds:** at most 1 escalation; no de-escalation; a mid-turn failure of the
      request guard or of any stage-B gate (P, 0–3) ends the turn with a caveat and
      the matching `stop_*` code.
- [ ] **Promises without another test:**
  - an approved cap overage writes an `override` row;
  - a cap below one estimated turn shows the Settings warning;
  - **dynamic check:** on a cloud turn with privacy mode off, memory extraction and
    embeddings still hit only local models (captured requests, not just the static
    census);
  - while a tainted message is in context, a hysteresis turn stays local with a
    privacy code;
  - two OS users on one machine have separate ledgers and caps.
- [ ] **Providers:**
  - **No backup:** gateway unreachable → local, `primary_unavailable`, and **no
    Fireworks request**. A revoked key or a delisted model gives the same result.
  - **Backup configured and granted:** the turn goes to Fireworks with
    `provider_reason=backup`. `reason_code` still names why the turn went to cloud
    (e.g. `capability_tools`), and the route note names Fireworks.
  - **Primary drops mid-turn:** `stop_provider_lost`. The backup is never used
    mid-turn.
  - **Primary not granted, backup granted:** the backup is used. The gates are
    checked per candidate.
  - **Cloud mode, no candidate passes:** the turn fails with a `cloud_*` reason; it
    never runs locally.
  - **Backup configured but not granted this session:** local, and no Fireworks
    request.
  - A backup on the same provider is refused at config load.
  - Consent is per provider: a grant of `{amd}` doesn't cover Fireworks.
- [ ] **Token budget:** an AMD-gateway call that would cross
      `routing_cloud_cap_tokens_month` is stopped with `stop_cap`.
- [ ] **Ledger:**
  - two processes reserving per call concurrently can't jointly exceed the cap;
  - a call whose worst case would cross the cap is stopped with `stop_cap` before it
    is sent;
  - `route_turn` stays pure: it never writes the ledger;
  - a locked or corrupt DB raises a named error;
  - unpriced reads `n/a`.
- [ ] **Agent cache:** in the UI, a local↔cloud flip within a session reuses the same
      cached agent (keyed on mode), and MCP connections stay up.
- [ ] **Privacy:**
  - prompt-injection attempts to reach the cloud without a grant;
  - a disallowed document reached indirectly (memory or a RAG hit);
  - the call-site census.
- [ ] **Cold state:** no key, no cloud model discovered, local model not pulled,
      offline.
- [ ] **Contract:** one `require_lemonade` integration test of a tool-calling turn
      through a Lemonade-registered cloud model.
- [ ] **Location text:** `inference_location` is never "local" on a turn with any
      cloud call.

---

## 7. Phasing (each phase depends only on earlier ones)

| Phase | Deliverable | Exit |
|---|---|---|
| 0 | **Local only** switch, scoped to what exists today: `policy.json` + atomic config writers; `@local_only_guard` on the listed `LemonadeClient` inference methods (model management exempt, plus the inference-method test) and in `create_client`; auto-titling through `LemonadeClient`; judge/runner/`tool_cost` checks; census scan; one gated key re-send function (Python) + Go key-path checks; stored-cloud-id rewrite and refusal of cloud **model ids** on every existing write path; sidecar `/version` `policy_enforced` check; option state (`local_only`, `no_local_model` codes only) for the **existing** provider and model surfaces, in Python and Go with shared fixtures; docs | Kill-switch, census and option-state tests pass; a network-level assertion shows zero cloud requests. **No dependency on later phases:** `routing_*` fields, availability reason codes and the mode selector don't exist yet, and each is born with its Local only check in the phase that adds it |
| 0b | **Offline** + **AMD-network rule** + managed policy file. **UI and TUI:** the three-level network-policy selector (replacing Phase 0's toggle), the Offline badge and list of turned-off features, "Set by your organization" locks, "Not allowed on the AMD network" rows, and the option codes `offline`, `needs_os_confinement`, `managed_by_org`, `blocked_on_corporate_network` in Python and Go with shared fixtures (§3.2, §3.3). **Backend:** `network=True` / `reads_user_data=True` tool tags; the `is_offline()` guards on background paths; in-process loopback guards (Python audit hook, Go transport, Electron); Linux network-namespace child confinement; Windows AppContainer if resolved (open question 9); the marker check in the request guard | Offline, AMD-network and option-state tests (both clients, every new code) pass; the OS packet capture shows zero egress |
| 1 | `llm/pricing.py` (single rate card) + SQLite ledger; `chat` suite + replayed multi-turn set + frozen holdout; `hybrid-grid` job; local and cloud arms (Fireworks public, gateway internal); oracle | Ledger reconciles with the **Fireworks usage export** within ±5%; kill check passes per machine class |
| 2 | Rebind in `base/` + fast path; `current_route`; call-site pinning + census; call-level retry; cache decision from Phase 1 data | Rebind-equivalence and side-effect-once tests pass; `gaia eval agent` shows no regression |
| 3 | **Privacy mode ships with routing, never after it:** taint, `cloud_safe` prompt sections, cloud tool filtering, detectors, semantic check, network-tool argument scan, "Send anyway". `cloud_availability.py`; `route_turn` + precedence + v1 rules; config fields incl. backup model + per-provider `cloud_allowed`; cap headroom (stage-B gate 3) + per-call reservation in `_pre_llm_call_guard`, in dollars and in tokens (the ledger exists since Phase 1); shadow logging | Precedence, determinism, ledger-concurrency, privacy-mode and capture-provider tests pass. `routing_mode=hybrid` is rejected by config validation in any build without privacy mode |
| 4 | Escalation and consent prompts (pre-routed and escalation); `route` in `status`; SSE spec; opt-in **routing stats** (schema, preview, upload, once open question 1 is resolved) | Bounds and side-effect-once tests pass; stats schema test passes |
| 5 | UI and TUI hybrid surfaces, labelled **Preview**; guide | Parity test; the `driving-the-tui` harness asserts route notes |
| 6 | **Four arms on the holdout** (local-only, cloud-only, hybrid, hybrid + privacy mode), 5 repeats each, per machine class → §5 bars → drop the Preview label per class | All §5 rows met for that class |

- **Rollback** at any point: `routing_mode=local`, or Local only.
- Phases 0 and 1 are worth shipping even if hybrid is killed: a provable "no cloud"
  guarantee, one rate card, and an honest ledger.

---

## 8. Open questions

**Settled by the user (2026-10-26):**

- **Cloud payload:** privacy mode (on by default in hybrid) keeps sensitive content and
  any session that read user data local (§2.8).
- **Routing stats:** opt-in, counts only (§4.5).
- **Spend budget:** per user (§2.5).
- **Local only and Offline:** available to every user (§2.1).
- **AMD network:** never Fireworks (§2.1.8).

**Still open:**

1. **Stats endpoint (blocks 4's upload):** where routing stats are received and who
   can read them, e.g. a route on the hub Worker. It **must not log or retain client
   IPs**, and a privacy review of the closed schema plus that guarantee is needed
   before the first upload.
2. **Disclosure wording (blocks 5):** legal/privacy review of the hybrid opt-in,
   privacy mode, Local only and Offline help text.
3. **Semantic-check categories (blocks 3):** whether the list in §2.8 matches what
   AMD and users consider sensitive, and whether "confidential business" needs
   org-specific terms delivered through the managed policy file.
4. **Claude-direct tier (post-v1):** only if the grid shows Fireworks/AMD-gateway
   models fall short.
5. **Managed-policy distribution (blocks 0b for AMD fleets):** how IT installs the
   managed file and `network_markers` on AMD laptops (MDM / Intune).
6. **Local-model classes beyond the two measured:** they get their own grid, or ship
   hybrid as Preview only.
7. **Premium-model arm (post-v1):** which premium cloud model earns a "≥ 40% lower
   spend" bar. Until then spend is reported, not gated.
8. **Lemonade's own egress:** whether `lemond` makes any background connection
   (update checks, telemetry). GAIA does not confine `lemond` in v1 (it needs root), so
   upstream should be asked for an offline flag.
9. **Windows AppContainer confinement (blocks Windows shell/MCP in Offline):**
   which launcher and capability set, and whether `run_python`'s interpreter works
   inside it. Until then, Windows Offline runs with GAIA enforcement only, and the
   shell and MCP stay off.
10. **Marker distribution for unmanaged AMD developers:** where the internal setup
   guide lives, and whether the internal GAIA build should ship the marker in its
   managed file by default.
