# gaia-agent-jira

Standalone GAIA agent for Jira issue management. Depends on the published
`amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-jira              # from PyPI (once published)
pip install -e hub/agents/jira/python    # editable, for development
```

Installing registers the `jira` agent via the `gaia.agent` entry-point group;
the GAIA registry discovers it automatically.

## Use

```bash
gaia jira "show my open issues"
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/jira/python/tests/ -x
```
