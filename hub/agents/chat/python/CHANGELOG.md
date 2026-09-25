# Changelog

## Unreleased

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
