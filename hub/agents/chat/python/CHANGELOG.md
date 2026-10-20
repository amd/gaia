# Changelog

## Unreleased

- A `run_python` snippet or `execute_python_file` script whose code only reads
  files no longer marks the turn's undo as incomplete, unless files changed in
  the folders it ran in. A snippet is recorded by its docstring or first
  comment line instead of its first line of code. Uses `may_write` on
  `note_untracked`, new in core 0.25.1.
- gpt-oss 20B and 120B get `set_reasoning_effort` (low, medium or high), so the
  agent chooses how hard the model reasons for the rest of a request; each new
  message starts again at the default. It registers per agent and only on a
  model that grades its reasoning, so other models' agents never see it, even
  in a server that ran gpt-oss earlier.
- Needs core 0.25.1, the first release that ships
  `gaia.agents.tools.reasoning_tools`, which the agent imports at start; on
  0.25.0 it fails at import. The `amd-gaia` floor rises to 0.25.1 in that
  release (a floor above the in-tree core cannot resolve), and
  `tests/test_chat_dependency_floor.py` fails the release until it does.

## 0.2.0

- A file copied or saved into a watched folder is reindexed once it has
  finished being written, instead of at its first change, so search no longer
  answers from a half-written copy. Reindexing runs off the file watcher's
  thread, and `stop_watching` now stops it too.
- When `generate_image` fails, the prompt asks for its error in a sentence or two
  instead of pointing at an `--sd` flag that does not exist.
- Requires `amd-gaia>=0.25.0`, the first core release that ships
  `gaia.agents.tools.path_access`, which the agent imports at start. With an
  older core the agent fails at import.
- A chat started while GAIA's Lemonade Server was stopped no longer stays on the
  stopped-state address `localhost:13305`. Without a configured `base_url` the
  agent takes the port the server actually starts on, so its first turn answers
  instead of failing with "Couldn't reach the local LLM".
