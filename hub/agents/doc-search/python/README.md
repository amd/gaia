# gaia-agent-doc-search

A RAG (retrieval-augmented generation) agent built by **composing a framework
tool mixin**. It inherits `RAGToolsMixin` to get a full set of document tools —
index, query, list, summarize — for free, then answers questions over the
user's documents.

This is a *reference example* (`category: examples`,
`security_tier: experimental`).

## What it teaches

`gaia_agent_doc_search/agent.py` demonstrates the most powerful pattern in
GAIA: **you rarely write retrieval code yourself — you compose a mixin.**

1. **Inherit the mixin:** `class DocSearchAgent(Agent, RAGToolsMixin):`.
2. **Give the mixin its dependency:** `RAGToolsMixin` reads `self.rag`, so build
   a `RAGSDK` in `__init__` before `super().__init__()`.
3. **Activate the tools:** call `self.register_rag_tools()` in
   `_register_tools()`.
4. **Prompt the model** on how to use index/query/cite.

The other framework mixins (file_io, shell, browser, scratchpad, …) follow the
identical shape — see `KNOWN_TOOLS` in `gaia.agents.registry`.

## Install

```bash
pip install -e hub/agents/doc-search/python   # editable, for development
```

Installing registers the `doc-search` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("doc-search")
```

## Develop / test

```bash
pip install -e "hub/agents/doc-search/python/[test]"
pytest hub/agents/doc-search/python/tests/ -x
```

The tests mock the LLM **and** the RAG SDK, so they need no running Lemonade
server and no embedding model.

## Make your own

Copy this directory and swap `RAGToolsMixin` for whichever framework mixin you
need (or add several). See [docs/sdk/sdks/rag.mdx](../../../../docs/sdk/sdks/rag.mdx)
for the RAG SDK and [docs/guides/custom-agent.mdx](../../../../docs/guides/custom-agent.mdx)
for the full agent guide.
