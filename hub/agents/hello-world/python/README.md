# gaia-agent-hello-world

The **smallest possible GAIA agent** — a conversational agent with a system
prompt and no tools. Use it as a copy-paste starting point for your own
conversational agent.

This is a *reference example* (`category: examples`,
`security_tier: experimental`). It is intentionally minimal.

## What it teaches

`gaia_agent_hello_world/agent.py` shows the four things every GAIA agent needs:

1. Subclass `gaia.agents.base.Agent`.
2. Set `response_mode = "conversational"` before `super().__init__()` so the
   agent replies in plain text (not the planning JSON envelope).
3. Implement `_get_system_prompt()` to give the agent its behavior.
4. Implement `_register_tools()` — empty here, because a prompt alone is useful.

`gaia_agent_hello_world/__init__.py` shows how the package advertises itself to
the GAIA registry through the `gaia.agent` entry point via `build_registration()`.

## Install

```bash
pip install -e hub/agents/hello-world/python   # editable, for development
```

Installing registers the `hello-world` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("hello-world")
```

## Develop / test

```bash
pip install -e "hub/agents/hello-world/python/[test]"
pytest hub/agents/hello-world/python/tests/ -x
```

The tests mock the LLM and need no running Lemonade server.

## Make your own

Copy this directory, rename `gaia_agent_hello_world` → `gaia_agent_<your_id>`,
update `pyproject.toml`, `gaia-agent.yaml`, and the class name, then edit
`_SYSTEM_PROMPT`. See [docs/guides/custom-agent.mdx](../../../../docs/guides/custom-agent.mdx).
