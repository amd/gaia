# gaia-agent-word-count

A GAIA agent with **exactly one tool**. It shows the tool pattern end-to-end:
the LLM reads the request, decides to call `count_text`, the framework runs the
Python function, and the LLM turns the result into a natural-language answer.

This is a *reference example* (`category: examples`,
`security_tier: experimental`).

## What it teaches

`gaia_agent_word_count/agent.py` demonstrates:

1. **Registering a tool** with the `@tool` decorator inside `_register_tools()`.
2. **The docstring is the schema** — the framework parses the wrapper's
   docstring + signature to tell the model what the tool does.
3. **Keep logic in a pure function** (`count_text_stats`) so it's unit-testable
   without an LLM, with the `@tool` wrapper a thin adapter over it.
4. **`self._snapshot_tools()`** at the end of `_register_tools()` isolates this
   agent's tools from other agents in the same process.

## Install

```bash
pip install -e hub/agents/word-count/python   # editable, for development
```

Installing registers the `word-count` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("word-count")
```

## Develop / test

```bash
pip install -e "hub/agents/word-count/python/[test]"
pytest hub/agents/word-count/python/tests/ -x
```

The tests mock the LLM and need no running Lemonade server.

## Make your own

Copy this directory, rename the package and class, then replace `count_text`
with your own `@tool` function. See
[docs/sdk/core/tools.mdx](../../../../docs/sdk/core/tools.mdx) for the full
tool reference.
