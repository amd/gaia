# gaia-agent-chat

Standalone GAIA agent — the conversational ChatAgent, shipped under three prompt
profiles: `chat` (general conversation), `doc` (document Q&A with RAG), and
`file` (file-system navigation/search). Depends on the published `amd-gaia`
framework wheel.

## Install

```bash
pip install gaia-agent-chat              # from PyPI (once published — see #2240)
pip install -e hub/agents/chat/python    # editable, for development
uv pip install "gaia-agent-chat @ git+https://github.com/amd/gaia.git#subdirectory=hub/agents/chat/python"  # works today without a repo checkout
```

Installing registers the `chat`, `doc`, and `file` agents via the `gaia.agent`
entry-point group; the GAIA registry discovers them automatically, so
`gaia chat` (including `gaia chat --ui`) resolves the agent through the registry.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/chat/python/tests/ -x
```

## Provider migration

The direct OpenAI and LiteLLM adapters are removed. `--use-chatgpt` and
`use_chatgpt=True` remain only to report an actionable migration error before
startup. Configure your model in Lemonade, then select its catalog ID and the
Lemonade server URL. The local Lemonade and Claude routes are unchanged.
See [gateway migration](https://amd-gaia.ai/docs/sdk/sdks/llm#gateway-migration).
