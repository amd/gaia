# GAIA VSCode Integration Documentation

## Overview

GAIA can be integrated with Visual Studio Code in two ways:

1. **VSCode Extension (API Server)** - A VSCode Language Model Provider extension that exposes GAIA agents as selectable models
2. **MCP Client Integration** - Direct integration using the Model Context Protocol

---

## VSCode Extension (API Server)

### Overview

The GAIA VSCode extension integrates GAIA agents as selectable language models in Visual Studio Code, allowing you to use GAIA's autonomous agents alongside GitHub Copilot and other language model providers.

### Features

- **GAIA Code Agent**: Autonomous Python development with planning, code generation, linting, and testing
- **GAIA Jira Agent**: Natural language interface for Jira operations
- **Seamless Integration**: Works with VSCode's built-in model picker and Copilot Chat
- **Local Processing**: All inference runs locally on your AMD hardware via GAIA

### Prerequisites

1. **GAIA Framework**: Install GAIA with API support (see [API Server Prerequisites](./api.md#prerequisites))
2. **Lemonade Server**: Ensure the LLM backend is running with extended context:
   ```bash
   lemonade-server serve --ctx-size 32768
   ```
3. **Node.js v20.19.x**: Required for building the VSCode extension
   - **Windows**: Download and install from [nodejs.org](https://nodejs.org/en/download) (select Windows Installer for v20.x LTS)
   - **Linux**: Install using a package manager or nvm:
     ```bash
     # Using nvm (recommended)
     curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash
     source ~/.bashrc
     nvm install 20
     nvm use 20

     # Or using NodeSource repository (Debian/Ubuntu)
     curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
     sudo apt-get install -y nodejs
     ```
   - Verify installation: `node --version` (should show v20.x.x)
4. **GitHub Copilot Chat**: Required for the chat interface that integrates with GAIA
   - Install from the [VSCode Marketplace](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat)
   - This provides the chat UI that GAIA models integrate with via the Language Model Provider API

### Getting Started

**Important**: GAIA Code requires **three services** running simultaneously:
1. **Lemonade Server** (LLM backend on port 8000)
2. **GAIA API Server** (REST API on port 8080)
3. **VSCode Extension** (installed in VSCode)

Follow these steps in order:

#### 1. Start Lemonade Server (LLM Backend)

```bash
lemonade-server serve --ctx-size 32768
```

Keep this terminal running and open a new terminal for the next step.

#### 2. Start the GAIA API Server

```bash
gaia api start
```

The server will run on `http://localhost:8080` by default. Keep this terminal running.

#### 3. Build and Install the Extension

**Option A: Build and Install from VSIX**

First, build the extension:

```bash
cd src/vscode/gaia
npm install
npm run package
```

This creates `gaia-vscode-0.1.0.vsix` in the extension directory.

Then install it:

```bash
code --install-extension gaia-vscode-0.1.0.vsix
```
1. Open a new instance of VSCode
1. Open a directory (i.e., ~/src/test) that you wish to test GAIA Code against in new VSCode instance

**Option B: Development Mode**
1. Open the GAIA project root in VSCode
2. Run > Start Debugging > Select **"GAIA VSCode Extension (API)"** from the dropdown
3. This launches the Extension Development Host with the extension loaded

#### 4. Select a GAIA Model

1. Open athe VSCode Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
2. Select "Chat: Manage Language Models..." or [follow this guide](https://code.visualstudio.com/docs/copilot/customization/language-models#_customize-the-model-picker).
3. Click on the model picker
4. Select a GAIA model using Github Copilot Chat from the Secondary Side Bar: ⚠️ GAIA API server must be running to see this; see step above.
   - **gaia-code**: For GAIA Code tasks

#### 5. Use GAIA in Chat

Type your request in the GitHub Copilot chat and GAIA will process it using the selected agent.

### Configuration

Access settings via `File > Preferences > Settings` and search for "GAIA":

- **gaia.apiUrl**: GAIA API server URL (default: `http://localhost:8080`)
- **gaia.defaultModel**: Default GAIA model to use (default: `gaia-code`)

### Commands

The extension provides the following commands via the Command Palette:

- **GAIA: Manage API Server**: Shows information about GAIA, configuration, and links to documentation

### Architecture

```
VSCode Model Picker
    ↓
GAIA VSCode Extension (LanguageModelChatProvider)
    ↓
GAIA API Server (OpenAI-compatible REST API)
    ↓
GAIA Agents (Code, Jira, etc.)
    ↓
Lemonade Server (AMD-optimized LLM inference)
```

The extension implements VSCode's `LanguageModelChatProvider` API to register GAIA as a language model provider.

### Usage Examples

#### Code Development with GAIA Code Agent

1. Select **GAIA code** from the model picker
2. In chat, type: "Create a function to calculate prime numbers"
3. GAIA will:
   - Create the function
   - Generate tests
   - Run linting
   - Save files to your workspace
   - Report file paths in the response

#### Jira Operations with GAIA Jira Agent

1. Select **GAIA jira** from the model picker
2. In chat, type: "Show my open issues"
3. GAIA will:
   - Query your Jira instance
   - Parse natural language
   - Return formatted results

### Troubleshooting

#### "Failed to connect to GAIA API server"

**Symptoms**: Extension cannot reach the API server

**Solutions**:
- Ensure the API server is running: `gaia api start`
- Check the server URL in settings matches where the server is running
- Verify the server is healthy: `curl http://localhost:8080/health`

#### "No models available"

**Symptoms**: Model picker doesn't show GAIA models

**Solutions**:
- Ensure Lemonade Server is running: `lemonade-server serve --ctx-size 32768`
- The API server needs to be running before selecting models
- Check the GAIA output channel for error messages (View > Output > GAIA)
- Try restarting VSCode

#### Server Configuration Issues

**Symptoms**: Custom host/port not working

**Solutions**:
- Update the `gaia.apiUrl` setting to match your server configuration
- Restart the API server on the configured address
- Ensure firewall allows connections to the configured port

#### Agent-Specific Issues

**Code Agent not working**:
- Ensure Lemonade context size is 32768 or higher
- Check workspace has write permissions

**Jira Agent not working**:
- Ensure Jira is configured (see [Jira documentation](./jira.md))
- Verify Jira credentials are set up correctly

---

## MCP Client Integration

### Overview

VSCode can also integrate with GAIA through the Model Context Protocol (MCP) server. This is an alternative approach that uses MCP client capabilities rather than the Language Model Provider API.

**Note**: This is not a VSCode extension, but rather a client integration using MCP protocol.

### Prerequisites

1. **GAIA Framework**: Install GAIA
2. **Lemonade Server**: Running with appropriate context size
3. **GAIA MCP Server**: The MCP server must be running

### Getting Started

#### 1. Start Lemonade Server

```bash
lemonade-server serve --ctx-size 32768
```

#### 2. Start the GAIA MCP Server

```bash
gaia mcp start
```

The server will run on `http://localhost:8765` by default.

#### 3. Configure MCP Client

Configure your MCP client in VSCode to connect to `http://localhost:8765`.

#### 4. Test Connection

Test the MCP integration:

```bash
# List available tools
curl http://localhost:8765/tools

# Test a query
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"Hello GAIA"}'
```

### Architecture

```
VSCode MCP Client
    ↓
GAIA MCP Server (JSON-RPC + REST)
    ↓
GAIA Agents (Chat, LLM, Jira, Blender, Docker)
    ↓
Lemonade Server (AMD-optimized LLM inference)
```

### Available Tools

The MCP server exposes GAIA agents as tools:

| Tool | Description |
|------|-------------|
| `gaia.chat` | Conversational chat with context |
| `gaia.query` | Direct LLM queries (no context) |
| `gaia.jira` | Natural language Jira operations |
| `gaia.blender.create` | 3D content creation |

### Configuration

- **MCP Server URL**: `http://localhost:8765` (default)
- **Protocol**: JSON-RPC 2.0 + REST endpoints

For detailed MCP server documentation, see [MCP Server Documentation](./mcp.md).

### Troubleshooting

#### Connection Issues

**Symptoms**: Cannot connect to MCP server

**Solutions**:
- Verify MCP server is running: `gaia mcp status`
- Start server if needed: `gaia mcp start`
- Check server health: `curl http://localhost:8765/health`

#### Tool Not Available

**Symptoms**: Specific GAIA tool not showing up

**Solutions**:
- List available tools: `curl http://localhost:8765/tools`
- Check server logs for errors
- Ensure required agent dependencies are installed

---

## Extension Development

This section covers developing and testing the GAIA VSCode extension (API Server approach).

### Project Structure

```
src/vscode/gaia/
├── src/
│   ├── extension.ts          # Extension activation and commands
│   └── provider.ts            # LanguageModelChatProvider implementation
├── package.json               # Extension manifest and dependencies
├── tsconfig.json              # TypeScript configuration
└── .vscodeignore              # Files to exclude from package
```

### Setup Development Environment

**Note**: Node.js v20.19.x is required. See [Prerequisites](#prerequisites) for installation instructions.

#### 1. Install Dependencies

```bash
cd src/vscode/gaia
npm install
```

#### 2. Compile TypeScript

```bash
npm run compile
```

Or watch for changes:
```bash
npm run watch
```

### Testing the Extension

#### 1. Start Backend Services

Before testing, ensure the GAIA backend is running:

```bash
# Terminal 1: Start Lemonade Server
lemonade-server serve --ctx-size 32768

# Terminal 2: Start GAIA API Server
gaia api start
```

#### 2. Launch Extension Development Host

1. Open the GAIA project root in VSCode
2. Go to Run > Start Debugging (F5)
3. Select **"GAIA VSCode Extension (API)"** from the launch configuration dropdown
4. This opens a new VSCode window (Extension Development Host) with the extension loaded

**Note**: The launch configuration is defined in `.vscode/launch.json` and automatically compiles TypeScript before launching. There are two configurations:
- **GAIA VSCode Extension (API)** - For testing with the GAIA API server (port 8080)
- **GAIA VSCode Extension (MCP)** - For testing with the GAIA MCP server (port 8765)

#### 3. Test the Provider

In the Extension Development Host window:

1. Open Command Palette (`Ctrl+Shift+P`)
2. Run "GAIA: Manage API Server" to see the About dialog
3. Open any chat feature (e.g., Copilot Chat if available)
4. Click the model picker
5. Select a GAIA model (gaia-code or gaia-jira)
6. Send a test message

### Key Components

#### Extension Activation (`extension.ts`)

- Registers the GAIA LanguageModelChatProvider
- Implements management command for About dialog
- Creates output channel for logging

#### Provider Implementation (`provider.ts`)

Implements VSCode's `LanguageModelChatProvider` interface:

- **provideLanguageModelChatInformation**: Fetches available models from API server
- **provideLanguageModelChatResponse**: Handles chat requests with SSE streaming
- **provideTokenCount**: Estimates token count for input

#### Package Configuration (`package.json`)

- Declares `languageModelChatProviders` contribution
- Registers commands and settings
- Specifies activation events

### API Integration

The extension communicates with the GAIA API server (see [API documentation](./api.md)):

#### GET /v1/models
Returns list of available GAIA models with metadata.

#### POST /v1/chat/completions
Sends chat requests. Supports streaming via Server-Sent Events (SSE).

### Building for Distribution

#### 1. Install VSCE

```bash
npm install -g @vscode/vsce
```

#### 2. Package Extension

```bash
cd src/vscode/gaia
vsce package
```

This creates `gaia-vscode-0.1.0.vsix`.

#### 3. Install Locally

```bash
code --install-extension gaia-vscode-0.1.0.vsix
```

### Debugging

#### Enable Verbose Logging

The extension logs to the "GAIA" output channel. View it via:
- View > Output > Select "GAIA" from dropdown

#### Common Development Issues

1. **"Failed to connect to GAIA API server" during development**
   - Check API server is running: `curl http://localhost:8080/health`
   - Check `gaia.apiUrl` setting in Extension Development Host

2. **Models not appearing in picker**
   - Ensure API server returns models: `curl http://localhost:8080/v1/models`
   - Check VSCode output channel for errors
   - Restart Extension Development Host

3. **Streaming not working**
   - Verify API server supports SSE streaming
   - Check network tab in browser developer tools (if testing web)

4. **TypeScript compilation errors**
   - Run `npm install` to ensure dependencies are installed
   - Check `tsconfig.json` configuration
   - Verify VSCode TypeScript version matches project

5. **Extension not loading in debugger**
   - Ensure you selected the correct launch configuration: **"GAIA VSCode Extension (API)"**
   - Check that pre-launch task compiled successfully
   - Look for errors in Debug Console

### Architecture Details

#### Message Flow

```
User types in VSCode Chat
    ↓
VSCode calls provider.provideLanguageModelChatResponse()
    ↓
Provider converts messages to GAIA format
    ↓
HTTP POST to /v1/chat/completions (stream: true)
    ↓
Provider reads SSE stream
    ↓
Provider reports chunks via progress callback
    ↓
VSCode displays streamed response
```

#### Model Discovery

```
VSCode requests models
    ↓
VSCode calls provider.provideLanguageModelChatInformation()
    ↓
Provider fetches from /v1/models
    ↓
Provider converts to LanguageModelChatInformation format
    ↓
VSCode shows models in picker
```

### Testing Checklist

Before submitting changes:

- [ ] TypeScript compiles without errors: `npm run compile`
- [ ] Extension activates without errors
- [ ] Models appear in model picker
- [ ] Chat completions work (non-streaming)
- [ ] Streaming responses work
- [ ] About dialog displays correctly
- [ ] Settings are respected
- [ ] Error messages are clear and helpful
- [ ] Output channel shows useful logs

### Contributing

When making changes to the extension:

1. Update TypeScript code in `src/`
2. Run `npm run compile` to build
3. Test using the **"GAIA VSCode Extension (API)"** launch configuration
4. Update this documentation if adding features
5. Follow existing code style and patterns
6. Ensure copyright headers are present

---

## Comparison: Extension vs MCP Integration

| Feature | VSCode Extension (API) | MCP Integration |
|---------|----------------------|-----------------|
| **Integration Type** | VSCode Extension | MCP Client |
| **Protocol** | OpenAI-compatible REST + SSE | JSON-RPC + REST |
| **Port** | 8080 | 8765 |
| **Exposes** | Agents as "models" | Agents as "tools" |
| **Model Picker** | ✅ Yes | ❌ No |
| **Setup Complexity** | Install extension | Configure MCP client |
| **Launch Config** | "GAIA VSCode Extension (API)" | "GAIA VSCode Extension (MCP)" |
| **Best For** | Language model provider workflow | MCP client workflow |

---

## See Also

- [GAIA API Server Documentation](./api.md) - OpenAI-compatible API server
- [GAIA MCP Server Documentation](./mcp.md) - Model Context Protocol server
- [Code Agent Documentation](./code.md) - Code agent capabilities
- [Jira Agent Documentation](./jira.md) - Jira agent capabilities
- [Development Guide](./dev.md) - GAIA framework setup
- [VSCode Language Model API](https://code.visualstudio.com/api/extension-guides/ai/language-model-chat-provider) - Official VSCode docs

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
