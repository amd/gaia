# gaia-agent-chat

Standalone GAIA agent — the conversational ChatAgent, shipped under three prompt
profiles: `chat` (general conversation), `doc` (document Q&A with RAG), and
`file` (file-system navigation/search). Depends on the published `amd-gaia`
framework wheel.

## Install

```bash
pip install gaia-agent-chat              # from PyPI (once published — see #2240)
pip install -e hub/agents/python/chat    # editable, for development
uv pip install "gaia-agent-chat @ git+https://github.com/amd/gaia.git#subdirectory=hub/agents/python/chat"  # works today without a repo checkout
```

Installing registers the `chat`, `doc`, and `file` agents via the `gaia.agent`
entry-point group; the GAIA registry discovers them automatically, so
`gaia chat` (including `gaia chat --ui`) resolves the agent through the registry.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/python/chat/tests/ -x
```
