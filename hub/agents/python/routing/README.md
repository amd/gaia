# gaia-agent-routing

Standalone GAIA agent — the **routing meta-agent**. `RoutingAgent` analyzes a
user request, disambiguates language/project-type, and routes the work to the
right concrete agent (currently `CodeAgent`). Depends on the published
`amd-gaia` framework wheel.

This is **infrastructure**, not a user-selectable agent: it does not inherit the
base `Agent` and is loaded by class path from the OpenAI-compatible API server
(`gaia.api.agent_registry`, model `gaia-code`). It therefore ships **without** a
`gaia.agent` entry point — installing the wheel just makes `gaia_agent_routing`
importable so the API server can resolve it.

## Install

```bash
pip install gaia-agent-routing              # from PyPI (once published — see #2240)
pip install -e hub/agents/python/routing    # editable, for development
uv pip install "gaia-agent-routing @ git+https://github.com/amd/gaia.git#subdirectory=hub/agents/python/routing"  # works today without a repo checkout
```

The API server's `gaia-code` model routes through `RoutingAgent`, which in turn
needs the `gaia-agent-code` wheel installed. Install both the same way (swap
`routing` for `code` in the subdirectory) to use `gaia api` for code
generation.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/python/routing/tests/ -x
```
