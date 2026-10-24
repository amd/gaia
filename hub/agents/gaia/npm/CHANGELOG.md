# Changelog

All notable changes to `@amd-gaia/gaia` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- **An old failure in memory no longer stops a request you make now.** A note
  like "this page returned 503, don't waste tool calls on it" was obeyed as a
  standing ban, so the agent refused a URL you handed it. Memories now reach
  the model as dated notes your request outranks, and a passing failure is
  marked as one. A 429 or 502-504 "try later" failure is no longer saved as
  an error memory at all.
- **Text from a file is not saved as your details.** The agent could save a
  line from a document it read, or call it something you had saved. It now
  saves text from a file, page or tool result only when you ask it to keep
  something, and an empty memory search says that file text is not saved.

### For integrators: contract 2.20

- **A failed model request says which endpoint failed, worded for your
  front-end** (contract **2.20**, #5295). A headless caller of `/query` whose
  hosted model returned HTTP 502 was told the "cloud request through Lemonade
  failed" and to check "the TUI provider settings", though no TUI was
  involved. The error now names the provider and the `host:port` the request
  went through. A local model on a base URL you set is reported as "the model
  server at `host:port`" instead of "Lemonade on this machine", since GAIA
  can't tell what serves it. The advice follows the `surface` field: a caller
  that omits it is pointed at the model server the GAIA host uses; `"tui"`,
  `"agent_ui"` and `"cli"` keep their own settings screens. Older binaries
  refuse the field with `422`, so probe `/version` before sending it.
  Version skew: a front-end older than this release never sends `surface`, so
  against this agent it gets the headless advice (the model server's
  `LEMONADE_BASE_URL`) instead of its own settings screen. GAIA ships the TUI,
  the Agent UI and this agent together, so this only shows when one of them is
  updated without the others, or when the TUI's `/version` probe times out and
  it can't confirm the agent takes the field.
- **Answers stream over `/query`.** The HTTP sidecar ran its agent with
  streaming off, so a relayed turn (the Agent UI, and the TUI's later sessions)
  showed "Waiting on the model" until the whole answer landed at once, Stop could
  not cut a model call short, and a stopped turn kept no text. Every `/query`
  turn now streams its model calls and sends the answer as `token` events before
  `final`, the same as the stdio transport. A run cancelled mid-answer stops
  within a token and ends with an empty `final`, as stdio does; text not yet
  sent when the cancel lands is dropped.
### Fixed

- **Full access in the Agent UI asks before a gated shell command instead of
  refusing it.** With `/sessions/{id}/bypass` on, a command such as a delete or
  `git add` was refused outright, so a Full access chat could not run it at
  all. On a run someone can answer it now emits `needs_confirmation` and runs
  only on an allow from `/tool_decision`. The shell gates still stay on over
  HTTP, commands that need no approval still run unasked, and a run nobody can
  answer still refuses.
- **Local models with a large window can write longer replies.** The output
  cap is a quarter of the loaded window (16K at 64K, between 8K and 32K)
  instead of a flat 8K, and a reply cut off at the cap is told to write the
  file in smaller parts.
- **`search_file_content` searches the workspace.** A relative or default
  directory resolved against the sidecar's own folder and reported "0 files";
  it now resolves against the workspace, searches text files of any extension,
  and says when nothing was searched.
- **Token counts are exact in the published binary, offline too.** The binary
  could not load its tokenizer, so every turn trimmed history and sized
  context from an estimate that overcounts, and logged `Unknown encoding
  cl100k_base`. The tokenizer and its data now ship inside the binary, so it
  no longer downloads anything to count tokens.

### For integrators: contract 2.20

- **`/query` takes `surface`** (contract **2.20**): `"agent_ui"`, `"tui"` or
  `"cli"`. Asked to run something on a schedule from the Agent UI, the agent
  said it had no scheduler and sent the user to Windows Task Scheduler; with
  `"agent_ui"` it points them at the **Scheduled tasks** page instead. Older
  binaries `422` the field, so probe `/version` before sending it.
- **The agent knows which folders it can work in.** The system prompt now names
  the session's folders and labels GAIA's documents folder, so "summarize X in
  my documents folder" looks there first instead of trying `~/Documents` and
  the home folder.

### For integrators

- **A tool result keeps the tool's own status.** When a tool's result had no
  structured payload, the `tool_result` event's `data` carried only `summary`
  and `success`, so a client could not tell a skipped `remember` (a private
  chat saves nothing) from a saved one, and the Agent UI listed it as saved.
  `data` now also carries `status` when the tool returned one. Additive, so
  the contract version is unchanged.

- **An MCP tool call says which connector it used and what came back.** A Git
  connector call reached a client as a bare `mcp_git_git_log`, and its result
  as "Tool 'git_log' returned data". `tool_call` gains optional `mcp_server`,
  `mcp_server_label` ("Git") and `mcp_tool` ("git_log"), and the result's
  `data` carries the tool's text in `mcp_output` (`{ text, truncated }`, capped
  at 16,000 characters). Clients that ignore the fields see the same events.

### For integrators: contract 2.19

- **`/query` can record a turn's file changes for undo** (contract **2.19**).
  Desktop chats run in this agent and the Agent UI relays them, so a file the
  agent wrote never reached the chat's Changes view or its Undo. With
  `changes_turn_id` (and `session_id`), the agent copies each file before
  changing it into the snapshot store the Agent UI reads, and finishes the
  recording before the terminal event. Older binaries refuse the field with
  `422`, so probe `/version` before sending it.
- **A confirmation's `args` are only what the model sent.** `needs_confirmation`
  `args` also carried keys the agent adds to its own prompt — a risk note, a
  scratch-folder marker, and the folder a shell call runs in — so a policy
  client auto-approving on `args` matched fields the model never sent. `args`
  is now exactly the call's arguments; the folder and risk note moved to a new
  `prompt_context` object.
- **A tool result names the file it wrote.** When a tool's result carries
  `file_path` or `image_path`, the `tool_result` event's `data` now keeps it, so
  a client can list the files a turn produced.

## [0.3.1]

Released with GAIA 0.25.1, and requires it. Integrators: the sidecar
contract is now **2.18**; clients built for 2.17 keep working, and the new
`/query` fields are refused with `422` by older binaries, so probe
`/version` before sending them.

### For integrators: contract 2.18

- **`/query` takes the chat's file scope and memory mode** (contract **2.18**).
  `allowed_paths` lets the Agent UI hand the agent the same scope its session
  panel shows. `memory` fixes private chats saving to memory: the relayed turn
  never said the chat was private or that memory was off, so the agent went on
  saving the errors and lessons it records on its own. `"private"` or `"off"`
  now turns off every memory write for the turn.
- **A confirmation carries the call it asks about.** `needs_confirmation` now
  has `args` (each value capped at 64,000 characters, with `args_truncated`
  when one was cut) and `tool_label`, so a client can show all of the code it
  is approving rather than the shortened `summary`, and word a path prompt for
  a file or folder that exists.
- **A turn the model gave up on ends as failed, not done.** When the model
  stopped responding or refused mid-turn (a stall, a 404, every retry spent),
  the turn ended on the model's opening sentence and the Agent UI listed the
  chat as done. The final answer now names the step, the provider and the
  cause, what already ran, and what is unfinished, and the `final` event
  carries `failed: true`.
- **A turn queued for the model says so on the stream.** When another process
  or request is using the model, `/query` now sends `status` events with
  `phase: "model_queue"` every ~2 s, carrying a `queue` object with the turn's
  place in line, who holds the model and how long it has waited, then one
  `granted` update. A turn queued this way leases ahead of background work, and
  `/query/{run_id}/cancel` takes it out of the queue. Before, the stream went
  silent for the whole wait, so a client could not tell a queued turn from a
  dead one.
- **Needs core 0.25.1.** The chat agent this one builds on imports the
  reasoning-effort tool, which first ships in core 0.25.1, so its
  `amd-gaia` floor and `min_gaia_version` rise to 0.25.1 with that release.

### File access and privacy

- **Full access in the Agent UI grants the folders you name.** Turning it on
  over `/sessions/{id}/bypass` refused every path outside the chat's scope with
  nobody asked. On a run the caller can answer for, Full access now grants the
  path without a prompt, as it does in-process; Ask mode asks. Unattended runs,
  the home folder and drive roots are still refused.
- **No more approval prompts for files that aren't there.** Naming a missing
  file or folder outside the allowed paths opened a 10-minute approval prompt,
  and only after it did the agent learn the file didn't exist; a retry asked
  again. The tool now says the file wasn't found straight away, without asking,
  and names an allowed or attached file with the same name. `analyze_data_file`
  given a bare file name uses the attached file of that name instead of asking
  about one in the working folder. Secret and protected paths are refused
  exactly as before, whether or not they exist.
- **A turn's file changes can be undone.** The agent saves a copy of every
  file before a tool writes, edits, creates or deletes it, so the Agent UI
  can show the turn's changes and put every file back, even after a restart.
  A turn that also ran a shell command, Python or an MCP tool says undo may
  be incomplete.
- **Saving a download asks about the file, not its folder.** A download into
  the home folder failed without asking, because the agent asked to grant the
  whole folder. It now asks about the one file it is about to create.
- **A document another program has open is re-indexed once it is free.** On
  Windows, a watched file that Word, PowerPoint or another program held locked
  was read once, reported as corrupted, and never re-indexed. It is now retried
  a few times, and if it stays locked the log says to close the program and
  save the file again.

### Long and busy turns finish

- **A context overflow no longer throws away the evidence.** A task that read
  many documents used to fill a 32K–64K window, after which the overflow retry
  dropped every older result and the answer was built on whatever was left
  (#2780). The retry now keeps each result whole behind a handle the model can
  re-read, and retries only when the request will then fit.
- **Token counts no longer undercount when the tokenizer data can't load.** On
  an offline or locked-down machine the counter fell back to characters ÷ 4.28,
  which undercounts JSON and CJK text, so history that should take half the
  window could fill it (#5066). It now warns and counts at least one token per
  three UTF-8 bytes.
- **Opening a session or switching its model no longer freezes the other
  sessions.** The sidecar built a new session's agent, and ran a `/model`
  switch (which builds a new client and checks its server), on its event
  loop. Every other session's stream stopped for that long, heartbeats
  included, so the TUI and the daemon relay saw an idle connection. That
  setup now runs on a worker thread, and a request that goes away mid-setup
  still releases its session.
- **Background memory upkeep no longer wipes the chat's cached prompt.**
  Memory consolidation, reconciliation and skill distillation ran on the
  model slot the conversation uses, so the next message re-read the whole
  ~19K-token prompt (~45 s on Gemma-4-E4B). They now run on the side slot,
  as memory extraction already did.
- **A turn cut short by closing the TUI is saved as interrupted.** The session
  history got a turn only once it ended, so closing the TUI mid-turn saved it
  as an ordinary "stopped" answer, or, if the TUI had to kill the agent, lost
  the question entirely. The question is now saved when the turn starts, and a
  turn the TUI's closing cut short ends with "This answer was interrupted when
  GAIA closed." A resumed session (`GAIA_HISTORY_FILE`) sees it. Questions
  queued before the close are saved as they ended.
- **A busy, restarting or sleeping model server no longer fails the turn.**
  The agent waits for a shared Lemonade that is loading another app's model,
  waits up to 60 s for GAIA's own server to come back after a restart, and a
  PC that sleeps mid-answer resumes instead of reporting a stall. A shared
  KV-cache collision is no longer reported as "conversation too long".
- **Nothing a tool waits on can hang the turn for good.** A hung MCP server,
  a web server sending one byte at a time (page fetches stop at 120 s,
  downloads at 30 min) and a busy embedder during indexing (one 300 s wait
  per batch, with a message saying why) all end with a clear error. One bad
  batch of parallel tool calls no longer trips the failure breaker and ends
  the task with no answer.
- **The agent's own scripts get time to run.** A script run through an
  interpreter (`python analyze.py big.csv`) gets a 600 s default instead of
  the 30 s kill for quick commands.
- **Web search survives DuckDuckGo rate limits.** A refused search is retried
  with backoff and through DuckDuckGo's lite endpoint, or answered by Tavily
  when that connector is set up, instead of failing and being saved to memory
  as a known error.

### Memory you can trust

- **Memory keeps what the user says, and keeps it current.** Facts come only
  from what the user says about themselves, a forgotten fact is not re-learned
  from old chats, a newer fact outranks the one it replaced, and a write that
  carries tool-call markup or model reasoning is refused instead of saved
  corrupted. Relevant memories now surface in the chats they are about.

### Models and answers

- **gpt-oss 20B and 120B are supported models.** On a model that grades its
  reasoning, the agent gets `set_reasoning_effort` (low, medium or high) to
  choose how hard it thinks for the rest of the request; every new message
  starts again at medium. It is not registered on other models, so the
  agent's 95 tools on Gemma and Qwen are unchanged.
- **gpt-oss runs the agent.** Every array tool parameter now declares
  `items`, without which gpt-oss's chat template rejected every request
  with HTTP 500, and gpt-oss control markers inside a file or page the agent
  reads are broken up so the model no longer answers with nothing.
- **Answers are checked more honestly.** A claim such as "all 55 tests pass"
  is checked even when the same sentence says "but" or "not", and asking the
  agent to run a shell command runs it rather than reading the shell's state.

### Diagnostics

- **The turn log says how busy the model server was.** With `GAIA_TURN_LOG`
  set, each model call records the server's own prefill, decode and cache-reuse
  figures and the time spent waiting on the model-slot lease, the slot lock and
  the status probe; each turn records its server compute, its non-model time,
  and what filled the gaps between model calls. `GET /v1/gaia/debug/turns`
  returns the last records while recording is on (off the versioned contract).

## [0.3.0]

Released with GAIA 0.25.0, and requires it.

### Changed

- **Requires `amd-gaia>=0.25.0` and `gaia-agent-chat>=0.2.0`.** Every file tool
  now checks the allowed-folders rule, and that check ships in core 0.25.0; an
  older core cannot import the chat agent this one builds on.
- **`~/.gaia/logs/gaia-agent.log` stays bounded.** It rotates like `gaia.log`
  (10 MB, three backups; `GAIA_LOG_MAX_MB`, `GAIA_LOG_BACKUPS`) instead of
  growing without limit.

- **Everyday questions get short, plain answers.** "Why is the sky blue?" got
  140 words with an equation, and a Wi-Fi question got diagnostic shell runs.
  The voice skill now caps an everyday answer at three plain sentences, goes
  one level deeper only when asked, honours a stored short/detailed preference,
  and answers general-knowledge questions without running tools.
- **Memory off no longer loads the embedding model.** A private chat, or one
  with memory switched off, used to load the ~300 MB embedder on its first turn
  only to validate it and to score the always-on `gaia-voice` skill. It now
  loads when memory is first used, such as when memory is switched back on or
  the agent recalls something. A turn with only always-on skills loaded embeds
  nothing.
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
- **Permission prompts say what a command does, once.** Running the tests is
  labelled `RUNS CODE`, not `DESTRUCTIVE`; the command appears once, relative to
  the project; and `a` ("always this session") is offered for shell commands and
  Python too, covering the command's family — `pytest` covers
  `python -m pytest`, `cd proj && pytest 2>&1 | tail`. Deletes never offer it.
  `needs_confirmation` gains an optional `risk` field.
- **Shift+Tab permission modes in the terminal UI**: ask → accept edits → full
  access. Accept edits lets file edits inside the starting folder run unasked;
  the stdio control verb is `accept_edits`, and `--accept-edits` starts in it.
  Full access is still never turned on by a stray keypress.
- **An unanswered prompt is reported as a timeout.** `tool_decision` accepts
  `timeout`; the agent tells the user the request expired rather than that they
  refused it.
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

- **Answering the agent's question from the Agent UI or the TUI works.**
  `POST /query/{run_id}/respond` only took the answer as `response`, while
  both front-ends send `{ request_id, value }` as the contract specifies, so
  every answer was refused and the run waited until the question timed out.
  It now takes `value`. `response` is still accepted; sending both, or
  neither, is a 422.
- **A file you allow is usable by every tool, including document Q&A.** Indexing
  or querying a file outside the chat's scope was refused with no prompt, and
  approving it through another tool still left it unreadable to the document
  index. Indexing now asks the same question `read_file` does, and one approval
  covers both. A tool waiting on that question is no longer abandoned at its
  180 s limit while the question is still on screen. A refusal now tells the
  model not to copy or move the file elsewhere to get around it.
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
- A "no" now covers what an action does, not the one tool it was asked
  through. After you decline `pytest`, or leave its prompt unanswered, the
  agent no longer writes `run_tests.py` and runs it with another tool: any
  call that would run the tests, reach the same host, or change the same file
  is stopped for the rest of that request. Where the surface can ask, you get
  the exact command and why, with Allow once / Don't run it; otherwise the
  agent stops and asks in its reply.
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

### Also in 0.3.0

These were still listed under Unreleased when 0.3.0 was tagged, so they
shipped in it.

- **Finished work is no longer replaced by "I can't confirm this is done".** A
  redaction task that loaded the `document-extract` skill had its answer swapped
  for a coverage warning about its own `redaction_log.csv`, and a task with two
  named outputs only knew about the last one. Every file the request names, and
  every file the turn wrote, is now an output and never a source; the skill
  checks coverage only when the request names a source document or asks for
  every item. No check replaces a non-empty answer any more: an unproven
  save, test result or extraction keeps the answer and adds a short "I
  couldn't confirm all of this" list after it; a turn that ends early keeps
  what it said ahead of its report; and a tool that keeps failing ends with the
  model's answer from what it found, then the failure. A failed image request
  is no longer rewritten into advice to start GAIA with an `--sd` flag that
  does not exist. `load_skill` no longer tells the model the extraction tools
  cannot run when it is about to register them, and a rejected extractor reply
  is now logged.
- **`run_python` works in the Agent UI without a project.** It ran from the
  server's own folder, which is not an allowed path, so every approved snippet
  was refused and the agent fell back to slower shell commands. With no project
  and a working directory you did not allow, a snippet now runs in the agent's
  scratch directory, and its result says so. A project outside the allowed
  paths is still refused.
- **An "always" answer for a shell command no longer covers code in the
  scratch folder.** A command run there or naming a path there — `cd` into it
  then `python -m explore`, or `pytest`, `make`, `pip install`, `uv run` or
  `go run` pointed at it — and a script handed a scratch path as an argument
  now ask every time, with no `always_scope`, the same as a script there.
  `always_scope` labels for commands that run code they do not show now say so:
  `pytest (runs the test code it finds)`, `make (runs the Makefile's
  commands)`, `python -m explore (runs that Python module)`.
- **Exploring data no longer asks twice per step.** Each step wrote a helper
  script into the agent's own scratch folder and ran it, so a data task asked
  for approval about 30 times. Writes inside that folder now run unasked
  (writes anywhere else still ask). Running a script from it asks each time
  and offers no `always_scope`, since what is in that folder changes without
  a prompt.
- **A command no loaded skill grants asks you instead of failing.** `python
  script.py`, `git commit` or `pip install` with no skill granting them used to
  come back as "needs a skill grant … declares 'shell:execute:git' in its
  SKILL.md", which nobody could act on. They now raise the approval prompt; a
  skill grant still skips it for reads. A prompt's `summary` for a write or
  code-running command ends with one plain line on what it can do, and the
  `in <folder>` line also appears when an earlier `cd` moved the session. A
  call you decline tells the agent not to reach the same result another way,
  and a run nobody can answer refuses in one plain sentence.
- **Commands GAIA's checks don't cover ask once instead of failing.** `git
  push`, `git fetch`, `python -c`, PowerShell the checks can't read, a line with
  shell operators, or a path outside the allowed folders used to be refused
  outright. They now raise a prompt whose `summary` is the exact line the shell
  will run, its full folder and a risk sentence, with no `always_scope`; an
  approval runs that line once. Still refused with no prompt: an encoded
  PowerShell command or invisible characters, a line whose quoting can't be
  split the way the shell would, such a line that also deletes or discards work
  (`git push --force`, `git reset --hard`, `rm` outside the project), and
  folders no approval can open.
- **Long Python data jobs can finish.** `run_python` gave up after 60 s and
  the agent abandoned `execute_python_file` at 180 s even when it had asked
  for 15 minutes, while the script kept running and left a corrupt workbook
  behind. Python runs now get 120 s by default and up to 1800 s on request,
  the agent waits that long, and a run that runs out of time is killed with
  every process it started; the result names the files that changed while it
  ran so the agent checks them instead of reporting them finished.
- **Document search says when it could not search.** With the embedding
  model or Lemonade Server down, `query_documents` and `query_specific_file`
  answered "no relevant information found", so the agent told the user their
  documents held nothing on the topic. They now return an error naming the
  cause (Lemonade unreachable, or the embedding model failing) and how to fix
  it. `index_document` and `index_directory` report the same cause, and
  `index_directory` stops at the first such file and reports an error when no
  file could be indexed. Hints for a missing optional package (RAG, PDF, Excel,
  HTML parsing) say in a frozen build that it isn't bundled, and from source
  give the install command for the running Python.
- **A failed tool call is reported as a failure.** `run_shell_command` returned
  `status: success` for a command that exited non-zero, so the TUI and Agent UI
  showed a failed build or test run as a passed step. It is now `status: error`
  with an `error` naming the failing step, its exit code and the last stderr
  line; `stdout`, `stderr` and `return_code` are unchanged. grep's no-match and
  diff's "inputs differ" (exit 1) stay a success and say so in `expected_exit`.
  An MCP tool that answers `isError`, a DuckDuckGo search that was blocked, a
  `tasklist`/`wmctrl`/`osascript` window listing that failed, a notification
  whose PowerShell died, and an `open_url` with no browser to open are errors
  too. Install hints in a frozen build no longer name the binary as a Python to
  run pip with.
- **Reviewing a few documents reads them in full.** `read_file` on a Word,
  PDF or Excel file returned `[Binary file, N bytes]`, so to compare a term
  sheet against a draft agreement the agent indexed them and ran a handful of
  searches, and missed a closing date that differed between the two because no
  search asked for it. `read_file` now returns the extracted text, in parts sized
  to the model's budget that say where the next part starts, and the prompt
  tells the agent to read a few documents in full for a review, comparison or
  reconciliation and keep indexing for large collections and single lookups.
- **`fetch_page` no longer drops the middle of a long page.** Text past
  `max_length` was cut to its head and tail, which is where a product page keeps
  its menus and footer, not its price. It now returns the first part with a
  marker naming the offset of the next, takes `offset` to continue and `find` to
  jump to the passages containing a word, says so when a word is not on the
  page, and flags a page with almost no static text as rendered by JavaScript.
  `Length:` now reports the whole page rather than the excerpt.
- **Long desktop tasks finish with their deliverables instead of a bare "I
  can't confirm this is done".** Picking a controller from nine datasheets and
  pricing a 40-unit BOM ran out of its 50 steps while still searching for
  prices, and the reply dropped the recommendation the agent had already
  written. The agent's default step limit is now **150** (a `/query`
  `max_steps` or `GAIA_AGENT_MAX_STEPS` still wins); at 20, 10 and 5 steps left
  it is told to write the requested files from what it has, marking unverified
  values, and a turn that still runs out keeps its findings and lists what is
  missing after them.
- **The model notice is one line.** Switching or restoring a model printed a
  four-sentence paragraph that named the model twice and said "via Lemonade".
  It now reads like "Switched to **DeepSeek-V4.1-Flash** on the AMD LLM Gateway
  (cloud). Your messages, and anything read from files or memory, are sent
  there." The `/model` list drops "via Lemonade" from its headings and marks
  the configured default model with `(default)`.
- **Single-file search results name their file.** `query_specific_file` put the
  path only at the top of its result, so each chunk arrived with no source and
  the chunk cards showed none. Every chunk now carries `source_file`, and the
  result lists it in `source_files`, as `query_documents` already did.
- **The turn log counts the answer of a streamed turn.** With `GAIA_TURN_LOG`
  set, a turn whose answer streamed out recorded `answer_chars: 0`. It now
  records the length of the answer the user saw.
- **`run_python` and `execute_python_file` work in the released binary.** The
  binary has no Python interpreter, so it handed every script to its own
  argument parser, which rejected it, and no calculation, chart or `.xlsx`
  could be produced. It now runs scripts itself, with pypdf, pymupdf,
  python-docx, python-pptx, openpyxl, xlsxwriter, reportlab, pandas, numpy,
  matplotlib and the full standard library bundled for them, and both tools
  name those libraries to the model. A script that starts `sys.executable`
  (`-m`, `-c`, a file) runs too; `-m pip` says packages can't be installed
  and lists what is bundled. The Windows binary grows from 147 MB to 168 MB.
- **A Python script that fails is reported as a failure.** A non-zero exit
  came back as `status: success`, so the TUI showed a failed run as
  successful. It is now `status: error`, with an `error` naming the exit code
  and the last stderr line; `stdout`, `stderr` and `return_code` are unchanged.
- **`GAIA_ALLOWED_PATHS` confines a run to the folders you name.** The agent
  could read anything under the user's home and nothing narrowed that short of
  embedding it, so an evaluation run could read the graders' answer keys. Set
  to absolute folders, it narrows every file tool, search, index and shell path
  check to them — it never widens, an invalid value stops startup, and code the
  agent runs (`run_python`, shell) is not confined.
- **The sidecar's start-up warm-up now makes the first question fast.** It
  primed only the agent's own instructions, without the tool list or the rest
  of the system prompt, so the first question re-read all ~19K prompt tokens
  (34 s on Qwen3.6-35B-A3B). It now sends exactly what a turn sends, and the
  first question reads only its own ~50 tokens (5 s). A warm-up that fails is
  logged as a warning with its cause instead of an info line.
- **`/model` names the command that connects a cloud provider.** With no
  Fireworks or AMD gateway connection, the list told users to "connect this
  provider in the TUI provider settings", a screen with no obvious way in. It
  now says to run `/provider`, the command that opens it.
- **The TUI's provider picker works after GAIA's Lemonade restarts.** When the
  agent ran as the TUI's own child, its model-state ping kept naming the port
  Lemonade started on, so after a restart the picker said "Lemonade did not
  respond" while chat worked. With a local or cloud chat model the ping now
  reports the port the agent talks to, and it still never starts a stopped
  server. A Claude session still reports the port it started with.
- **The Agent UI's Skills settings list this agent's skills.** With the agent
  installed from the Hub, the page said the agent package was not installed
  and to run `gaia init`, which changed nothing: the bundled skills live
  inside the binary, where the UI could not read them. `GET /v1/gaia/skills`
  (contract **2.17**) now returns them.
- **A message sent right after Stop is answered instead of refused.** A stopped
  turn holds its session until its model call returns, so the next `/query`
  got `409 … is already running a turn` for as long as the stopped answer would
  have taken — about 40 s for a long one. It now waits for the stopped turn to
  finish (up to 240 s) and then runs. A turn that was not cancelled is still
  refused at once.
- **Documents attached to a chat now reach the agent.** In the Agent UI, a
  Hub-installed agent answered "no documents are indexed" about a file the user
  had attached, because the relayed turn never carried the chat's files.
  `/query` now takes a `documents` field (contract **2.16**): the agent indexes
  them before the turn, granting each exact file and not its folder, including
  a file attached partway through a conversation.
- **The first chat after the agent starts uses its own tools.** A session built
  while the start-up warm-up was still building could end up running the
  warm-up agent's tools, so a file it had just indexed read as access denied.
  Agents are now built one at a time.
- **Web requests work behind a corporate TLS-inspecting proxy.** HTTPS now
  trusts the operating system's certificate store, as pip does, instead of only
  Python's bundled list, so a proxy root such as Zscaler's no longer fails every
  fetch with `CERTIFICATE_VERIFY_FAILED`.
- **Starting the agent no longer downloads a model you skipped.** The HTTP
  sidecar's start-up warm-up skips the default local model when it is not
  downloaded, so someone using a cloud model is not handed a multi-GB download
  they never asked for.

### Notes

- Tracks sidecar contract `apiVersion` **2.17**; a differing major raises
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
  inference, and the `gaia` Python CLI 0.25.1+ on `PATH` for the daemon the TUI
  starts. 0.25.1 is the first core that can run the current release; on an
  earlier core the UI starts with nothing behind it.
- The sidecar has no arm64 Linux or arm64 Windows build. On those platforms the
  run stops with an error naming the platform and the supported set rather than
  launching a UI with no agent behind it.
