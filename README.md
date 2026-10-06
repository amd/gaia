# <img src="https://raw.githubusercontent.com/amd/gaia/main/src/gaia/img/gaia.ico" alt="GAIA Logo" width="64" height="64" style="vertical-align: middle;"> GAIA: Local AI Agents for AMD Ryzen AI

[![GAIA CLI Tests](https://github.com/amd/gaia/actions/workflows/test_gaia_cli_linux.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our cli tests")
[![Latest Release](https://img.shields.io/github/v/release/amd/gaia?include_prereleases)](https://github.com/amd/gaia/releases/latest "Download the latest release")
[![PyPI](https://img.shields.io/pypi/v/amd-gaia)](https://pypi.org/project/amd-gaia/)
[![GitHub downloads](https://img.shields.io/github/downloads/amd/gaia/total.svg)](https://github.com/amd/gaia/releases)
[![OS - Windows](https://img.shields.io/badge/OS-Windows-blue)](https://amd-gaia.ai/docs/quickstart "Windows installation")
[![OS - Linux](https://img.shields.io/badge/OS-Linux-green)](https://amd-gaia.ai/docs/quickstart "Linux installation")
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-7289DA?logo=discord&logoColor=white)](https://discord.com/channels/1392562559122407535/1402013282495102997)

**GAIA** is AMD's open-source agent factory. It uses coding-agent skills, evals and an auto-fix loop to improve and build agent harnesses that run **locally by default** on AMD Ryzen AI hardware. Local inference keeps your data private, avoids cloud usage fees, and supports air-gapped deployment with hardware-accelerated performance.

Anyone can build an agent harness with GAIA. The **GAIA agent** is the one we built to show what it can do: conversation, documents, data, web, memory and skills in one agent.

| I want to... | Start here |
|--------------|-----------|
| **Use GAIA**: run the flagship agent in the desktop app or terminal | [Install GAIA](https://amd-gaia.ai/docs/guides/install), then the [GAIA agent guide](https://amd-gaia.ai/docs/guides/gaia) |
| **Build with GAIA**: make an agent harness with skills, evals and the SDK | [Composing skills](https://amd-gaia.ai/docs/guides/composing-skills), [Agent evals](https://amd-gaia.ai/docs/guides/eval), [SDK reference](https://amd-gaia.ai/docs/sdk) |

The terminal UI also supports optional **Fireworks AI** and **AMD LLM Gateway** chat through Lemonade. Use `/provider` to connect and choose a model; cloud chat sends conversation history to the selected provider. See [AI provider setup](docs/guides/ai-providers.mdx).

<p align="center">
  <a href="https://amd-gaia.ai/docs/quickstart"><strong>Get Started →</strong></a>
</p>

---

## Download

[![Download for Windows](https://img.shields.io/badge/Download-Windows-0078d4?style=for-the-badge&logo=windows)](https://amd-gaia.ai/#install)
[![Download for macOS](https://img.shields.io/badge/Download-macOS-000000?style=for-the-badge&logo=apple)](https://amd-gaia.ai/#install)
[![Download for Linux](https://img.shields.io/badge/Download-Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)](https://amd-gaia.ai/#install)

One download for users: on Windows, one installer offers the desktop app, the terminal, or both. See [Install GAIA](https://amd-gaia.ai/docs/guides/install) for every platform, and its [For developers](https://amd-gaia.ai/docs/guides/install#for-developers) section for script, `pip`/`uv` and source installs.

> **Note (Email agent):** the Email agent installs with `gaia hub install email`, which fetches a binary sidecar into your GAIA agents directory — no PyPI wheel and no Node.js toolchain required. An npm client is also published for embedding the agent in a JS/TS app.

---

## Why GAIA?

| Feature | Description |
|---------|-------------|
| **Local Inference** | Run models on your machine for sensitive workloads and air-gapped deployments |
| **No Cloud Inference Fees** | Local models require no API subscription; optional cloud providers have their own pricing |
| **Privacy-First** | HIPAA-compliant, GDPR-friendly—ideal for healthcare, finance, and enterprise |
| **Ryzen AI Optimized** | Hardware-accelerated inference using NPU + iGPU on AMD Ryzen AI processors |

---

## Build with GAIA

The SDK is the base layer under the GAIA agent. A minimal harness is a class with a prompt and tools:

```python
from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

class MyAgent(Agent):
    """A simple agent with custom tools."""

    def _get_system_prompt(self) -> str:
        return "You are a helpful assistant."

    def _register_tools(self):
        @tool
        def get_weather(city: str) -> dict:
            """Get weather for a city."""
            return {"city": city, "temperature": 72, "conditions": "Sunny"}

agent = MyAgent()
result = agent.process_query("What's the weather in Austin?")
print(result)
```

**[See the full quickstart guide →](https://amd-gaia.ai/docs/quickstart)**

---

## Key Capabilities

- **Agent Factory** — Skills, an eval benchmark, and `gaia eval agent --fix`, which patches failing scenarios and re-runs them ([guide](https://amd-gaia.ai/docs/guides/eval))
- **Agent SDK** — Base class with tool orchestration, state management, and error recovery
- **Agent UI** — Privacy-first desktop app with chat, file browser, document indexing, and tool execution
- **RAG System** — Document indexing and semantic search for Q&A over 50+ file formats
- **Voice Integration** — Whisper ASR + Kokoro TTS for speech interaction (P0 enabling technology)
- **Vision Models** — Extract text from images with Qwen3-VL-4B
- **MCP Integration** — Connect to any MCP server for external tool access
- **Skills** — Extend the GAIA agent with a `SKILL.md`, no code required ([guide](https://amd-gaia.ai/docs/guides/composing-skills))

---

## C++ Framework

A C++17 port of the GAIA base agent framework is available under [`cpp/`](cpp/README.md). It implements the same agent loop, tool registry, and MCP client interface without any Python dependency — suitable for embedding in native applications or resource-constrained environments.

```cpp
#include <gaia/agent.h>

class MyAgent : public gaia::Agent {
protected:
    std::string getSystemPrompt() const override {
        return "You are a helpful assistant.";
    }
};
```

**[C++ build and usage instructions →](cpp/README.md)**

---

## Quick Install

```bash
pip install amd-gaia
```

For complete setup instructions including Lemonade Server, see the **[Quickstart Guide](https://amd-gaia.ai/docs/quickstart)**.

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **Processor** | AMD Ryzen AI 300-series | AMD Ryzen AI Max+ 395 |
| **OS** | Windows 11, Linux | - |
| **RAM** | 16GB | 64GB |

---

## Documentation

- **[Quickstart](https://amd-gaia.ai/docs/quickstart)** — Install GAIA and build a first harness in 10 minutes
- **[SDK Reference](https://amd-gaia.ai/docs/sdk)** — Complete API documentation
- **[Guides](https://amd-gaia.ai/docs/guides)** — Chat, Voice, RAG, and more
- **[FAQ](https://amd-gaia.ai/docs/reference/faq)** — Frequently asked questions

---

## Releases

See the full [Release Notes](https://amd-gaia.ai/docs/releases) on the documentation site, or browse [GitHub Releases](https://github.com/amd/gaia/releases).

### Release Process

To publish a new release (e.g. `v0.17.0`), create a release PR that updates these 3 files:

| # | File | What to change |
|---|------|----------------|
| 1 | `src/gaia/version.py` | Set `__version__ = "0.17.0"` |
| 2 | `docs/releases/v0.17.0.mdx` | Create release notes (see [format guide](https://amd-gaia.ai/docs/releases)) |
| 3 | `docs/docs.json` | **(a)** Add `"releases/v0.17.0"` to the Releases tab pages array, **(b)** update the navbar label to `"v0.17.0 · Lemonade X.Y.Z"` |

Then merge and tag:

```bash
git tag v0.17.0 && git push origin v0.17.0
```

CI validates all three files are consistent with the tag before publishing to [GitHub Releases](https://github.com/amd/gaia/releases) and [PyPI](https://pypi.org/project/amd-gaia/).

---

## Contributing

We welcome contributions! See our [Contributing Guide](CONTRIBUTING.md) for details.

- **Build agents** in your own repository using GAIA as a dependency
- **Improve the framework** — check [GitHub Issues](https://github.com/amd/gaia/issues) for open tasks
- **Add documentation** — examples, tutorials, and guides

---

## Contact

- **Email**: [gaia@amd.com](mailto:gaia@amd.com)
- **Discord**: [Join our community](https://discord.com/channels/1392562559122407535/1402013282495102997)
- **Issues**: [GitHub Issues](https://github.com/amd/gaia/issues)

---

## License

[MIT License](./LICENSE.md)

Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
