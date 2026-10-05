# Changelog

## Unreleased

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
