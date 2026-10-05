# Changelog

All notable changes to `@amd-gaia/gaia` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Every tool is offered on every turn again, as in the Agent UI.** Per-turn
  tool selection swapped about a dozen tools in and out at its cap, which broke
  the local model's prompt cache (17s to first token on a one-line question,
  0.4s without it) and left out the tool a question needed: a CSV question was
  given web tools and fetched an unrelated page. `GAIA_DYNAMIC_TOOLS=1` turns
  selection back on. The registered count is 95: `load_tools`, the selector's
  escape hatch, registers only while selection is on.
- **A turn's silent opening now says what the model is doing.** A thinking
  model sat on "Getting started" for 10-20 s while it read the prompt and then
  reasoned in a paragraph released only once finished. `status` events gain an
  optional `phase` (`loading_model`, `downloading_model`, `reading`,
  `reasoning`, `tool_call`) with a `words` or `chars` count, sent when each phase
  actually starts; the terminal UI shows them as "Reading your request",
  "Reasoning · 214 words" and so on. Clients that ignore the field see the same
  sentence in `message`.
- **GPU models no longer load at 32K on an NPU-profile machine.** The NPU's
  32,768-token ceiling was applied to every model whenever `default_device` was
  `npu`, so a GGUF model ran at half its window and long tasks overflowed. It now
  binds NPU (FLM) models only. A model can also opt in to a window sized to the
  machine's memory, up to its native maximum.
- **A stale `GAIA_SKILL_SET` now stops startup.** An undeclared name used to be
  dropped silently, so the agent came up healthy with no skills. The sidecar
  and the stdio entry now exit non-zero before serving, with a message naming
  the valid sets; leave the variable unset to start without one.
- **The code index is built on first search, not at task start.** In 32
  SWE-bench tasks the agent never searched it, yet every task embedded the whole
  repository in the background: about 2,000 local embedding requests per six
  tasks. The first `search_code_index` now builds it; `GAIA_PROJECT_MAP_AUTO_INDEX=1`
  restores building at task start.
- **The first answer on a local model starts in seconds, not after a ~20 s
  silence.** The terminal UI now shows a "Getting GAIA ready" stage before the
  chat: the agent starts, loads its model and reads its system prompt there,
  step by step. New stdio sentinel `warm_up` (answers `warmed_up`, or
  `warm_up_skipped` for a remote model).
- The default chat model now follows the hardware. On a PC whose GPU has
  ~27 GB for models — a 64 GB+ Strix Halo or a 32 GB GPU; the 23.3 GB model also
  needs its context cache — `gaia init` sets up Qwen3.6 35B A3B (a 23 GB Lemonade
  built-in MoE, run with thinking on) and records it as `default_model`; the
  agent and its `GET /v1/gaia/init` readiness check use it for chat. Gemma 4 E4B
  is still downloaded for vision. Every other PC, including a CPU-only one, keeps
  Gemma alone.
- **Qwen3.6 gets the longest context this PC's memory holds.** Up to its native
  262,144 tokens on a Strix Halo (a 5.4 GB KV cache), about 152K on a 32 GB GPU,
  never under 64K. Gemma stays at 64K.
- Qwen3.8 Flash Next (82 GB, multimodal, thinking on) is available on a
  128 GB Strix Halo and never picked as a default; it needs ~90 GB for models.
  Choose it with `gaia config set default_model user.Qwen3.8-Flash-Next-GGUF`;
  `gaia init` refuses it on a PC that cannot hold it.
- **Bypass permissions is now called full access, everywhere.** `--full-access`
  and `/full-access` replace `--bypass-permissions` and `/bypass`; the old names
  fail with a message naming the new one. `/full-access always` (or
  `gaia config set full_access true`) keeps it on across launches.
- **The agent has to read a file before it changes it.** `edit_file`, and
  `write_file` on an existing file, now refuse a file the agent hasn't read with
  `read_file` in this session, or one that changed on disk since it did. Benchmark
  runs caught the agent patching files it had never opened, from a grep snippet
  or a guess; now it has to look first. A partial read counts, creating a file
  needs no read, and the refusal comes before any approval prompt. It applies
  with confirmations bypassed too.
