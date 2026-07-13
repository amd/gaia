# gaia-agent-connectors-demo

Standalone GAIA agent that demonstrates the connectors framework end-to-end —
it pulls real data from your connected Google account (Gmail, Calendar, Drive)
and GitHub PAT. Depends on the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-connectors-demo              # from PyPI (once published)
pip install -e hub/agents/connectors-demo/python    # editable, for development
```

Installing registers the `connectors-demo` agent via the `gaia.agent`
entry-point group; the GAIA registry discovers it automatically. Select it in
the Agent UI dropdown to validate your connector setup.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/connectors-demo/python/tests/ -x
```
