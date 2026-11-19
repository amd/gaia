# gaia-agent-hello-world

The **smallest possible GAIA agent** — a conversational agent with a system
prompt and no tools. It is the SDK's one reference agent package: the layout
`gaia agent init` scaffolds, and the fixture `tests/unit/test_agent_template.py`
imports, lint-checks, and constructs on every unit-test run. It is not a
catalog agent and is never published to the Agent Hub.

## What it teaches

`gaia_agent_hello_world/agent.py` shows the four things every GAIA agent needs:

1. Subclass `gaia.agents.base.Agent`.
2. Set `response_mode = "conversational"` before `super().__init__()` so the
   agent replies in plain text (not the planning JSON envelope).
3. Implement `_get_system_prompt()` to give the agent its behavior.
4. Implement `_register_tools()` — empty here, because a prompt alone is useful.

`gaia_agent_hello_world/__init__.py` shows how the package advertises itself to
the GAIA registry through the `gaia.agent` entry point via `build_registration()`.

## Make your own

Don't copy this directory — scaffold a fresh package under your own id:

```bash
gaia agent init my-agent
cd my-agent
gaia agent test --lint
pip install -e .
```

See the [custom agent guide](https://amd-gaia.ai/docs/guides/custom-agent).

## Try this one

```bash
pip install -e tests/fixtures/agents/hello-world
gaia agent test --lint --path tests/fixtures/agents/hello-world
python -m pytest tests/unit/test_agent_template.py
```

Installing registers the `hello-world` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically. The tests mock the LLM and
need no running Lemonade server.