- **The agent sees every installed skill and loads the one that fits.**
  Previously a per-turn matcher scored the request against skill descriptions
  and loaded a skill on 0 of 24 benchmark tasks, so most GitHub requests never
  learned `gh` was available. The system prompt now lists each installed skill
  in one line (~1,000 tokens for the starter pack), and refusing a skill-gated
  CLI names the skill to load. `GAIA_SKILL_DISCOVERY=0` still hides the list.
  **Removed:** `GAIA_SKILL_DISCOVERY_TAU` is now ignored, and
  `GaiaAgentConfig(skill_discovery_threshold=…)` raises `TypeError` — drop the
  argument. See the Agent Skills spec, "Skill catalogue".
- Contract `apiVersion` is now **2.14** (2.13 added `GET /memory`) for the two new routes and
  the `claude` provider value. A differing major still raises
  `VersionMismatchError`; a higher minor is accepted.
- **Changing `model` on a live `session_id` switches in place** instead of
  returning 409, so the conversation and any loaded skills survive it. A switch
  that fails still returns 409 and leaves the session on its previous model.

### Fixed

- **`--use-claude` works with the downloaded binary.** The release build left
  out the Anthropic client, so every Claude launch from the terminal UI, and
  every `/model` switch to Claude, failed with "The 'anthropic' package is
  required", which a frozen binary cannot act on. The client is now bundled, and
  the release build fails if it is missing.

- **Code search works in an Agent UI chat that is not inside a project.** It
  started in GAIA's own documents folder, which usually does not exist yet, so
  every `search_code_index` failed with "repo_path does not exist". It now starts
  in a repository the chat can reach, else any folder it can reach that exists.

- **`/model` no longer offers speech or other non-chat models as chat
  targets.** Whisper was listed as a "chat-capable" local model; switching to it
  reported success and broke the next turn. Transcription, speech, music,
  classification, upscaling and 3D models are excluded; a model labeled `chat`
  still qualifies even if it also transcribes.
- **The agent no longer starts an unrelated job after answering.** A bugfix
  request loaded the `coding` skill, whose "find every call site" tip switched
  on document inventorying; after the fix was done and verified, the agent spent
  minutes extracting every file it had read until the user cancelled. Only a
  skill built on the extraction tool turns inventorying on now. Separately,
  once a turn has answered, a tool call on nothing the request touched is not
  run and a second one ends the turn, and a tool that keeps failing with new
  arguments is stopped like an identical repeat.
- `/v1/gaia/query` now honours `provider` on an existing session. It used to
  matter only when the session was created, so `provider: "lemonade"` could keep
  sending a Claude session's conversation to Anthropic, and `provider: "claude"`
  could run locally. A different provider now switches the session in place.
  Naming a `model` that belongs to the other provider is a 400, on new and
  existing sessions alike. Omitting `provider` and naming a Claude `model` now
  starts a Claude session, the way it already switched an existing one — it used
  to point the local backend at an id it cannot serve.
- "Lemonade is not reachable" errors no longer tell users to run
  `lemonade-server serve`, a command current Lemonade installs don't have. The
  `GET /v1/gaia/init` hint, run errors, and `/model` now say how to start
  Lemonade on the user's own install (tray app, macOS app, service, or CLI).
  `/model <unknown>` with nothing downloaded likewise names the download command
  the host actually has, instead of the removed `lemonade-server pull`.
- `gaia serve` no longer exits 0 when Ctrl+C fails to stop the sidecar. The
  error naming the surviving process and how to kill it was discarded, so the
  next `serve` hit an unexplained port conflict. It now prints that error and
  exits 1.
- Conversation state is saved under `~/.gaia/sessions` instead of the directory
  the agent was started from. A `session_id` must be 1–128 characters from
  `A-Z a-z 0-9 . _ -`; any other value is a 400 on `/query` and
  `/sessions/{session_id}/bypass` instead of a 500. A corrupt saved session fails
  the request instead of being silently replaced.
- `gaia hub install gaia` no longer refuses Intel Macs: the hub manifest now
  lists `darwin-x64`, which the release already builds and the lock already ships.
- The hub install card advertises the declared npm package instead of an unpublished PyPI wheel.
- The readiness check (`GET /v1/gaia/init`) and the terminal session's health
  report name GAIA's own Lemonade Server instead of Lemonade's default port.
