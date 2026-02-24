# GAIA C++ Agent Framework

<!-- Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved. -->
<!-- SPDX-License-Identifier: MIT -->

A C++ port of the [GAIA](https://github.com/amd/gaia) Python base agent framework. Implements the core agentic loop — LLM reasoning, tool execution, multi-step planning, and MCP (Model Context Protocol) client integration — as a lightweight, header-friendly C++17 library.

The included demo (`simple_agent`) runs a **Windows System Health Agent** that connects to the [Windows MCP server](https://github.com/microsoft/windows-mcp), gathers memory/disk/CPU metrics via PowerShell, and pastes a formatted report into Notepad — demonstrating the full computer-use (CUA) flow over the MCP client-server interface.

---

## Prerequisites

### 1. Build Tools

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| CMake | 3.14 | `cmake --version` |
| C++ Compiler | C++17 | MSVC 2019+, GCC 9+, or Clang 10+ |
| Git | any | Required by CMake FetchContent |

> **Windows**: Install [Visual Studio 2022](https://visualstudio.microsoft.com/) (Desktop C++ workload) or the standalone [Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022). CMake is bundled with Visual Studio, or install it separately from [cmake.org](https://cmake.org/download/).

### 2. LLM Server (Lemonade)

The agent connects to an OpenAI-compatible LLM server at `http://localhost:8000/api/v1` by default. The reference backend is [Lemonade Server](https://github.com/amd/lemonade), which runs models locally on AMD hardware.

Install and start Lemonade before running the agent:

```bash
pip install lemonade-server
lemonade-server start
```

Default model: `Qwen3-Coder-30B-A3B-Instruct-GGUF`

> Any OpenAI-compatible server (vLLM, llama.cpp server, Ollama, etc.) works — update `AgentConfig::baseUrl` and `AgentConfig::modelId` in `examples/simple_agent.cpp` to point to your endpoint.

### 3. Windows MCP Server (for the demo)

The `simple_agent` demo launches the Windows MCP server via `uvx`. Install `uv` first:

```bash
pip install uv
```

The first run will automatically download and cache `windows-mcp` via `uvx windows-mcp`.

---

## Building

All dependencies (nlohmann/json, cpp-httplib, Google Test) are fetched automatically by CMake at configure time — no manual installs required.

### Windows (Visual Studio / MSVC)

```bat
cd cpp
cmake -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

Binaries are placed in `build\Release\`:
- `simple_agent.exe` — demo agent
- `gaia_tests.exe` — unit test suite

### Windows (Ninja / faster builds)

```bat
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

### Linux / macOS

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Binaries are placed in `build/`:
- `simple_agent`
- `gaia_tests`

---

## Running the Demo

Make sure the Lemonade server is running, then launch the agent:

```bat
build\Release\simple_agent.exe
```

The agent will attempt to connect to the Windows MCP server on startup. Once connected, try one of these prompts:

```
You: Run a full system health analysis.
You: How much RAM and disk space do I have?
You: What LLM models can my system run?
```

The agent will:
1. Query memory, disk, and CPU via PowerShell (through `mcp_windows_Shell`)
2. Format a health report and copy it to the clipboard
3. Open Notepad (`Start-Process notepad`)
4. Paste the report with `ctrl+v` (through `mcp_windows_Shortcut`)

Type `quit`, `exit`, or `q` to stop.

> If the Windows MCP server fails to connect, verify that `uvx` is on your PATH and that `uvx windows-mcp` runs without errors in a separate terminal.

---

## Running Tests

```bat
cd build
ctest -C Release --output-on-failure
```

Or run the test binary directly for verbose output:

```bat
build\Release\gaia_tests.exe --gtest_color=yes
```

---

## Project Structure

```
gaia/                           # repo root
└── cpp/
    ├── CMakeLists.txt          # Build configuration (fetches all dependencies)
    ├── include/gaia/
    │   ├── agent.h             # Core Agent class (processQuery, MCP connect)
    │   ├── types.h             # AgentConfig, Message, ToolInfo, ParsedResponse
    │   ├── tool_registry.h     # Tool registration and execution
    │   ├── mcp_client.h        # MCP JSON-RPC client (stdio transport)
    │   ├── json_utils.h        # JSON extraction with multi-strategy fallback
    │   └── console.h           # TerminalConsole / SilentConsole output handlers
    ├── src/
    │   ├── agent.cpp           # Agent loop state machine
    │   ├── tool_registry.cpp
    │   ├── mcp_client.cpp      # Cross-platform subprocess + pipes (Win32 / POSIX)
    │   ├── json_utils.cpp
    │   └── console.cpp
    ├── examples/
    │   └── simple_agent.cpp    # Windows System Health Agent (CUA demo)
    └── tests/
        ├── test_agent.cpp
        ├── test_tool_registry.cpp
        ├── test_json_utils.cpp
        ├── test_mcp_client.cpp
        ├── test_console.cpp
        └── test_types.cpp
```

---

## Writing Your Own Agent

Subclass `gaia::Agent`, override `getSystemPrompt()` and optionally `registerTools()`, then call `init()` at the end of your constructor:

```cpp
#include <gaia/agent.h>

class MyAgent : public gaia::Agent {
public:
    MyAgent() : Agent(makeConfig()) {
        init();
    }

protected:
    std::string getSystemPrompt() const override {
        return "You are a helpful assistant. Use tools to answer questions.";
    }

    void registerTools() override {
        toolRegistry().registerTool(
            "get_time",
            "Return the current UTC time as a string.",
            [](const gaia::json&) -> gaia::json {
                return {{"time", "2026-02-24T00:00:00Z"}};
            },
            {} // no parameters
        );
    }

private:
    static gaia::AgentConfig makeConfig() {
        gaia::AgentConfig cfg;
        cfg.baseUrl = "http://localhost:8000/api/v1";
        cfg.modelId = "Qwen3-Coder-30B-A3B-Instruct-GGUF";
        cfg.maxSteps = 20;
        return cfg;
    }
};

int main() {
    MyAgent agent;
    auto result = agent.processQuery("What time is it?");
    std::cout << result["result"].get<std::string>() << std::endl;
}
```

To connect an MCP server dynamically:

```cpp
agent.connectMcpServer("my_server", {
    {"command", "uvx"},
    {"args", {"my-mcp-package"}}
});
```

All tools exposed by the MCP server are automatically registered under the naming convention `mcp_<server_name>_<tool_name>`.

---

## Dependencies

All fetched automatically by CMake — no manual installation needed.

| Library | Version | License | Purpose |
|---------|---------|---------|---------|
| [nlohmann/json](https://github.com/nlohmann/json) | 3.11.3 | MIT | JSON parsing |
| [cpp-httplib](https://github.com/yhirose/cpp-httplib) | 0.15.3 | MIT | HTTP client (LLM API calls) |
| [Google Test](https://github.com/google/googletest) | 1.14.0 | BSD-3 | Unit testing |

---

## Relationship to Python GAIA

This C++ library is a port of `src/gaia/agents/base/` from the [GAIA Python package](https://github.com/amd/gaia), and lives alongside it in the same repository under `cpp/`. It targets the same agent loop semantics and MCP integration, without pulling in Python-only features (audio, RAG, Stable Diffusion, REST API server, etc.).

| Feature | Python GAIA | C++ port |
|---------|------------|----------|
| Agent loop (plan → tool → answer) | ✓ | ✓ |
| Tool registration | ✓ | ✓ |
| MCP client (stdio) | ✓ | ✓ |
| JSON parsing with fallbacks | ✓ | ✓ |
| OpenAI-compatible LLM backend | ✓ | ✓ |
| Multiple LLM providers (Claude, OpenAI) | ✓ | planned |
| Specialized agents (Code, Docker, Jira…) | ✓ | not ported |
| REST API server | ✓ | not ported |
| Audio / RAG / Stable Diffusion | ✓ | not ported |

---

## License

MIT License — see [LICENSE.md](LICENSE.md).
Copyright (C) 2025-2026 Advanced Micro Devices, Inc.