- `run_python` with no project and a working directory outside the allowed
  paths (the Agent UI server's own folder) runs in the agent's scratch
  directory instead of being refused, and its result names that directory. A
  project outside the allowed paths is still refused.
- `run_python`, `execute_python_file` and the Windows notification fallback start
  their child process without GAIA's internal credentials, as core's shell and
  MCP children already do.
- `run_python` and `execute_python_file` run inside a frozen (PyInstaller)
  agent. There `sys.executable` is the agent binary, so they now re-launch it
  with `--gaia-run-python-file`, which its entry point must hand to
  `gaia_agent_chat.python_runner.run_python_file` before parsing argv.
- A Python run that exits non-zero now returns `status: "error"` with an
  `error` naming the exit code and the last stderr line, instead of
  `status: "success"`.
- `run_python` and `execute_python_file` default to a 120 s run (was 60 s) and
  accept `timeout` up to 1800 s; a larger value is refused, not clamped. The
  agent waits out the requested time instead of abandoning the call at its
  180 s default. A run that times out or is stopped kills its whole process
  tree (a Job Object on Windows, so orphaned grandchildren die too), and its
  result lists `files_modified` — the files that changed while it ran, with
  `files_modified_complete: false` when the bounded search stopped early — so
  a half-written file is checked rather than reported as finished. Nothing is
  deleted. A script that exits while a process it started still holds its
  output no longer waits out the timeout: those processes are stopped and the
  result carries a `note`.
  `gaia_agent_chat.python_runner.dispatch` before parsing argv. `dispatch`
  also runs the `python -m/-c/<file>` forms a script uses to start
  `sys.executable`, and refuses `-m pip` with the bundled-library list. Both
  tool descriptions name the bundled libraries.
- The agent reads its MCP server list from `$GAIA_HOME/mcp_servers.json`, not
  always `~/.gaia/mcp_servers.json`, so a run with its own `GAIA_HOME` no longer
  starts the user's MCP servers.
- A Python run that exits non-zero now returns `status: "error"` with an
  `error` naming the exit code and the last stderr line, instead of
  `status: "success"`.
- A script that prints a character outside the Windows console codec (`✓`,
  CJK, emoji) no longer crashes with `UnicodeEncodeError` when the agent runs
  from source: both tools ask the child for UTF-8 output, which is what they
  decode.
- Every file tool now checks the same allowed-folders rule as `read_file`:
  listing and searching folders (`list_files` included), indexing documents,
  watching folders, and the files `text_to_speech` and the export tools save. A folder you
  approve for the chat is now readable by document indexing too. Symlinks and
  `..` are resolved before the check, and secrets such as `.env` inside an allowed
  folder are no longer indexed.
- `ChatAgentConfig.memory_incognito` starts a session with memory off and does
  not load the embedding model until memory is used. The Agent UI sets it for
  private chats and when memory is switched off, which used to load the ~300 MB
  embedder on the first turn for nothing.
- Asking for the shell by name ("use your shell tool to run pwd") or for the
  working directory now always offers `run_shell_command`. With dynamic tool
  selection on, most tools matched such a request and the cap dropped the shell, so
  the agent answered the directory from a guess.
- An answer the model writes alongside its last tool call (typically the
  scratchpad `drop_table` cleanup) now reaches the user. The reply used to end on
  the short wrap-up that followed, such as "Scratch table cleaned up.", and the
  answer itself was dropped. The data-analysis workflow prompt now also places
  `drop_table` before the final answer.
- A GPU model now loads at its own context window on a machine whose
  `default_device` is `npu`. Every model there used to load at the NPU's 32,768
  tokens, so long tasks overflowed. `ChatAgentConfig.min_context_size` no longer
  defaults to 32,768: unset, the server is checked against the device profile and
  each model loads at its own window.
- `text_to_speech` now synthesizes through Lemonade's `kokoro-v1` model instead of
  a local Kokoro install, so it works without the PyTorch-based `kokoro` and
  `soundfile` packages. The `voice` argument is now honoured (it was ignored) and an
  unknown voice is rejected with the list of valid ones.
- A question the agent asks with `request_user_input` now waits as long as it says
  (up to 10 minutes) instead of being abandoned after 3 minutes as a "hung" tool;
  an answer given after that point used to be lost.
- File and document tools now expand a leading `~`, so `~/notes.txt` reads from the
  home directory instead of failing with "File not found" or creating a literal `~`
  folder.
- `/clear-cache` no longer deletes the whole working-directory `.gaia` folder
  (all of `~/.gaia` when started from home). The RAG cache now lives in
  `~/.gaia/cache/rag`, and clearing removes only the files the cache wrote.
- Chat sessions are now saved under `~/.gaia/sessions` (or `$GAIA_CONFIG_DIR/sessions`)
  instead of `./.gaia/sessions` in whatever directory `gaia chat` started from, so
  transcripts no longer land inside project repos and resume from any directory.
  Sessions saved by earlier versions stay where they were; move them into
  `~/.gaia/sessions` to keep resuming them. Saves are atomic, a truncated or invalid
  session file raises `SessionCorruptError` instead of being silently replaced by an
  empty session, and session ids must be 1–128 characters from `A-Z a-z 0-9 . _ -`.
- Removed the direct OpenAI and LiteLLM backend adapters from the shared framework.
  `--use-chatgpt` and `use_chatgpt=True` now fail with migration guidance before
  inference. Configure a local or on-prem model in Lemonade and use its server
  URL and catalog model ID. The OpenAI Python HTTP-client dependency is retained.
- `list_windows` reports a failed `tasklist`, `wmctrl` or `osascript` as an
  error naming its exit code, instead of an empty list or a generic message.
  `notify_desktop`'s Windows fallback waits briefly and reports a PowerShell
  that exited non-zero, and `open_url` reports when no browser could be opened.
- Missing-package hints (`psutil`, `pyperclip`, `plyer`) give a working install
  command, and in a frozen build say the package isn't bundled rather than
  suggesting `pip install`.