- Clearing a TUI conversation now also clears the flagship stdio agent’s prior
  conversation context, while preserving the selected model, skills, and permissions.
- Internal session deletion (not yet exposed by a route) refuses busy agents instead of closing them mid-turn.
- Scratch files no longer land in the user's project. The system temp dir was
  out of scope, so the agent wrote throwaway test runners and intermediate files
  into the repository instead. It now gets its own scratch directory, named in its
  prompt and deleted when the agent closes; the rest of the temp dir stays denied.
- Every file tool now checks the same allowed-folders rule as `read_file`:
  browsing, folder and content search, recent files, document indexing, folder
  watching, code indexing, and where screenshots, transcripts and exports are
  saved. A folder outside the scope gets the usual approval prompt, and a folder
  you approve is readable by document indexing too.

### Added

- **A committed, machine-readable `/query` contract.** `openapi.gaia.json` in
  the Python package is generated from the live routes
  (`python -m gaia_agent.export_openapi`) and checked for drift in CI, so a
  typed client no longer has to reverse-engineer the body from prose —
  `query`, `run_id`, and `context` are required, `run_id` must be a UUID, and
  the bearer-auth posture is declared in the schema. Swagger UI (`/docs`) is
  disabled on the sidecar — it loads its JS from a CDN, an unexpected network
  call for an offline embedder — but `/openapi.json` is still served.
- **Programs the agent starts no longer inherit GAIA's internal credentials.**
  Shell commands, MCP servers, CLI installs and sign-ins, native hub agents,
  media tools and the Lemonade server get the sidecar's environment minus
  GAIA's own tokens. Your own variables (`GH_TOKEN` and the like) still pass through, so CLI skills
  keep working. An embedding app can withhold more names with
  `GAIA_CHILD_ENV_DENY` (comma or space separated), and can stop GAIA loading
  `.env` files with `GAIA_NO_DOTENV=1` — read from the real environment, so a
  `.env` cannot set it.
- **The agent can drive a real browser.** Pages behind JavaScript or a login
  used to be out of reach — `fetch_page` is one HTTP GET, so a signed-in inbox
  or a dashboard came back empty. Eight new tools open a real Chromium, read
  it, click and type in it, and sign in to a site; the password goes to the
  human, never the model, and the session is stored encrypted. Acting inside a
  session you signed in to asks first. Ships behind the optional `browser`
  extra; without it none of the eight register. Registered tool count goes
  88 → 96.
- **Fast mode, for a session that's only conversation.** `GAIA_FAST=1` drops
  the flagship to a plain conversational surface for the whole session —
  3,110 tokens of fixed prompt instead of 17,942 — so saying "hi" no longer
  costs as much as a repo search. Session-scoped and one-way: a fast session
  has no documents, files, web or skills and cannot pick them up mid-way.
- **`chat`, `doc` and `file` are no longer offered as agents.** New users saw
  four entries in the picker where only one is the product. The three ids still
  resolve, so existing sessions, `*-lite` aliases and eval scenarios keep
  working — they are hidden, not deleted, and `ChatAgent` remains the
  flagship's base class. A session created without an explicit agent now lands
  on the flagship rather than a hidden agent.
- **Complete inventories from long documents.** Asking for every exercise,
  action item or finding in a transcript now returns all of them, each with its
  source quote, instead of a condensed list. The opt-in `document-extract` skill
  drives new `extract_document_items` and `save_extracted_items` tools; a save is
  reported only after the exact file is written and read back, and anything
  unfinished is reported as incomplete. `gaia-voice` gains one routing line
  (702 tokens).
- **The agent can set up a skill's CLI instead of handing the job back.** Asking
  it to triage GitHub issues on a machine without `gh` used to end the
  conversation. Three new tools — `check_cli_setup` (read-only), `install_cli`
  and `sign_in_cli` — let it report exactly what is wrong, install the CLI with
  the machine's package manager, and drive the browser sign-in. Both mutating
  tools are confirmation-gated on every call and no skill grant pre-approves
  them; over `/v1/gaia/query` they are refused, like every other gated tool
  (§8). Registered tool count goes 83 → 86.
