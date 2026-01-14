# <img src="https://raw.githubusercontent.com/amd/gaia/main/src/gaia/img/gaia.ico" alt="GAIA Logo" width="64" height="64" style="vertical-align: middle;"> GAIA: AI Agent Framework for AMD Ryzen AI

[![GAIA Build Installer](https://github.com/amd/gaia/actions/workflows/build_installer.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our build")
[![GAIA CLI Tests](https://github.com/amd/gaia/actions/workflows/test_gaia_cli.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our cli tests")
[![Latest Release](https://img.shields.io/github/v/release/amd/gaia?include_prereleases)](https://github.com/amd/gaia/releases/latest "Download the latest release")
[![PyPI](https://img.shields.io/pypi/v/amd-gaia)](https://pypi.org/project/amd-gaia/)
[![GitHub downloads](https://img.shields.io/github/downloads/amd/gaia/total.svg)](https://github.com/amd/gaia/releases)
[![OS - Windows](https://img.shields.io/badge/OS-Windows-blue)](https://amd-gaia.ai/quickstart "Windows installation")
[![OS - Linux](https://img.shields.io/badge/OS-Linux-green)](https://amd-gaia.ai/quickstart "Linux installation")
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-7289DA?logo=discord&logoColor=white)](https://discord.com/channels/1392562559122407535/1402013282495102997)

**GAIA** is AMD's open-source framework for building intelligent AI agents that run **100% locally** on AMD Ryzen AI hardware. Keep your data private, eliminate cloud costs, and deploy in air-gapped environments—all with hardware-accelerated performance.

<p align="center">
  <a href="https://amd-gaia.ai/quickstart"><strong>Get Started →</strong></a>
</p>

---

## Why GAIA?

| Feature | Description |
|---------|-------------|
| **100% Local** | All data stays on your machine—perfect for sensitive workloads and air-gapped deployments |
| **Zero Cloud Costs** | No API fees, no usage limits, no subscriptions—unlimited AI at no extra cost |
| **Privacy-First** | HIPAA-compliant, GDPR-friendly—ideal for healthcare, finance, and enterprise |
| **Ryzen AI Optimized** | Hardware-accelerated inference using NPU + iGPU on AMD Ryzen AI processors |

---

## Build Your First Agent

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

**[See the full quickstart guide →](https://amd-gaia.ai/quickstart)**

---

## Key Capabilities

- **Agent Framework** — Base class with tool orchestration, state management, and error recovery
- **RAG System** — Document indexing and semantic search for Q&A
- **Voice Integration** — Whisper ASR + Kokoro TTS for speech interaction
- **Vision Models** — Extract text from images with Qwen2.5-VL
- **Plugin System** — Distribute agents via PyPI with auto-discovery
- **Web UI Packaging** — Generate modern interfaces for your agents

---

## Quick Install

```bash
pip install amd-gaia
```

For complete setup instructions including Lemonade Server, see the **[Quickstart Guide](https://amd-gaia.ai/quickstart)**.

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **Processor** | AMD Ryzen AI 300-series | AMD Ryzen AI Max+ 395 |
| **OS** | Windows 11, Linux | - |
| **RAM** | 16GB | 64GB |

---

## Documentation

- **[Quickstart](https://amd-gaia.ai/quickstart)** — Build your first agent in 10 minutes
- **[SDK Reference](https://amd-gaia.ai/sdk)** — Complete API documentation
- **[Guides](https://amd-gaia.ai/guides/chat)** — Chat, Voice, RAG, and more
- **[FAQ](https://amd-gaia.ai/reference/faq)** — Frequently asked questions

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

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
