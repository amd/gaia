<!--
Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Examples

Small, runnable agents built on the GAIA SDK (`src/gaia`). Each file is one agent you
can read top to bottom and run with `python examples/<file>.py` once GAIA is installed
(`uv pip install -e ".[dev]"`) and a model server is running (`gaia init`).

They show the SDK, not the GAIA agent itself: the GAIA agent lives in
[`hub/agents/gaia/python/gaiapy/`](../hub/agents/gaia/python/gaiapy/). See
[The two harnesses](../docs/reference/harnesses.mdx).

| File | What it shows |
|---|---|
| `weather_agent.py` | The smallest agent: a system prompt and one `@tool` |
| `governed_weather_agent.py` | The same agent behind the governance layer's approval rules |
| `notes_agent.py` | An agent that keeps state in a local database |
| `file_watcher_agent.py` | Reacting to new files in a folder |
| `rag_doc_agent.py` | Document Q&A with the RAG tools |
| `hardware_advisor_agent.py` | Reading this PC's hardware to recommend a model |
| `product_mockup_agent.py` | Generating an HTML page from a description |
| `sd_agent_example.py` | Images with local Stable Diffusion, then a story about them |
| `mcp_time_server_agent.py` | Connecting to an MCP server configured inline |
| `mcp_config_based_agent.py` | Loading MCP servers from `mcp_servers.json` |
| `mcp_windows_system_health_agent.py` | Driving the Windows MCP server end to end (Windows only) |
| `skill_references_example.py` | Using a skill's reference files from Python |

`test_examples.yml` checks that every file here still parses and imports, and runs
several against a live model server on the Strix Halo runner
(`tests/integration/test_example_agents.py`).
