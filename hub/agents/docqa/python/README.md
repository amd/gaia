# gaia-agent-docqa

Standalone GAIA agent — RAG-focused document Q&A and indexing. Depends on the published
`amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-docqa              # from PyPI (once published)
pip install -e hub/agents/docqa/python    # editable, for development
```

Installing registers the `docqa` agent via the `gaia.agent` entry-point group;
the GAIA registry discovers it automatically. It is a building-block agent,
hidden from the UI selector by default.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/docqa/python/tests/ -x
```
