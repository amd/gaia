# Changelog

## Unreleased

- `/clear-cache` no longer deletes the whole working-directory `.gaia` folder
  (all of `~/.gaia` when started from home). The RAG cache now lives in
  `~/.gaia/cache/rag`, and clearing removes only the files the cache wrote.
- Removed the direct OpenAI and LiteLLM backend adapters from the shared framework.
  `--use-chatgpt` and `use_chatgpt=True` now fail with migration guidance before
  inference. Configure a local or on-prem model in Lemonade and use its server
  URL and catalog model ID. The OpenAI Python HTTP-client dependency is retained.
