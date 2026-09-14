# <img src="https://raw.githubusercontent.com/amd/gaia/main/src/gaia/img/gaia.ico" alt="GAIA Logo" width="64" height="64" style="vertical-align: middle;"> GAIA: AI Agent Framework for AMD Ryzen AI

[![GAIA CLI Tests](https://github.com/amd/gaia/actions/workflows/test_gaia_cli_linux.yml/badge.svg)](https://github.com/amd/gaia/tree/main/tests "Check out our cli tests")
[![Latest Release](https://img.shields.io/github/v/release/amd/gaia?include_prereleases)](https://github.com/amd/gaia/releases/latest "Download the latest release")
[![PyPI](https://img.shields.io/pypi/v/amd-gaia)](https://pypi.org/project/amd-gaia/)
[![GitHub downloads](https://img.shields.io/github/downloads/amd/gaia/total.svg)](https://github.com/amd/gaia/releases)
[![OS - Windows](https://img.shields.io/badge/OS-Windows-blue)](https://amd-gaia.ai/docs/quickstart "Windows installation")
[![OS - Linux](https://img.shields.io/badge/OS-Linux-green)](https://amd-gaia.ai/docs/quickstart "Linux installation")
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-7289DA?logo=discord&logoColor=white)](https://discord.com/channels/1392562559122407535/1402013282495102997)

**GAIA** is AMD's open-source framework for building intelligent AI agents that run **locally by default** on AMD Ryzen AI hardware. Local inference keeps your data private, avoids cloud usage fees, and supports air-gapped deployment with hardware-accelerated performance.

The terminal UI also supports optional **Fireworks AI** and **AMD LLM Gateway** chat through Lemonade. Use `/provider` to connect and choose a model; cloud chat sends conversation history to the selected provider. See [AI provider setup](docs/guides/ai-providers.mdx).

<p align="center">
  <a href="https://amd-gaia.ai/docs/quickstart"><strong>Get Started →</strong></a>
</p>

---

## Download

[![Download for Windows](https://img.shields.io/badge/Download-Windows-0078d4?style=for-the-badge&logo=windows)](https://github.com/amd/gaia/releases/latest)
[![Download for macOS](https://img.shields.io/badge/Download-macOS-000000?style=for-the-badge&logo=apple)](https://github.com/amd/gaia/releases/latest)
[![Download for Linux](https://img.shields.io/badge/Download-Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)](https://github.com/amd/gaia/releases/latest)

Start with the [Terminal UI guide](docs/guides/terminal-hub.mdx), then run `gaia-tui`.
For the browser/Electron application, see [Agent UI](docs/guides/agent-ui.mdx).

> **Note (Email agent):** the Email agent installs with `gaia hub install email`, which fetches a binary sidecar into your GAIA agents directory — no PyPI wheel and no Node.js toolchain required. An npm client is also published for embedding the agent in a JS/TS app.

---

## Why GAIA?

| Feature | Description |
|---------|-------------|
| **Local Inference** | Run models on your machine for sensitive workloads and air-gapped deployments |
| **No Cloud Inference Fees** | Local models require no API subscription; optional cloud providers have their own pricing |
| **Privacy-First** | Local inference with explicit cloud-provider selection and tool permissions |
| **Ryzen AI Optimized** | Hardware-accelerated inference using NPU + iGPU on AMD Ryzen AI processors |

---

## Build on the harness

The [quickstart](docs/quickstart.mdx) runs a small agent with a word-count tool.
Use the [agent harness reference](docs/sdk/core/agent-system.mdx) for lifecycle,
context, result handling, and extension points, and the
[tool reference](docs/sdk/core/tools.mdx) for registration and permissions.


## Key Capabilities

- **Agent Framework** — Base class with tool orchestration, state management, and error recovery
- **Agent UI** — Privacy-first desktop app with chat, file browser, document indexing, and tool execution
- **RAG System** — Document indexing and semantic search for Q&A over 50+ file formats
- **Voice Integration** — Whisper ASR + Kokoro TTS for speech interaction
- **Vision Models** — Extract text from images with Qwen3-VL-4B
- **MCP Integration** — Connect to any MCP server for external tool access
- **Plugin System** — Distribute agents via PyPI with auto-discovery

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

Requirements depend on the agent, model, and backend. See the
[flagship guide](docs/guides/gaia.mdx) and
[hardware advisor](docs/guides/hardware-advisor.mdx) for setup and device selection.


## Documentation

- **[Quickstart](https://amd-gaia.ai/docs/quickstart)** — Build your first agent in 10 minutes
- **[SDK Reference](https://amd-gaia.ai/docs/sdk)** — Complete API documentation
- **[Guides](https://amd-gaia.ai/docs/guides)** — Chat, Voice, RAG, and more
- **[FAQ](https://amd-gaia.ai/docs/reference/faq)** — Frequently asked questions

---

## Releases

See the full [Release Notes](https://amd-gaia.ai/docs/releases) on the documentation site, or browse [GitHub Releases](https://github.com/amd/gaia/releases).


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