- **A shell command's `cd` now survives to the next one.** Every
  `run_shell_command` call used to start from scratch, so `cd build` in one
  call was invisible to the next. `get_shell_state` reads the session's
  current directory, and `reset_shell_session` returns it to where the task
  started. Registered tool count goes 86 → 88.
- **Say something while the agent is still working.** `POST
  /v1/gaia/query/{run_id}/followup` hands a live run a message the user typed
  after it started (contract **2.15**). The run is not interrupted and no
  second turn starts — the agent folds the text into the turn already running
  at its next step boundary, so a correction during a five-minute task changes
  that task instead of arriving after it finished. Unknown run → `404`, an
  agent that cannot take one → `409`; both loud, because the caller has already
  taken the message from the user. See SPEC §5.6 and SKILL §7.
- **Approve a gated tool over HTTP.** `write_file`, `run_shell_command` and the
  seven other confirmation-gated tools can now run through `/v1/gaia/query`:
  the stream stays open on `needs_confirmation` and
  `POST /v1/gaia/query/{run_id}/tool_decision` answers it. Previously the only
  possible answer was a refusal, so those tools were unreachable over HTTP.
  `POST /v1/gaia/sessions/{session_id}/bypass` turns the asking off for a
  session. A run that cannot answer — no `session_id`, or
  `can_answer_questions: false` — is still refused. See SKILL §8.
- **Claude as an inference backend.** `provider: "claude"` sends the
  conversation to Anthropic's API instead of the local server; `model` then
  names a Claude model. Anything outside `lemonade` / `claude` is still a 400.
- **`gaia-agent --serve` works from a pip install.** The console script pointed
  past the transport dispatcher, so the documented HTTP mode exited with
  "unrecognized arguments".
- **Tracked eval scorecard ([`SCORECARD.md`](./SCORECARD.md)).** The agent now
  ships a per-release scorecard: judged-scenario pass rate (the aggregate)
  plus per-category rates and the judge's average score across the
  13-category eval corpus (`eval/scenarios/gaia_*`), generated by
  `hub/agents/gaia/python/packaging/gen_scorecard.py` from real
  `gaia eval agent --agent-type gaia` runs. CI runs a PR subset
  (`test_gaia_agent_eval.yml`), the weekly sweep runs the full corpus, and the
  release pipeline gates on the committed card
  (`scorecard-gate` + a judged eval subset in `release_agent_gaia.yml`).
  The initially committed card is a harness validation measured with the
  agent on `claude-haiku-4-5` (the dev environment cannot run Lemonade); the
  first full runner refresh (`gaia_scorecard_refresh.yml`) replaces it with
  the Gemma-4-E4B product baseline.
- **`capture_skill` — capture a skill from pasted `SKILL.md` text, a URL, or a
  local folder, with code inert until trusted.** The agent can now bring a
  skill into `~/.gaia/skills` straight from the conversation (68 registered
  tools, up from 67). Every capture is confirmation-gated, security-audited (a
  `BLOCK` verdict refuses it), SSRF-guarded on the URL path, and lands at the
  `experimental` tier. Instructions load immediately; any `tools.py`/scripts
  the bundle carries stay **inert** until the user runs
  `gaia skill promote <name>` in a terminal, which re-audits and binds trust
  to the audited bytes. Over `/query`, `capture_skill` is gated like the
  other confirmation tools (SKILL §8; SPEC §5.2 documents the load-time
  deferral).
- **`run_python`, always on.** A quick calculation or data transform is now one
  confirmation-gated call that runs from the project root and returns what it
  printed, instead of a throwaway script left in your repository. It joins the
  always-on tool set (about 250 more prompt tokens per call) and the `shell`
  bundle.
- Opt-in developer-mode skill and consent-gated MCP handoffs to Claude Code/Codex,
  with managed worktrees, approved feedback snapshots and reported preview results.
  Python `[mcp]` installation is required for the bridge; normal mode has no access.
- **The shell, always on inside a code repository.** When the project map
  resolves to a repository (a VCS directory or a known manifest at its root),
  `run_shell_command` is offered on every turn instead of only when the request
  happens to sound like a shell request. Coding tasks such as "skip these tests
  on PRs" previously ran without a shell and did every grep through `run_python`.
  It follows the *same* root the map already uses, so the sidecar needs
  `GAIA_PROJECT_ROOT=/path/to/repo` (or `GaiaAgentConfig(project_root=...)` when
  embedding) to see your repository — its own working directory is whatever
  started it, not yours. With no repository nothing changes, and the shell's
  approval gate still applies either way. See SKILL §11.
