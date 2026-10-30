# gaia-agent-chat (deprecated)

`ChatAgent` now ships inside the flagship package, `gaia-agent-gaia`
([#6099](https://github.com/amd/gaia/issues/6099)). This package is a re-export
shim kept for one release so existing imports keep working, and it is deleted
in the release after ([#6103](https://github.com/amd/gaia/issues/6103)).
Importing it emits a `DeprecationWarning`.

## Migrate

| Old import | New import |
|------------|------------|
| `gaia_agent_chat.agent` | `gaia_agent.chat_agent` |
| `gaia_agent_chat.app.interactive_mode` | `gaia_agent.interactive.interactive_mode` |
| `gaia_agent_chat.profiles`, `.tool_bundles`, `.session`, `.python_jobs`, `.python_runner`, `.tool_requests` | `gaia_agent.<same name>` |
| `gaia_agent_chat.session.validate_session_id` | `gaia.sidecar.session_ids.validate_session_id` |

```python
from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig
```

Each old submodule is the new module object itself, so `isinstance` checks and
`unittest.mock.patch("gaia_agent_chat.agent.X")` behave exactly as before.

Install the flagship package instead of this one. It is not on PyPI yet
([#2240](https://github.com/amd/gaia/issues/2240)); from a checkout:

```bash
pip install -e hub/agents/gaia/python
```

## Agent ids

This package no longer registers the `chat`, `doc` and `file` agent ids. The
registry resolves them, and their `*-lite` aliases, to the flagship `gaia`
agent, so a stored Agent UI session that names one of them reopens on the
flagship.

`ChatAgentLite` (`gaia_agent_chat.lite_agent`) still lives here and goes away
with this package.

## Provider migration

The direct OpenAI and LiteLLM adapters are removed. `--use-chatgpt` and
`use_chatgpt=True` remain only to report an actionable migration error before
startup. Configure your model in Lemonade, then select its catalog ID and the
Lemonade server URL. The local Lemonade and Claude routes are unchanged.
See [gateway migration](https://amd-gaia.ai/docs/sdk/sdks/llm#gateway-migration).
