# Changelog

## Unreleased

- The next release must raise the `amd-gaia` floor to the first core release
  that ships `gaia.agents.tools.path_access`, which the agent now imports at start.
- Every file tool now checks the same allowed-folders rule as `read_file`:
  listing and searching folders (`list_files` included), indexing documents,
  watching folders, and the files `text_to_speech` and the export tools save. A folder you
  approve for the chat is now readable by document indexing too. Symlinks and
  `..` are resolved before the check, and secrets such as `.env` inside an allowed
  folder are no longer indexed.
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