- **`sleep`, always on.** The agent can now wait before retrying, e.g. until a
  rate limit resets, instead of giving up; before, its only way to wait was
  `time.sleep` inside a confirmation-gated `run_python`. Up to five minutes per
  call, no approval needed, and Stop ends the wait within a second. It joins the
  always-on tool set (about 190 more prompt tokens per call) and the
  `loop_control` bundle (80 tools → 81).
- **`--bypass-permissions` now lifts the shell guardrails too.** It used to skip
  only the confirmation prompt, which left the agent unable to run a build or a
  test suite even with the user's blanket consent: no interpreter, test runner
  or package manager was reachable, and nothing could write its output anywhere.
  Under bypass, redirection (`>`, `>>`, `<`), backgrounding (`&`), substitution
  (`` ` ``, `$()`) and the newline now parse and run — chaining with `&&` / `||`
  / `;` / `|` already worked by default — the
  read-only allowlist is replaced by a developer set (`node`, `npm`, `make`,
  `cmake`, `go`, `cargo`, `sed`, `awk`, `curl`, `sleep`, `timeout`, `export`,
  `cp`, `mv`, plus `python` / `python3` / `pytest` / `gh` / `git`), and the
  shell rate limit is dropped. Off by default and byte-identical to before when
  off. `git` in that set means its policy's outright refusals — push, reset,
  rebase — also stop applying under bypass. `rm` stays excluded. Every command run this way is audit-logged with its full
  arguments. Stdio only — an HTTP session's `/bypass` stops its approval prompts
  but never lifts the shell gates, and the request body cannot ask for it.
  Redirection has one exception: a command that
  is nothing but a skill-granted CLI runs argv-only, so `>` there is refused
  with an explanation instead of reaching the binary as text. See SPEC §5.5.

### Notes

- Tracks sidecar contract `apiVersion` **2.14**; a differing major raises
  `VersionMismatchError`.
- `GET /v1/gaia/memory` (contract 2.13) answers the same read-only snapshot the
  stdio transport's `/memory` sentinel produces, so a daemon-supervised
  install of the flagship exposes `/memory` too, not just a subprocess one.

## [0.2.0] — 2026-09-16

### Fixed

- Windows npm launchers now find the Python daemon CLI even when npm passes the
  package script as argv[1], preserving unrelated tools in shared PATH directories.

### Added

- **Image generation, reachable out of the box.** "Draw me a red bicycle" now
  generates a PNG with local Stable Diffusion and reports the path; previously
  the tools existed behind a flag nothing turned on, so the agent just said it
  couldn't. Adds `generate_image`, `list_sd_models`, and `get_generation_history`
  (70 tools → 73) plus an `image_gen` bundle so per-turn selection can find them.
  Generating swaps the resident model, so the next reply waits for the chat model
  to reload. The `image-gen` starter skill covers prompt expansion and iterating
  on the previous image.
- TUI provider setup for Local, Fireworks AI, and AMD LLM Gateway, with masked
  runtime API keys, discovered models, and remote-inference status.
- **A project map at task start.** In a code repository the agent now opens
  every task knowing the directory shape, the likely entry points, which
  commands are installed, and the three platform differences that change
  command syntax — instead of discovering each one through a failed tool call.
  Capped at 600 prompt tokens. If the repository has no code index the map
  starts one in the background; `GAIA_PROJECT_MAP_AUTO_INDEX=0` turns that off,
  and `GAIA_PROJECT_ROOT` picks the project when the working directory is not
  it. See SKILL §11.
- **Three further client-visible refusals from `/query`.** Reusing a `run_id`
  that is still in flight gets `409` — it used to replace the live run, leaving
  it with no way to be cancelled. Supplying a `model` that differs from the one
  the `session_id` was built with gets `409` rather than silently answering on
  the old model. A request with an absent or empty `Host` header gets `400`
  rather than being served, closing a DNS-rebinding check that failed open.
  See SPEC §5 and §5.2.
- **Per-turn tool selection, now on by default for the flagship `full`
  profile.** The model is sent about 28 of its 84 tools on any one call — a
  fixed core plus whichever cohesion bundles the query matched — instead of the
  whole registry every time. No capability is lost: `load_tools` is an escape
  hatch the model calls mid-turn to pull in a bundle the selector missed; that
  bundle is appended for the rest of the turn (briefly above the cap, which the
  next turn restores) so the prompt already sent stays cached.
  `GAIA_DYNAMIC_TOOLS=0` turns the selection off, `GAIA_DYNAMIC_TOOLS_MAX`
  moves the cap and `GAIA_DYNAMIC_TOOLS_TAU` the match threshold.
- **One bundled skill ships enabled: `gaia-voice`.** It is a manifest `skills:`
  entry, so it is always on and rendered in full on every LLM call — 702 tokens
  of every prompt, and it declares no tools. It is the agent's honesty floor
  (don't claim work you didn't do, don't present empty output as a result,
  don't substitute a near-miss and report success), which is why it is not in an
  opt-in bundle the way a task skill is. No *skill set* ships enabled: the
  manifest's `skill_sets:` / `default_skill_set:` blocks stay commented out until
  an eval measures what loading several bodies costs. See SKILL.md §10.
- **A materially smaller fixed prompt on every call.** That always-on
  `gaia-voice` guidance was rewritten from 2,129 tokens to 676 with all of its
  behavioural rules intact. It had been a rationale document — every rule
  followed by the incident that motivated it — and the model needs the rule,
  not the incident report.
- **The `.installed` record is written after a verified sidecar install.**
  Staging the binary was only half an install: the daemon and the terminal UI
  both key "this agent is installed" on `~/.gaia/agents/gaia/.installed`, and
  without it the UI ran the REST sidecar as its own stdio child and the chat
  filled with uvicorn's startup log. It is written on a cache hit as well as a
  fresh download, so an install left by an earlier version repairs itself, and
  only for this host's own platform — a `--platform` fetch stages a binary for a
  different machine and records nothing. See SPEC §4.1.
- **`--allow-insecure-base-url`** — opt-in for a non-`https` `--base-url`, for a
  trusted local mirror.

### Changed

- **A `LEMONADE_BASE_URL` that already carries a path is now used exactly as
  written.** Previously any URL not ending in `/api/v1` had that suffix appended,
  so a reverse proxy configured as `https://proxy.example/lemonade` was silently
  rewritten to `https://proxy.example/lemonade/api/v1`. It now resolves to
  `https://proxy.example/lemonade` unchanged, and only a bare origin with no path
  at all gains `/api/v1`. If a proxied install starts returning `404` after
  upgrading, append the API path to the variable yourself.

### Fixed

- **A second `gaia serve` no longer reports success against a server it does not
  own.** With the port already taken, the incumbent answered `/health` while our
  own sidecar was still unpacking, so the start "succeeded", printed a ready URL
  for someone else's server, and the child then died of `EADDRINUSE`. The port is
  now checked before anything is spawned, and a taken one fails naming the port
  and how to find the process holding it.
- **Importing the package no longer changes a host app's error handling.** The
  crash and signal handlers reaped every sidecar *before* checking whether the
  host had its own handler, so an exception the host handled killed the sidecar
  and the host's next request got an unexplained `ECONNREFUSED`.
- **`gaia run` no longer breaks the terminal UI's `PATH`.** Hiding our own `gaia`
  shim removed the whole bin directory, which on a Homebrew or pipx layout also
  took `python3`, `lemonade-server`, and the real `gaia` with it — so the UI
  reported a missing CLI that we had hidden. A shared directory now moves to the
  end of `PATH` instead of being dropped.
- **Flags a command does not read are refused, not ignored.** `run --port 9000`
  parsed fine and came up on the default port; likewise `serve --component` and
  `serve --cache-dir`.
- **A failed install says what to do.** A download that could not be moved into
  place — usually a running sidecar holding the file on Windows — printed a raw
  stack trace.
- **`taskkill` failures name their reason.** Its exit code and stderr were
  discarded, so "Access is denied" surfaced ten seconds later as a generic
  timeout.
- **A sidecar that survives shutdown is still reaped at exit.** `shutdown`
  de-registered it before killing it, so one that survived both kill windows
  became a permanent orphan holding the port.
- **`serve` removes its signal handlers once it stops**, so Ctrl+C keeps working
  afterwards, and a repeat Ctrl+C during a slow teardown now says what it is
  waiting for instead of looking frozen.
- **A non-JSON reply from a sidecar probe raises a typed error** rather than a
  bare `SyntaxError` — the case where a proxy answers `200` with an HTML page.
- **`--port` no longer accepts what `Number()` would coerce**, so `--port 0x2710`
  is rejected instead of quietly binding 10000.

### Security

- **A desktop notification can no longer run code.** On Windows, `notify_desktop`
  rendered its message box by pasting the title and body into a PowerShell command
  string, so a `'` in either — text the model picks, and prompt-injected content
  can steer it — closed the string literal and the remainder ran as PowerShell.
  The command is now a fixed script that reads both values from the child's
  environment, and the tool now needs your approval before it runs, like the
  other tools that spawn a process (SKILL.md §8).
- **`resolveSidecarPath` / `resolveTuiPath` verify the binary before returning a
  path that gets spawned.** Both fed `spawn()` from a predictable cache path with
  no integrity check, so anything able to write `~/.gaia/agents/gaia/` got code
  run — despite the package documenting the SHA verify as its security boundary.
  They now re-hash against `binaries.lock.json`; pass `{ verify: false }` for a
  binary you built yourself. Note this makes them proportional to the binary's
  size — resolve once at startup, not per request.
- **`--base-url` requires `https`** unless `--allow-insecure-base-url` is passed.
  The pinned SHA already made a plaintext mirror non-exploitable, but tampering
  surfaced as a confusing `IntegrityError` instead of the transport failure it is.
- **A ~200MB artifact is never held in memory whole.** Downloads stream to disk,
  and the cache-hit check that re-hashes an already-installed binary — which runs
  on every `gaia run` — reads it in bounded chunks. Both hashes are computed
  incrementally, and a download is still verified *before* the file is moved into
  place.

### Fixed

- **Esc stops a running turn in the terminal UI without killing the agent.**
  The TUI used to kill the agent process, and on the released one-file binary
  that killed only the launcher: the cancelled tool call ran to completion and
  the surviving process consumed the next message. The first Esc now sends the
  agent a `cancel` control message, so the turn ends and the session keeps its
  loaded skills, "always" grants, history and full access. A second Esc stops
  the whole process tree.
- **A restart after a hard stop no longer turns full access back on.**
  The replacement agent is launched in the session's current permission mode
  instead of from the original flags, and the TUI says what the restart lost.

### Notes

- `gaia_agent` enforces a per-session caller-auth bearer on every `/v1/gaia/*`
  request, plus a loopback `Host` allowlist and non-loopback `Origin` rejection.
  This package mints no token, so a sidecar it spawns comes up in dev mode (token
  check skipped, loudly warned, Host/Origin still enforced) — pass your own
  through `spawnSidecar`'s `env` to turn it on. See SPEC §5.4.

## [0.1.1] — 2026-08-21

First working release. `npx @amd-gaia/gaia` is now the single command that gets a
user running GAIA: it fetches and verifies everything GAIA needs and drops them
into the terminal UI. Before this there was no packaged path at all — the flagship
agent had to be run from a repo checkout with a Python environment, and reaching
the terminal UI meant building it from source.

### Added

- **`503` from `/query` at session capacity.** When every retained session
  slot is busy and none is idle enough to evict, starting a new session
  returns `503` with the reason in `detail` — retryable, distinct from a
  bug-shaped `500`. See SPEC §5.2.
- **Per-turn skill-body selection.** A loaded skill stays loaded, but its body
  only renders in the prompt on turns whose query matches its description; the
  rest collapse to a one-line menu the model re-activates with `load_skill`.
  `GAIA_DYNAMIC_SKILLS=0` turns the selection off, `GAIA_DYNAMIC_SKILLS_TAU`
  overrides the match threshold, and an embedder outage disables it for the
  session (every body renders — capability is never lost to a failed match).
- **`gaia run` (the default command)** — resolves the host platform, fetches and
  SHA-256 verifies both binaries, then launches the terminal UI and propagates its
  exit code. Arguments after a bare `--` are forwarded to the TUI verbatim.
- **Dual-binary delivery.** The package installs two published artifacts: the
  frozen agent sidecar (`gaia-agent`), published by this package's own release,
  and the terminal UI (`gaia-tui`), which is the published **`terminal-hub`**
  component. The TUI is consumed, not rebuilt — it is byte-for-byte the binary a
  full GAIA install runs as `gaia tui`, so an npm user and a core user cannot end
  up on terminal UIs that behave differently. A second build under this package's
  own lane would have been the same bytes at a different version under a third
  naming convention, and the two would have drifted.
- **`binaries.lock.json` `schemaVersion` 3.0** — a component-keyed checksum
  manifest where **each component carries its own `componentVersion`, `baseUrl`
  and `platforms`**. Component-first rather than the email agent's flat `binaries`
  map because the two differ in every dimension: hub lane, version, and platform
  coverage (terminal-hub covers arm64 Linux and arm64 Windows; the PyInstaller
  sidecar does not). A single shared base URL cannot address two lanes, so a
  `1.x`- or `2.x`-shaped lock is rejected at load with an error naming the schema.
- **Terminal-hub artifact naming is handled in data.** That lane names its Windows
  builds `gaia-win-x64.exe` / `gaia-win-arm64.exe`, while platform keys come from
  `process.platform` and say `win32`. The lock keeps the `win32-*` key and carries
  the hub's spelling in `filename`, so nothing branches on platform to construct a
  URL. The mapping is asserted on both sides (`TUI_ARTIFACT_NAMES` in
  `src/platform.ts` and in the lock generator) because a wrong name there is not a
  build failure anywhere — it is a 404 on a user's first run.
- **Mandatory SHA-256 verification.** Every download is hashed and compared
  against the lock before it is written. A mismatch deletes the download and
  raises `IntegrityError` naming expected vs actual. A placeholder hash blocks the
  fetch before any network call. There is no flag that relaxes either.
- **`gaia fetch`** — download and verify without launching; prints JSON. Supports
  `--component` and `--platform` for cross-platform staging in CI.
- **`gaia serve`** — run the agent sidecar alone on `127.0.0.1:8141` for
  integrators who want the REST surface without a daemon or a UI. Health-polls
  `GET /health`, checks the contract version, and tree-kills on exit. Port `4001`
  is refused.
- **`gaia version`** — prints, per component, its version, the URL it is fetched
  from, and its platform matrix.
- **Programmatic exports** — `fetchAll`, `startSidecar`, `shutdown`, `runTui`, the
  platform helpers, and the typed error classes, for embedding GAIA in another
  app.

### Notes

- The sidecar is installed into `~/.gaia/agents/gaia/`, the GAIA daemon's own
  cache directory. The daemon spawns and supervises the sidecar; putting an
  already-verified binary where it looks turns its fetch into a cache hit instead
  of a second large download. `run` therefore does not spawn a sidecar itself —
  the terminal UI reaches agents through the daemon relay and never holds a
  sidecar token, so a second process would only contend for the port. `serve` is
  the direct path for callers who do want to own it.
- The TUI is installed as `gaia-tui`, never as `gaia`, so it cannot shadow the
  `gaia` bin shim npm places on `PATH` — the terminal-hub artifact itself is named
  `gaia-<platform>`, which is why the lock separates `filename` from `executable`.
- Because the TUI comes from the `terminal-hub` lane, this package cannot be
  released until that component is published at the version the lock pins. The
  release fails loudly naming the required version; it never falls back to
  building its own TUI. Each terminal-hub artifact is additionally cross-checked
  against the hub's own server-side SHA-256 before its hash enters the lock.
- Requires Node.js 18+ (built-in `fetch`), a running Lemonade Server for
  inference, and the `gaia` Python CLI 0.24.1+ on `PATH` for the daemon the TUI
  starts. 0.24.1 is the first core whose daemon knows how to supervise this
  agent; on an earlier core the UI starts with nothing behind it.
- The sidecar has no arm64 Linux or arm64 Windows build. On those platforms the
  run stops with an error naming the platform and the supported set rather than
  launching a UI with no agent behind it.
