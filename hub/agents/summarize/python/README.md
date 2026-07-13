# gaia-agent-summarize

Standalone GAIA agent for document and text summarization (PDFs, meeting
transcripts, email). Depends on the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-summarize          # from PyPI (once published)
pip install -e hub/agents/summarize/python  # editable, for development
```

Installing registers the `summarize` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```bash
gaia summarize --input-file report.pdf
```

Or programmatically:

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("summarize")
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/summarize/python/tests/ -x
```
