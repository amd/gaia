# Agent UI Agent Capabilities Plan

> **Branch:** `kalin/chat-ui`
> **Date:** 2026-03-06
>
> **Two Milestones:**
> - **Milestone A** — [Agent UI: Wire Existing SDK Capabilities (#15)](https://github.com/amd/gaia/milestone/15)
>   *Expose existing GAIA SDK features to the Agent UI. No new SDK code — just wiring,
>   MCP integration, and UI work.*
> - **Milestone B** — GAIA Agent SDK: New Capabilities (TBD)
>   *Enhance the core GAIA Agent SDK with capabilities that don't exist yet:
>   guardrails framework, screenshot capture, computer use, voice, etc.*

---

## Milestone Scope Summary

### Milestone A — Agent UI: Wire Existing SDK Capabilities
**Goal:** Make ChatAgent as capable as possible using what the SDK already has.

| Category | What to Do | New SDK Code? |
|----------|-----------|---------------|
| File I/O | Add `FileIOToolsMixin` to ChatAgent | No (refactor only — §10.1 graceful degradation) |
| File listing | Add `ProjectManagementMixin` | No |
| Web search | Add `ExternalToolsMixin` (conditional registration) | No |
| MCP integration | Add `MCPClientMixin` to ChatAgent | No |
| MCP UI | MCP Server Manager panel in Settings | UI only |
| MCP catalog | Curated server catalog (Playwright, Brave, GitHub, etc.) | Config only |
| Browser | Enable Playwright MCP server | MCP config |
| Email/Calendar | Enable Gmail/Outlook/Calendar MCP servers | MCP config |
| App control | Enable Spotify/Obsidian/etc. MCP servers | MCP config |
| Tool discovery | Agent capabilities discovery API (#440) | Minimal API |
| Tool streaming | Tool argument streaming (#441) | Minimal |

### Milestone B — GAIA Agent SDK: New Capabilities
**Goal:** Build new capabilities in the core SDK that don't exist anywhere today.

| Category | What to Build | Scope |
|----------|--------------|-------|
| **Guardrails** | Tool execution confirmation framework (#438) | New SDK framework — OutputHandler, SSE, threading.Event, UI modal |
| **Cancellation** | Cooperative execution cancellation (#439) | New SDK framework — cancel tokens, cleanup |
| **Screenshot** | `ScreenshotToolsMixin` — cross-platform screen capture | New mixin (PIL.ImageGrab, mss) |
| **VLM for Chat** | Wire VLMToolsMixin into ChatAgent + Agent UI image display | Integration + UI |
| **Computer Use** | Desktop automation (mouse, keyboard, window mgmt) | New mixin (pyautogui, pywinauto) |
| **Voice** | Wire ASR/TTS into Agent UI (MediaRecorder, audio playback) | Integration + UI |
| **Tool categories** | Lazy loading, per-session tool selection | SDK architecture change |
| **Cross-platform** | Windows/Linux/macOS shell compat (#442) | SDK enhancement |
| **Image generation** | Wire SDToolsMixin into ChatAgent | Integration |
| **MCP Auto-Discovery** | Search, find, recommend, and install MCP servers on demand (§13.1) | New SDK feature — npm/GitHub registry search, auto-install flow |
| **SKILL.md Support** | Anthropic-compatible skill persistence — load, save, search, share (§13.2) | New SDK feature — skills directory, RAG integration, format spec |

---

## 1. Current GAIA SDK Capability Inventory

### 1.1 Agents

| Agent | Class | Location | Tools/Mixins |
|-------|-------|----------|-------------|
| **ChatAgent** | `ChatAgent(Agent, RAGToolsMixin, FileToolsMixin, ShellToolsMixin, FileSearchToolsMixin)` | `agents/chat/agent.py` | RAG, file watch, shell commands, file search |
| **CodeAgent** | `CodeAgent(ApiAgent, Agent, CodeToolsMixin, ValidationAndParsingMixin, FileIOToolsMixin, CodeFormattingMixin, ProjectManagementMixin, TestingMixin, ErrorFixingMixin, TypeScriptToolsMixin, WebToolsMixin, PrismaToolsMixin, CLIToolsMixin, ExternalToolsMixin, ValidationToolsMixin)` | `agents/code/agent.py` | Full-stack dev, CLI, testing, web, Prisma, external search |
| **BlenderAgent** | `BlenderAgent(Agent)` | `agents/blender/agent.py` | 3D scene manipulation via MCP |
| **JiraAgent** | `JiraAgent(Agent)` | `agents/jira/agent.py` | Jira issue management |
| **DockerAgent** | `DockerAgent(MCPAgent)` | `agents/docker/agent.py` | Docker container management via MCP |
| **SDAgent** | `SDAgent(Agent, SDToolsMixin, VLMToolsMixin)` | `agents/sd/agent.py` | Image generation + visual analysis |
| **MedicalIntakeAgent** | `MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin)` | `agents/emr/agent.py` | Medical form processing with VLM |
| **RoutingAgent** | `RoutingAgent` | `agents/routing/agent.py` | Intelligent agent selection |
| **SummarizerAgent** | `SummarizerAgent(Agent)` | `agents/summarize/agent.py` | Document summarization |

### 1.2 ChatAgent Tools (Current — What the Agent UI Uses)

| Tool | Mixin | Description |
|------|-------|-------------|
| `run_shell_command` | `ShellToolsMixin` | Execute terminal commands (whitelisted, read-only) |
| `add_watch_directory` | `FileToolsMixin` | Watch a directory for file changes |
| `query_documents` | `RAGToolsMixin` | Semantic search across indexed documents |
| `query_specific_file` | `RAGToolsMixin` | Query a specific indexed file |
| `search_indexed_chunks` | `RAGToolsMixin` | Low-level chunk search |
| `evaluate_retrieval` | `RAGToolsMixin` | Evaluate RAG retrieval quality |
| `index_document` | `RAGToolsMixin` | Index a document for RAG |
| `index_directory` | `RAGToolsMixin` | Index all documents in a directory |
| `list_indexed_documents` | `RAGToolsMixin` | List all indexed documents |
| `rag_status` | `RAGToolsMixin` | Get RAG system status |
| `summarize_document` | `RAGToolsMixin` | Summarize an indexed document |
| `dump_document` | `RAGToolsMixin` | Dump raw document content |
| *(FileSearchToolsMixin)* | `FileSearchToolsMixin` | Shared file search utilities |

### 1.3 CodeAgent Tools (Available in SDK, NOT in Agent UI)

| Tool | Mixin | Description |
|------|-------|-------------|
| `read_file` | `FileIOToolsMixin` | Read file contents |
| `write_file` | `FileIOToolsMixin` | Write/create files |
| `edit_file` | `FileIOToolsMixin` | Edit existing files (diff-based) |
| `edit_python_file` | `FileIOToolsMixin` | Python-aware file editing |
| `search_code` | `FileIOToolsMixin` | Search code with regex/glob |
| `run_cli_command` | `CLIToolsMixin` | Execute any CLI command (broader than shell_tools) |
| `stop_process` | `CLIToolsMixin` | Stop background processes |
| `list_processes` | `CLIToolsMixin` | List managed background processes |
| `get_process_logs` | `CLIToolsMixin` | Get output from background processes |
| `cleanup_all_processes` | `CLIToolsMixin` | Stop all background processes |
| `execute_python_file` | `TestingMixin` | Execute Python scripts |
| `run_tests` | `TestingMixin` | Run pytest test suites |
| `list_files` | `ProjectManagementMixin` | List files in directory tree |
| `create_project` | `ProjectManagementMixin` | Create project from template |
| `create_architectural_plan` | `ErrorFixingMixin` | Generate architecture plans |
| `create_workflow_plan` | `ErrorFixingMixin` | Generate workflow plans |
| `search_documentation` | `ExternalToolsMixin` | Search Context7 documentation |
| `search_web` | `ExternalToolsMixin` | Web search via Perplexity |
| `list_symbols` | `CodeToolsMixin` | List code symbols (AST) |
| Various TypeScript/Web tools | `TypeScriptToolsMixin`, `WebToolsMixin` | npm, template, Next.js |
| Various Prisma tools | `PrismaToolsMixin` | Database schema management |

### 1.4 Other SDK Capabilities (Not Exposed to Any Agent)

| Capability | SDK Location | Description |
|------------|-------------|-------------|
| **Vision/VLM** | `gaia/vlm/mixin.py` | `analyze_image`, `answer_question_about_image` |
| **Image Generation** | `gaia/sd/mixin.py` | `generate_image`, `list_sd_models`, `get_generation_history` |
| **Audio/ASR** | `gaia/audio/whisper_asr.py` | Speech-to-text (Whisper) |
| **Audio/TTS** | `gaia/audio/kokoro_tts.py` | Text-to-speech (Kokoro) |
| **MCP Bridge** | `gaia/mcp/mcp_bridge.py` | External tool integration via MCP |
| **Database** | `gaia/database/` | `DatabaseMixin` for persistent storage |
| **Multi-provider LLM** | `gaia/llm/providers/` | Claude, OpenAI, Lemonade backends |
| **Agent Routing** | `agents/routing/agent.py` | Intelligent multi-agent routing |

---

## 2. Gap Analysis: Agent UI Agent vs. Modern PC Agent Expectations

### 2.1 Capabilities Users Expect Today

Based on the current landscape (Claude Computer Use, OpenAI Operator, Windows Copilot, etc.):

| Category | Capability | Status | Priority |
|----------|-----------|--------|----------|
| **File System** | Read/write/edit files | MISSING (ChatAgent only has read-only shell + RAG) | P0 |
| **File System** | Create directories, move/copy/rename files | MISSING | P0 |
| **File System** | File search (name, content, regex) | EXISTS via FileSearchToolsMixin | P1 |
| **Shell** | Run shell commands | EXISTS | P0 |
| **Shell** | Background process management | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Web** | Browse URLs, fetch web content | MISSING | P1 |
| **Web** | Search the web | MISSING in ChatAgent (exists in CodeAgent via Perplexity) | P1 |
| **Vision** | Take screenshots of desktop/windows | MISSING | P1 |
| **Vision** | Analyze images/screenshots | MISSING in ChatAgent (exists in SDAgent) | P1 |
| **Vision** | OCR / read text from images | MISSING | P2 |
| **Computer Use** | Click, type, scroll on screen | MISSING | P2 |
| **Computer Use** | Control mouse and keyboard | MISSING | P2 |
| **Computer Use** | Window management (focus, resize, list) | MISSING | P2 |
| **Code** | Read/write/edit code files | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Code** | Run Python scripts | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Audio** | Voice input (speech-to-text) | MISSING in Agent UI (SDK exists) | P2 |
| **Audio** | Voice output (text-to-speech) | MISSING in Agent UI (SDK exists) | P2 |
| **Image Gen** | Generate images from prompts | MISSING in ChatAgent (exists in SDAgent) | P2 |
| **Clipboard** | Read/write clipboard | MISSING | P2 |
| **System** | Get system info (OS, CPU, GPU, memory) | PARTIAL (shell commands) | P2 |
| **Browser** | Open URLs in default browser | MISSING | P2 |
| **Notifications** | Desktop notifications | MISSING | P3 |
| **Scheduling** | Schedule tasks, set reminders | MISSING | P3 |
| **App Control** | Launch/close applications | MISSING | P3 |

### 2.2 What Can Be Added to ChatAgent NOW (Reusing Existing SDK)

These capabilities already exist in the codebase and just need to be wired into ChatAgent:

| Capability | Source | Effort | How |
|-----------|--------|--------|-----|
| File read/write/edit | `FileIOToolsMixin` from CodeAgent | **Low** | Add mixin to ChatAgent class |
| Code search | `FileIOToolsMixin.search_code` | **Low** | Included with FileIOToolsMixin |
| List files (tree view) | `ProjectManagementMixin.list_files` | **Low** | Add mixin to ChatAgent class |
| Web search | `ExternalToolsMixin.search_web` | **Low** | Add mixin to ChatAgent class |
| Doc search (Context7) | `ExternalToolsMixin.search_documentation` | **Low** | Add mixin to ChatAgent class |
| Image analysis | `VLMToolsMixin.analyze_image` | **Medium** | Add mixin + VLM model loading |
| Image Q&A | `VLMToolsMixin.answer_question_about_image` | **Medium** | Same as above |
| Image generation | `SDToolsMixin.generate_image` | **Medium** | Add mixin + SD model loading |
| Background processes | `CLIToolsMixin` (run/stop/list/logs) | **Medium** | Add mixin, security review |
| Python execution | `TestingMixin.execute_python_file` | **Medium** | Add mixin, sandbox review |

### 2.3 What Requires New Development

These capabilities don't exist anywhere in GAIA and need to be built:

| Capability | Category | Effort | Notes |
|-----------|----------|--------|-------|
| **Screenshot capture** | Vision | **Medium** | Use `PIL.ImageGrab` (Windows) or platform APIs. New tool mixin. |
| **Web browsing / URL fetch** | Web | **Medium** | `httpx` + BeautifulSoup for content extraction. New tool mixin. |
| **Clipboard read/write** | System | **Low** | `pyperclip` or `win32clipboard`. New tool. |
| **Open URL in browser** | System | **Low** | `webbrowser.open()`. New tool. |
| **Desktop/window control** | Computer Use | **High** | `pyautogui` / `pywinauto` for Windows. Complex, needs careful security. |
| **Mouse/keyboard control** | Computer Use | **High** | `pyautogui`. Very powerful, very dangerous. Requires guardrails (#438). |
| **Window listing/management** | Computer Use | **Medium** | `pywinauto` on Windows, `wmctrl` on Linux. |
| **Voice input (ASR)** | Audio | **Medium** | Wire existing `whisper_asr.py` SDK into Agent UI. WebSocket or MediaRecorder API. |
| **Voice output (TTS)** | Audio | **Medium** | Wire existing `kokoro_tts.py` SDK into Agent UI. Audio playback. |
| **Desktop notifications** | System | **Low** | `plyer` or `win10toast` on Windows. |
| **App launch/control** | System | **Medium** | `subprocess.Popen` for launch, `psutil` for control. Security-sensitive. |
| **Task scheduling** | System | **Medium** | Windows Task Scheduler or `APScheduler`. Persistent. |

---

## 3. Implementation Plan

### Phase 1: Quick Wins — Wire Existing SDK into ChatAgent (1-2 weeks)

Extend `ChatAgent` with existing mixins from CodeAgent and other agents. Minimal new code.

| # | Feature | Mixin to Add | Risk |
|---|---------|-------------|------|
| 1a | File read/write/edit | `FileIOToolsMixin` | Low — already battle-tested in CodeAgent |
| 1b | Code search | *(included in FileIOToolsMixin)* | Low |
| 1c | List files (tree view) | `ProjectManagementMixin` | Low |
| 1d | Web search | `ExternalToolsMixin` | Low — requires Perplexity API key or fallback |
| 1e | Python script execution | `TestingMixin` | Medium — needs sandboxing review |

**ChatAgent class after Phase 1:**
```python
class ChatAgent(
    Agent,
    RAGToolsMixin,         # Existing: document Q&A
    FileToolsMixin,        # Existing: file watching
    ShellToolsMixin,       # Existing: shell commands
    FileSearchToolsMixin,  # Existing: file search
    FileIOToolsMixin,      # NEW: read/write/edit files
    ProjectManagementMixin,# NEW: list_files, create_project
    ExternalToolsMixin,    # NEW: web search, doc search
    TestingMixin,          # NEW: execute Python, run tests
):
```

### Phase 2: Vision & Media (2-3 weeks)

Add image analysis, screenshot capture, and image generation.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 2a | Image analysis (VLM) | Add `VLMToolsMixin`, load VLM model alongside main LLM | Medium — needs VLM model (Qwen3-VL-4B) |
| 2b | Screenshot capture | New `ScreenshotToolsMixin` using `PIL.ImageGrab` + `mss` | Medium — cross-platform |
| 2c | Image generation (SD) | Add `SDToolsMixin`, requires Lemonade SD model | Medium — optional, SD model may not be loaded |
| 2d | Image display in Agent UI | Frontend: render images inline in chat messages | Medium — base64 or file URL serving |

### Phase 3: Web & System (2-3 weeks)

Add web browsing, clipboard, and basic system tools.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 3a | URL fetch / web scraping | New `WebBrowsingToolsMixin` using `httpx` + `BeautifulSoup` | Low |
| 3b | Open URL in browser | New tool using `webbrowser.open()` | Low |
| 3c | Clipboard read/write | New tool using `pyperclip` | Low |
| 3d | System info | New tool using `platform`, `psutil`, `GPUtil` | Low |
| 3e | Desktop notifications | New tool using `plyer` | Low |

### Phase 4: Computer Use (4-6 weeks, separate milestone)

Full desktop automation. This is the most complex and security-sensitive phase.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 4a | Window listing | `pywinauto` (Win) / `wmctrl` (Linux) / `pyobjc` (macOS) | Medium |
| 4b | Window focus/resize | Same as above | Medium |
| 4c | Screenshot of specific window | `PIL.ImageGrab` with window handle | Medium |
| 4d | Mouse click/move | `pyautogui` with coordinate targeting | **High** — needs guardrails |
| 4e | Keyboard typing | `pyautogui.typewrite()` | **High** — needs guardrails |
| 4f | Screen element detection | VLM + screenshot → identify clickable elements | **High** — requires VLM |
| 4g | Browser automation | Playwright via MCP or direct integration | **High** — complex |

### Phase 5: Audio/Voice (2-3 weeks)

Wire existing Whisper ASR and Kokoro TTS into Agent UI.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 5a | Voice input (push-to-talk) | Browser MediaRecorder → backend → Whisper ASR | Medium |
| 5b | Voice output (TTS) | Backend Kokoro TTS → audio stream → browser playback | Medium |
| 5c | Voice conversation mode | Continuous ASR + TTS for hands-free chat | High |

---

## 4. Cross-Platform Requirements

All capabilities MUST work on Windows, Linux, and macOS:

| Capability | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Shell commands | `cmd.exe` / PowerShell (shell=True) | `/bin/sh` | `/bin/zsh` |
| File operations | `pathlib` (cross-platform) | Same | Same |
| Screenshots | `PIL.ImageGrab` / `mss` | `mss` / `scrot` | `mss` / `screencapture` |
| Clipboard | `pyperclip` (auto-detects) | `xclip`/`xsel` | `pbcopy`/`pbpaste` |
| Window mgmt | `pywinauto` | `wmctrl`/`xdotool` | `pyobjc`/`osascript` |
| Notifications | `win10toast` / `plyer` | `notify-send` / `plyer` | `osascript` / `plyer` |
| Mouse/keyboard | `pyautogui` (cross-platform) | Same | Same (accessibility permissions) |
| Browser open | `webbrowser.open()` (cross-platform) | Same | Same |

---

## 5. Security Considerations

| Risk | Mitigation |
|------|-----------|
| Shell command injection | Whitelist approach (existing), guardrails popup (#438) |
| File write to system paths | PathValidator (existing), restricted allowed_paths |
| Arbitrary code execution | Sandboxed Python execution, no `eval()`/`exec()` |
| Screenshot privacy | User confirmation before capture, no auto-capture |
| Computer use (mouse/keyboard) | Mandatory confirmation per action, visual indicator, kill switch |
| Web requests (SSRF) | URL allowlist, no internal network access |
| Clipboard access | User confirmation, no silent reads |

---

## 6. Issue Tracker

### Already Created (Milestone #15)

| Issue | Title | Status |
|-------|-------|--------|
| [#438](https://github.com/amd/gaia/issues/438) | Tool execution guardrails | Open |
| [#439](https://github.com/amd/gaia/issues/439) | Cooperative execution cancellation | Open |
| [#440](https://github.com/amd/gaia/issues/440) | Agent capabilities discovery API | Open |
| [#441](https://github.com/amd/gaia/issues/441) | Tool argument streaming | Open |
| [#442](https://github.com/amd/gaia/issues/442) | Windows/cross-platform shell compatibility | Open |

### To Create (Phase 1-5)

| Phase | Title | Priority |
|-------|-------|----------|
| P1 | Add FileIOToolsMixin to ChatAgent (file read/write/edit) | P0 |
| P1 | Add ExternalToolsMixin to ChatAgent (web search) | P1 |
| P1 | Add ProjectManagementMixin to ChatAgent (list_files) | P1 |
| P1 | Add TestingMixin to ChatAgent (Python execution) | P1 |
| P2 | Add VLMToolsMixin to ChatAgent (image analysis) | P1 |
| P2 | Screenshot capture tool mixin | P1 |
| P2 | Image display in Agent UI messages | P1 |
| P2 | Add SDToolsMixin to ChatAgent (image generation) | P2 |
| P3 | Web browsing / URL fetch tool mixin | P1 |
| P3 | Clipboard read/write tool | P2 |
| P3 | Open URL in browser tool | P2 |
| P3 | System info tool | P2 |
| P3 | Desktop notifications tool | P3 |
| P4 | Window listing and management tool mixin | P2 |
| P4 | Mouse/keyboard control tool mixin (computer use) | P2 |
| P4 | Browser automation via Playwright | P2 |
| P5 | Voice input (ASR) in Agent UI | P2 |
| P5 | Voice output (TTS) in Agent UI | P2 |

---

## 7. MCP Server Integration

### 7.1 Current MCP Infrastructure in GAIA

GAIA already has a robust MCP client infrastructure:

- **`MCPClientMixin`** (`gaia/mcp/mixin.py`) — Any agent can connect to MCP servers and auto-register their tools
- **`MCPClientManager`** — Manages multiple MCP server connections
- **Config file** — `~/.gaia/mcp_servers.json` for persistent server configuration
- **`MCPAgent`** base class — `agents/base/mcp_agent.py`
- **MCP Bridge** — `gaia/mcp/mcp_bridge.py` exposes GAIA as an MCP server to external tools
- **Existing integrations** — Docker MCP, Blender MCP already implemented

**Gap:** The Agent UI has NO way to manage MCP servers. Users can't add, remove, enable/disable, or configure MCP servers from the UI.

### 7.2 Most Popular MCP Servers (2026 Ecosystem)

Based on real usage data from [FastMCP](https://fastmcp.me/blog/top-10-most-popular-mcp-servers) (1,864+ servers tracked) and [mcpservers.org](https://mcpservers.org/):

#### Tier 1 — Essential (High demand, directly useful for Agent UI)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Secure file operations with configurable access controls | File System |
| **Playwright** | `@anthropic/mcp-playwright` | Browser automation via accessibility snapshots (not screenshots) | Browser |
| **GitHub** | `@modelcontextprotocol/server-github` | Repos, PRs, issues, workflows — full GitHub access | Dev Tools |
| **Desktop Commander** | `desktop-commander` | Terminal command execution + file operations with user control | System |
| **Fetch** | `@modelcontextprotocol/server-fetch` | Web content fetching and conversion to markdown | Web |
| **Memory** | `@modelcontextprotocol/server-memory` | Knowledge graph-based persistent memory for agents | Context |
| **Git** | `@modelcontextprotocol/server-git` | Git repository tools (log, diff, status, blame) | Dev Tools |
| **Sequential Thinking** | `@anthropic/mcp-sequential-thinking` | Structured reasoning for complex problems | Reasoning |

#### Tier 2 — High Value (Popular integrations users commonly request)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Slack** | `slack-mcp-server` | Channel management, messaging, conversation history | Communication |
| **Notion** | `notion-mcp` | Workspace pages, databases, tasks | Productivity |
| **Google Drive** | `google-drive-mcp` | File access, search, sharing | Cloud Storage |
| **PostgreSQL** | `@modelcontextprotocol/server-postgres` | Database queries | Database |
| **Brave Search** | `@anthropic/mcp-brave-search` | Web search (alternative to Perplexity) | Web Search |
| **Context7** | `context7-mcp` | Inject fresh, version-specific code docs into prompts | Documentation |

#### Tier 3 — Windows Desktop Automation (Key for "Computer Use")

| Server | Repo | Description | Platform |
|--------|------|-------------|----------|
| **Windows-MCP** | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | Native Windows UI automation: open apps, control windows, simulate input, capture UI state | Windows |
| **mcp-windows-desktop-automation** | [mario-andreschak/mcp-windows-desktop-automation](https://github.com/mario-andreschak/mcp-windows-desktop-automation) | TypeScript MCP wrapping AutoIt: mouse, keyboard, clipboard, window management | Windows |
| **mcp-windows-automation** | [mukul975/mcp-windows-automation](https://github.com/mukul975/mcp-windows-automation) | 80+ automation tools: app control, system management, natural language commands | Windows |
| **mcp-desktop-automation** | [tanob/mcp-desktop-automation](https://github.com/tanob/mcp-desktop-automation) | Cross-platform desktop automation using RobotJS + screenshots | Cross-platform |

#### Tier 4 — Microsoft Ecosystem (Enterprise)

| Server | Source | Description |
|--------|--------|-------------|
| **Microsoft Learn MCP** | [MicrosoftDocs/mcp](https://github.com/MicrosoftDocs/mcp) | Real-time Microsoft documentation access |
| **Azure MCP Server** | [Microsoft Learn](https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/overview) | Azure resource management via natural language |
| **Azure DevOps MCP** | [Microsoft Learn](https://learn.microsoft.com/en-us/azure/devops/mcp-server/mcp-server-overview) | Work items, PRs, builds, test plans |
| **Windows On-Device Agent Registry** | [Microsoft Learn](https://learn.microsoft.com/en-us/windows/ai/mcp/overview) | Secure discovery of local MCP servers on Windows |

### 7.3 Agent UI MCP Integration Design

#### A) MCP Server Manager Panel (Settings)

The Agent UI Settings modal gets an "MCP Servers" tab where users can:

1. **Browse/add popular servers** from a curated list (Tier 1-2 above)
2. **Add custom servers** by providing command + args + env config
3. **Enable/disable servers** per session or globally
4. **View connected server status** (connected, tools available, errors)
5. **Configure server credentials** (API keys, tokens) with secure storage

#### B) Backend API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mcp/servers` | GET | List configured MCP servers and their status |
| `/api/mcp/servers` | POST | Add a new MCP server configuration |
| `/api/mcp/servers/{name}` | DELETE | Remove an MCP server |
| `/api/mcp/servers/{name}/enable` | POST | Enable/connect a server |
| `/api/mcp/servers/{name}/disable` | POST | Disable/disconnect a server |
| `/api/mcp/servers/{name}/tools` | GET | List tools provided by a server |
| `/api/mcp/catalog` | GET | Get curated list of popular servers |

#### C) ChatAgent MCP Integration

```python
class ChatAgent(
    Agent,
    MCPClientMixin,        # NEW: MCP server connectivity
    RAGToolsMixin,
    FileToolsMixin,
    ShellToolsMixin,
    FileSearchToolsMixin,
    # ... other mixins
):
```

When the Agent UI enables an MCP server, the backend:
1. Calls `agent.connect_mcp_server(name, config)`
2. Tools from the MCP server are auto-registered in the agent's tool registry
3. The agent can now use those tools in its planning/execution
4. Tools appear in the Capabilities panel (#440)

#### D) Curated Server Catalog

Ship a built-in catalog (`~/.gaia/mcp_catalog.json`) with pre-configured popular servers:

```json
{
  "catalog": [
    {
      "name": "filesystem",
      "display_name": "File System",
      "description": "Secure file read/write/search with configurable access",
      "category": "system",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"],
      "requires_config": ["allowed_directories"],
      "tier": 1
    },
    {
      "name": "github",
      "display_name": "GitHub",
      "description": "Repos, PRs, issues, workflows",
      "category": "dev-tools",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": ""},
      "requires_config": ["GITHUB_TOKEN"],
      "tier": 1
    },
    {
      "name": "playwright",
      "display_name": "Browser (Playwright)",
      "description": "Web browsing and interaction via accessibility snapshots",
      "category": "browser",
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-playwright"],
      "tier": 1
    }
  ]
}
```

### 7.4 MCP Issues to Create

| Phase | Title | Priority |
|-------|-------|----------|
| P1 | Add MCPClientMixin to ChatAgent | P0 |
| P1 | MCP server management API endpoints | P0 |
| P1 | MCP Server Manager UI panel in Settings | P0 |
| P1 | Curated MCP server catalog with Tier 1 servers | P1 |
| P2 | MCP server credential secure storage | P1 |
| P2 | Per-session MCP server enable/disable | P2 |
| P2 | MCP server health monitoring and auto-reconnect | P2 |
| P3 | Windows Desktop MCP integration (computer use) | P2 |
| P3 | Windows On-Device Agent Registry (ODR) integration | P3 |

---

## 8. Dependencies

```
Phase 1 (Quick Wins — Existing SDK Mixins)
  └── No external dependencies (reuse existing SDK)

Phase 1-MCP (MCP Server Integration)
  ├── MCPClientMixin (already exists in SDK)
  ├── Node.js/npx (for npm-based MCP servers)
  └── MCP server packages installed on demand

Phase 2 (Vision & Media)
  ├── Lemonade Server with VLM model loaded (Qwen3-VL-4B)
  ├── PIL/Pillow (already in deps)
  └── mss (new dep for cross-platform screenshots)

Phase 3 (Web & System)
  ├── httpx (already in deps)
  ├── beautifulsoup4 (new dep)
  ├── pyperclip (new dep)
  └── plyer (new dep for notifications)

Phase 4 (Computer Use)
  ├── Phase 1 (guardrails MUST be done first)
  ├── Phase 2 (VLM for screen understanding)
  ├── pyautogui (new dep)
  ├── pywinauto (Windows, new dep)
  ├── Playwright (optional, for browser automation)
  └── OR: Windows Desktop MCP servers (external, via MCP)

Phase 5 (Audio/Voice)
  ├── Whisper ASR model loaded in Lemonade
  ├── Kokoro TTS model loaded in Lemonade
  └── Browser MediaRecorder API support
```

## 9. Critical Capabilities Coverage Matrix

The following capabilities were identified as **user-critical priorities**. This matrix
tracks exactly where each is addressed in the plan and flags gaps.

| Critical Capability | Covered? | Where in Plan | Gaps / Issues |
|---------------------|----------|---------------|---------------|
| **Browser control & use** | ✅ Yes | Phase 4g (Playwright), MCP Tier 1 (Playwright MCP) | Playwright MCP is the fastest path. Native Playwright should be deferred. Plan puts this in Phase 4 (too late) — should be Phase 1-MCP. See §9.1. |
| **Web search** | ✅ Yes | Phase 1d (ExternalToolsMixin), MCP Tier 2 (Brave Search) | ExternalToolsMixin requires Perplexity API key — needs free fallback (Brave Search MCP). See §9.2. |
| **Document analysis** | ✅ Yes | Already exists (RAGToolsMixin) | Fully functional: index, query, summarize, dump. Needs no changes. |
| **Document search** | ✅ Yes | Already exists (RAGToolsMixin + FileSearchToolsMixin) | Working: `query_documents`, `search_indexed_chunks`, `query_specific_file`. |
| **Document summarization** | ✅ Yes | Already exists (`summarize_document` in RAGToolsMixin) | Also have standalone `SummarizerAgent`. Could wire summarizer into ChatAgent for long docs. |
| **Document Q&A** | ✅ Yes | Already exists (RAGToolsMixin) | Core feature, fully operational. |
| **Shell command tools** | ✅ Yes | Already exists (ShellToolsMixin), Windows fix done | Whitelist-only, read-only. Need write capability discussion (§9.3). |
| **Guardrails (catastrophic failure prevention)** | ✅ Yes | Issue #438, §5 Security | Design exists but **not yet implemented**. This is the single most important prerequisite for all write/execute capabilities. See §9.4. |
| **Email triage & management** | ❌ **MISSING** | Not in plan | Needs Email/Calendar MCP servers. See §9.5. |
| **Calendar management** | ❌ **MISSING** | Not in plan | Needs Google Calendar / Outlook MCP. See §9.5. |
| **Application control (CUA)** | ⚠️ Partial | Phase 4 (pyautogui/pywinauto) | Plan covers low-level mouse/keyboard but NOT application-specific control patterns. See §9.6. |
| **Popular app demos (e.g. Spotify)** | ❌ **MISSING** | Not in plan | Needs Spotify MCP or CUA workflow. See §9.6. |

### 9.1 Browser Control — Needs Priority Bump

The plan buries browser automation in Phase 4g which is 8+ weeks out. But the
**Playwright MCP server** is a Tier 1 server that works TODAY with the existing
`MCPClientMixin`. This should be promoted to **Phase 1-MCP** (first sprint):

```
BEFORE:  Phase 4g (week 8+) — build native Playwright integration
AFTER:   Phase 1-MCP (week 1) — enable Playwright MCP server
         Phase 4g (later) — build native integration only if MCP insufficient
```

The Playwright MCP provides: navigate, click, fill forms, take screenshots, read page
content — all via accessibility snapshots. This covers 90% of browser use cases.

### 9.2 Web Search — Needs Free Fallback

`ExternalToolsMixin.search_web` requires a `PERPLEXITY_API_KEY` environment variable.
If the user doesn't have one, web search silently fails. The plan says "Low effort"
but doesn't address this.

**Fix:** Prioritize **Brave Search MCP** (`@anthropic/mcp-brave-search`) as the
default web search. It's free-tier capable and runs as a standard MCP server. Fall
back chain should be:
1. Brave Search MCP (free, no API key for basic usage)
2. Perplexity (if API key available, via ExternalToolsMixin)
3. Fetch MCP (raw URL fetch + markdown conversion as last resort)

### 9.3 Shell Commands — Write Capability Gap

Current `ShellToolsMixin` is **read-only** by design (whitelist: `ls`, `cat`, `grep`,
`find`, etc.). This is safe but limiting — users will want:
- `mkdir` — create directories
- `cp`/`mv` — copy/move files
- `pip install` — install packages
- `npm install` — install node packages

**Recommendation:** Don't expand the shell whitelist. Instead, rely on:
1. `FileIOToolsMixin` for file write/create/edit (Phase 1a)
2. `CLIToolsMixin` for broader command execution (Phase 1, with guardrails)
3. Guardrails (#438) to confirm dangerous operations

### 9.4 Guardrails — The Critical Prerequisite

**Issue:** The plan lists guardrails (#438) as "NEXT SPRINT" but also adds
`FileIOToolsMixin` (file write), `TestingMixin` (Python execution), and `CLIToolsMixin`
(arbitrary commands) in the same sprint. This means **write/execute capabilities would
ship before the safety mechanism that protects against them**.

**Mandatory fix:** Guardrails (#438) MUST be implemented BEFORE or simultaneously
with any write/execute capability. The implementation order should be:

```
Week 1: Guardrails framework (#438) + read-only mixins (ProjectManagementMixin)
Week 2: FileIOToolsMixin + ExternalToolsMixin (with guardrails active)
Week 3: CLIToolsMixin + TestingMixin (with guardrails active)
```

### 9.5 Email & Calendar — New Capability Needed

**Completely missing from the plan.** This is a critical gap for a PC agent. Users
expect to triage emails, manage calendar events, and get summaries of their day.

#### MCP Servers Available

| Server | Package | Description |
|--------|---------|-------------|
| **Gmail MCP** | `gmail-mcp-server` / `@anthropic/mcp-gmail` | Read, search, send, label, archive Gmail messages |
| **Outlook MCP** | `outlook-mcp-server` | Microsoft Outlook email access via Graph API |
| **Google Calendar MCP** | `google-calendar-mcp` | Events, scheduling, availability, RSVP |
| **Microsoft Calendar MCP** | `outlook-calendar-mcp` | Outlook calendar via Graph API |
| **Nylas MCP** | `nylas-mcp-server` | Unified email + calendar (Gmail + Outlook + more) |

#### User Workflows (Email)

| # | Workflow | Tools Needed | Validation Test |
|---|---------|-------------|-----------------|
| E1 | "Summarize my unread emails" | Gmail/Outlook MCP → list unread → LLM summarize | User sees bulleted summary of unread emails with sender, subject, key action items |
| E2 | "Find all emails from [person] about [topic]" | Gmail MCP → search → display results | User sees filtered list with relevant messages highlighted |
| E3 | "Draft a reply to [email]" | Gmail MCP → read thread → LLM draft → confirm → send | Draft shown in chat, user confirms, email sent |
| E4 | "Archive/label emails matching [criteria]" | Gmail MCP → search → batch archive/label | Confirmation popup showing N emails to be affected, user approves |
| E5 | "What meetings do I have today?" | Calendar MCP → list events → LLM format | Formatted schedule with times, attendees, locations |
| E6 | "Schedule a meeting with [person] at [time]" | Calendar MCP → check availability → create event | Event created, confirmation shown |
| E7 | "Move my 2pm meeting to 3pm" | Calendar MCP → find event → update → confirm | Confirmation of change, attendees notified |

#### Priority

Add to **Phase 1-MCP** (Tier 2 servers) — these are MCP integrations, not native
code. The ChatAgent just needs `MCPClientMixin` and the user adds the server in
Settings.

### 9.6 Application Control (CUA) & Popular App Demos

The plan covers low-level computer use (Phase 4: pyautogui, pywinauto) but misses
the **high-level application control** pattern that users actually want. Users don't
say "move mouse to (432, 128) and click" — they say "play my Discover Weekly on Spotify"
or "open my latest document in Word".

#### MCP Servers for Application Control

| Server | Package | Description |
|--------|---------|-------------|
| **Spotify MCP** | `spotify-mcp-server` | Play, pause, skip, search, playlist management |
| **Apple Music MCP** | `apple-music-mcp` | Music control on macOS |
| **VS Code MCP** | `vscode-mcp` | Editor control, file management |
| **Obsidian MCP** | `obsidian-mcp-server` | Note-taking, knowledge base |
| **Todoist MCP** | `todoist-mcp-server` | Task management |
| **Linear MCP** | `linear-mcp` | Issue tracking |
| **Discord MCP** | `discord-mcp-server` | Messaging |

#### CUA (Computer Use Agent) Strategy

Two complementary approaches:

1. **MCP-first** (preferred): Use app-specific MCP servers for structured, reliable control.
   Spotify MCP is better than clicking the Spotify UI because it's API-driven, reliable,
   and doesn't break when the UI changes.

2. **Vision + automation** (fallback): For apps without MCP servers, use:
   - Screenshot → VLM (identify UI elements) → pyautogui (click/type)
   - This is Phase 4 in the plan and requires VLM + guardrails

#### Demo Workflows

| # | Workflow | Approach | Validation Test |
|---|---------|----------|-----------------|
| A1 | "Play Discover Weekly on Spotify" | Spotify MCP → search playlist → play | Music starts playing, now-playing info shown in chat |
| A2 | "Open my latest project in VS Code" | Shell (code .) or VS Code MCP | VS Code opens with correct project |
| A3 | "Create a note in Obsidian about today's meeting" | Obsidian MCP → create note | Note created with formatted content |
| A4 | "Take a screenshot and describe what's on screen" | Screenshot tool → VLM analysis | Screenshot shown in chat with description |
| A5 | "Click the submit button on this form" | Screenshot → VLM → pyautogui | Visual confirmation of action |

---

## 10. Detailed Plan Critique & Issues Found

### 10.1 CRITICAL: `FileIOToolsMixin` Has Hidden Dependency

**Plan says:** "Add `FileIOToolsMixin` to ChatAgent — Low effort, just add mixin"
**Reality:** `FileIOToolsMixin` has a **hard dependency** on `ValidationAndParsingMixin`.

From `src/gaia/agents/code/tools/file_io.py` lines 26-31:
```python
class FileIOToolsMixin:
    """...
    NOTE: This mixin expects the agent to also have ValidationAndParsingMixin
    for _validate_python_syntax() and _parse_python_code() methods.
    """
```

When `read_file` processes a `.py` file (line 99), it calls `self._validate_python_syntax(content)`
which is defined in `ValidationAndParsingMixin`, not in `FileIOToolsMixin`. Without it,
reading ANY Python file will crash with `AttributeError`.

**Impact:** Effort is **Medium, not Low**. Options:
1. Add `ValidationAndParsingMixin` to ChatAgent (drags in `CodeSymbol`, `ParsedCode` models, validator classes)
2. Refactor `FileIOToolsMixin` to make Python validation optional (try/except around `_validate_python_syntax`)
3. Create a lightweight `ChatFileIOToolsMixin` that strips out Python-specific features

**Recommendation:** Option 2 — refactor with graceful degradation:
```python
# In read_file, for .py files:
if hasattr(self, '_validate_python_syntax'):
    validation = self._validate_python_syntax(content)
    result["is_valid"] = validation["is_valid"]
else:
    result["file_type"] = "python"  # still tag it, just skip validation
```

### 10.2 CRITICAL: `_TOOL_REGISTRY` Is Global — Tool Count Explosion

**The plan proposes adding 6+ mixins to ChatAgent.** Each mixin registers tools into
`_TOOL_REGISTRY` which is a **module-level global dict** (`src/gaia/agents/base/tools.py:16`).

Current tool counts:
- ChatAgent: **~13 tools** (12 @tool decorators across 3 files)
- CodeAgent: **~57 tools** (69 @tool decorators across 12 files, minus register functions)

If we add `FileIOToolsMixin` (11 tools), `CLIToolsMixin` (6 tools), `ExternalToolsMixin`
(3 tools), `ProjectManagementMixin` (4 tools), `TestingMixin` (3 tools), plus MCP
tools (variable), ChatAgent could have **40+ tools**.

**Problem:** Every tool's full docstring gets appended to the system prompt via
`_format_tools_for_prompt()` (agent.py:370-384). With 40 tools averaging 10 lines of
description each, that's **400+ lines** of tool descriptions in the system prompt.
The default context window is `min_context_size: 32768` tokens (~24K words). Tool
descriptions alone could consume 15-25% of the context.

**Impact:**
- Reduced context for actual conversation history
- LLM confusion from too many tool choices (decision paralysis)
- Slower inference (more tokens to process)

**Recommendations:**
1. **Lazy tool loading**: Only register tools when their category is needed (e.g., don't
   load Prisma tools if user isn't doing database work)
2. **Tool description compression**: Use 1-2 sentence descriptions in prompts, not full docstrings
3. **Tool categories**: Group tools and let the LLM request a category expansion
4. **Per-session tool selection**: Let users enable/disable tool categories from the UI
   (ties into #440 Agent Capabilities Discovery)

### 10.3 HIGH: `ExternalToolsMixin` — Silent Failure Risk

`ExternalToolsMixin` imports from `gaia.mcp.external_services`:
- `get_context7_service()` — requires `npx` on PATH (Node.js installed)
- `get_perplexity_service()` — requires `PERPLEXITY_API_KEY` env var

If neither is available, both tools will return error results but the tools
are **still registered** in the system prompt. The LLM will repeatedly try to use
them and fail.

**Fix:** Conditional tool registration — only register tools if their backend is available:
```python
def register_external_tools(self):
    if shutil.which("npx"):
        # register search_documentation
    if os.environ.get("PERPLEXITY_API_KEY"):
        # register search_web
```

### 10.4 MEDIUM: MCP-vs-Native Build/Buy Confusion

The plan proposes BOTH native implementations AND MCP server equivalents for the
same capabilities:

| Capability | Native (Plan) | MCP Equivalent |
|-----------|--------------|----------------|
| File read/write | FileIOToolsMixin (Phase 1a) | Filesystem MCP (Tier 1) |
| Shell commands | ShellToolsMixin (exists) + CLIToolsMixin (Phase 1) | Desktop Commander MCP (Tier 1) |
| Web search | ExternalToolsMixin (Phase 1d) | Brave Search MCP (Tier 2) |
| Browser | Playwright native (Phase 4g) | Playwright MCP (Tier 1) |
| Git | Shell commands | Git MCP (Tier 1) |

**The plan doesn't resolve which to use.** Having both creates:
- Duplicate tools in registry (LLM sees `read_file` AND `filesystem__read_file`)
- Conflicting behaviors (different error formats, different security models)
- Maintenance burden

**Recommendation:** Clear decision framework:
- **Native tools** for core, always-available capabilities (file I/O, shell)
- **MCP servers** for external integrations and optional capabilities (GitHub, Spotify, email)
- **MCP preferred** when the MCP server is more capable (Playwright MCP > building our own)
- **Never both** for the same capability in the same session

### 10.5 MEDIUM: Missing Effort Estimates & Timeline Reality

The plan says "Phase 1: 1-2 weeks" but doesn't account for:
- Guardrails framework (#438) — design + implement + test = 1 week minimum alone
- `FileIOToolsMixin` refactoring (§10.1) — 2-3 days
- MCP Server Manager UI — new Settings tab + API endpoints + state management = 1 week
- Testing all new tools on Windows + Linux = 3-5 days

**Realistic timeline:**
```
Phase 1 actual: 3-4 weeks (not 1-2)
Phase 1-MCP actual: 2-3 weeks (not concurrent with Phase 1)
Phase 2 actual: 3-4 weeks (VLM model loading is non-trivial)
```

### 10.6 LOW: Cross-Platform Testing Gaps

The plan's cross-platform table (§4) lists tools but doesn't mention:
- **CI/CD**: No GitHub Actions matrix for Windows/Linux/macOS testing
- **pyautogui on headless**: Won't work in CI without virtual display
- **macOS permissions**: Screenshot, accessibility, and automation all require explicit
  System Preferences permissions that can't be automated

---

## 11. User Workflow Validation Tests

Every major capability should have a **user workflow** — a concrete end-to-end
scenario that validates the capability works. These serve as acceptance criteria
and demo scripts.

### 11.1 File Operations Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| F1 | Create a file | "Create a file called hello.py with a hello world program" | File created, content shown in chat | `write_file` |
| F2 | Read & explain | "Read the file main.py and explain what it does" | File content shown, LLM explanation follows | `read_file` |
| F3 | Edit a file | "In config.json, change the port from 3000 to 8080" | Diff shown, file updated, confirmation | `edit_file` with guardrails |
| F4 | Search project | "Find all files that import 'fastapi'" | File list with line numbers | `search_code` |
| F5 | Organize files | "Create a 'docs' folder and move all .md files into it" | Directory created, files moved, summary | `run_shell_command` + guardrails |

### 11.2 Web & Search Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| W1 | Web search | "What are the latest AMD Ryzen AI specs?" | Search results summarized with sources | `search_web` (Brave/Perplexity) |
| W2 | Fetch URL | "Summarize this article: https://example.com/article" | Article content fetched, summarized | Fetch MCP → LLM summarize |
| W3 | Browse website | "Go to github.com/amd/gaia and tell me the latest release" | Page navigated, content extracted | Playwright MCP |
| W4 | Fill web form | "Fill out the contact form on example.com with my info" | Form fields identified, filled, screenshot shown | Playwright MCP + guardrails |

### 11.3 Document Analysis Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| D1 | Index & query | "Index all PDFs in ~/Documents and tell me about project deadlines" | Documents indexed, relevant chunks retrieved, answer synthesized | `index_directory` → `query_documents` |
| D2 | Summarize doc | "Summarize the Q4 report" | Multi-section summary with key findings | `summarize_document` |
| D3 | Compare docs | "Compare these two contracts and highlight differences" | Side-by-side comparison, key differences listed | `query_specific_file` × 2 → LLM compare |
| D4 | Extract data | "Extract all email addresses from this PDF" | Structured list of emails | `dump_document` → LLM extract |

### 11.4 Shell & System Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| S1 | Explore files | "What files are in my project directory?" | File tree displayed | `run_shell_command` (ls/dir) |
| S2 | Git status | "What's the git status of this repo?" | Status, branch, changes shown | `run_shell_command` (git status) |
| S3 | Find large files | "Find all files larger than 100MB on my desktop" | File list with sizes | `run_shell_command` (find/dir) |
| S4 | System info | "What GPU do I have and how much VRAM?" | GPU model, VRAM, driver info | `run_shell_command` (system queries) |

### 11.5 Email & Calendar Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| E1 | Email triage | "Summarize my unread emails" | Bulleted summary: sender, subject, action items | Gmail/Outlook MCP |
| E2 | Email search | "Find emails from Sarah about the budget proposal" | Filtered list with previews | Gmail MCP search |
| E3 | Draft reply | "Draft a polite reply declining the meeting invitation" | Draft shown, user confirms, email sent | Gmail MCP |
| E4 | Calendar check | "What's on my calendar today?" | Formatted schedule with times and details | Calendar MCP |
| E5 | Schedule meeting | "Schedule a 30-min sync with the team at 2pm tomorrow" | Event created, confirmation shown | Calendar MCP |

### 11.6 Browser & App Control Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| B1 | Web lookup | "Look up the Python docs for asyncio.gather" | Browser navigates, content extracted, answer in chat | Playwright MCP |
| B2 | Play music | "Play my Discover Weekly on Spotify" | Spotify starts playing, now-playing shown | Spotify MCP |
| B3 | Screenshot & describe | "Take a screenshot and tell me what's on my screen" | Screenshot captured, VLM description in chat | Screenshot tool + VLM |
| B4 | Open app | "Open VS Code with the gaia project" | VS Code launches with correct folder | `run_shell_command` (code .) |
| B5 | Fill web form | "Go to the HR portal and submit my timesheet" | Browser automation with step-by-step confirmation | Playwright MCP + guardrails |

### 11.7 Guardrails Validation Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| G1 | File write confirm | "Delete all .tmp files in my project" | Confirmation popup: "Delete 14 .tmp files?" → user approves | Guardrails → shell/file tools |
| G2 | Dangerous command | "Run rm -rf /tmp/old_builds" | Confirmation popup showing exact command, risk level | Guardrails → shell tools |
| G3 | Auto-approve | User clicks "Always allow" for `read_file` | Future `read_file` calls skip confirmation | Guardrails allow-list |
| G4 | Emergency stop | Agent starts doing something unexpected | Kill switch button stops all execution immediately | Cancellation (#439) |
| G5 | Bulk email | "Send this email to everyone in my contacts" | Hard block: "Bulk email operations require explicit approval for each recipient" | Guardrails escalation |

---

## 12. Updated MCP Server Catalog (Complete)

Adding the missing Tier 2+ servers identified in the critique:

### Tier 2+ — Communication & Productivity (NEW)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Gmail** | `gmail-mcp-server` | Email read, search, send, label, archive | Email |
| **Outlook** | `outlook-mcp-server` | Microsoft email via Graph API | Email |
| **Google Calendar** | `google-calendar-mcp` | Events, scheduling, availability | Calendar |
| **Outlook Calendar** | `outlook-calendar-mcp` | Microsoft calendar via Graph API | Calendar |
| **Nylas** | `nylas-mcp-server` | Unified email + calendar (multi-provider) | Email+Calendar |
| **Spotify** | `spotify-mcp-server` | Music playback, search, playlists | App Control |
| **Todoist** | `todoist-mcp-server` | Task management, projects, labels | Productivity |
| **Obsidian** | `obsidian-mcp-server` | Note-taking, knowledge base | Productivity |
| **Linear** | `linear-mcp` | Issue tracking, project management | Dev Tools |
| **Discord** | `discord-mcp-server` | Messaging, channel management | Communication |

---

## 13. New SDK Capabilities: MCP Auto-Discovery & SKILL.md Support

### 13.1 MCP Server Auto-Discovery & Installation

**Problem:** When a user asks the agent to do something it can't (e.g., "check my email"),
the agent currently says "I can't do that." A modern agent should be able to **find,
recommend, and install** the right MCP server to gain the capability.

**Design:**

```
User: "Check my email for anything urgent"
Agent: I don't have email access yet. I found these MCP servers that can help:
       1. Gmail MCP (gmail-mcp-server) — Gmail access
       2. Outlook MCP (outlook-mcp-server) — Outlook/Microsoft 365
       3. Nylas MCP (nylas-mcp-server) — Multi-provider (Gmail + Outlook + more)
       Would you like me to install one?
User: "Install Gmail MCP"
Agent: [installs via npx, prompts for OAuth/credentials, connects]
       Gmail MCP is now connected. You have 3 urgent emails...
```

**Implementation:**

| Component | Description | Milestone |
|-----------|-------------|-----------|
| **MCP Registry Client** | Query public MCP registries (npmjs.com, mcpservers.org, GitHub) to find servers by capability keyword | **B** (new SDK) |
| **Capability-to-MCP Mapper** | Map user intent ("email", "calendar", "spotify") to known MCP server packages | **A** (config/catalog, curated list) |
| **Auto-Install Flow** | `npx -y <package>` with user confirmation, credential prompting, connection test | **B** (new SDK) |
| **Fallback Search** | If curated catalog doesn't match, search npm/GitHub for `mcp-server-*` packages | **B** (new SDK) |
| **UI: Install Prompt** | Agent UI shows "Install MCP server?" card with description, permissions, confirm button | **A** (UI) |

**Curated Capability Map** (ships with GAIA):
```json
{
  "capabilities": {
    "email": ["gmail-mcp-server", "outlook-mcp-server", "nylas-mcp-server"],
    "calendar": ["google-calendar-mcp", "outlook-calendar-mcp"],
    "browser": ["@anthropic/mcp-playwright"],
    "web_search": ["@anthropic/mcp-brave-search"],
    "music": ["spotify-mcp-server"],
    "notes": ["obsidian-mcp-server"],
    "tasks": ["todoist-mcp-server"],
    "code": ["@modelcontextprotocol/server-github", "@modelcontextprotocol/server-git"],
    "files": ["@modelcontextprotocol/server-filesystem"],
    "database": ["@modelcontextprotocol/server-postgres"]
  }
}
```

### 13.2 Anthropic SKILL.md Support for GAIA Agent SDK

**What is SKILL.md?** Anthropic's specification for agents to document their learned
skills — reusable procedures, workflows, and domain knowledge that persist across
sessions. Skills are stored as markdown files that the agent can read, update, and
reference.

**Why it matters:** GAIA agents should be able to learn from experience. If a user
teaches the agent a multi-step workflow ("here's how to deploy our app"), that knowledge
should persist and be reusable.

**Design for GAIA:**

| Component | Description | Milestone |
|-----------|-------------|-----------|
| **Skills Directory** | `~/.gaia/skills/` directory for storing skill files | **B** |
| **Skill Loader** | At agent startup, load all `*.md` files from skills dir into context | **B** |
| **Skill Writer** | Tool: `save_skill(name, content)` — agent can persist learned workflows | **B** |
| **Skill Search** | Tool: `search_skills(query)` — find relevant skills for current task | **B** |
| **Skill Format** | Follow Anthropic's SKILL.md format: title, description, steps, prerequisites | **B** |
| **Skill UI** | Skills panel in Agent UI Settings — view, edit, delete, import/export skills | **A** (UI) |
| **Skill Sharing** | Export skills as `.md` files, import from community/team repositories | **B** |

**SKILL.md Format (Anthropic-compatible):**
```markdown
# Deploy GAIA Application

## Description
Steps to deploy the GAIA application to production.

## Prerequisites
- Docker installed
- Access to container registry
- `.env.production` file configured

## Steps
1. Run tests: `pytest tests/ -x`
2. Build Docker image: `docker build -t gaia:latest .`
3. Push to registry: `docker push registry.example.com/gaia:latest`
4. Deploy: `kubectl apply -f k8s/deployment.yaml`

## Learned
- Always run tests before building (learned 2026-03-01)
- Use `--no-cache` flag if dependencies changed (learned 2026-03-05)
```

**Integration with existing GAIA features:**
- Skills can reference RAG documents ("See indexed doc: architecture.pdf")
- Skills can reference MCP servers ("Requires: gmail-mcp-server")
- Skills can include tool sequences that the agent replays
- Skills directory is auto-indexed by RAG for semantic search

---

## 14. Summary: Recommended Priority Order (Revised, Split by Milestone)

### Milestone A — Agent UI: Wire Existing SDK (Weeks 1-6)

```
IMMEDIATE (This branch — kalin/chat-ui)
  ├── ✅ Windows shell compatibility fix (done)
  ├── ✅ Sidebar minimize + resize (done)
  └── ✅ Milestone + issues created (#438-#442)

WEEK 1-2: Foundation + MCP Framework
  ├── Add MCPClientMixin to ChatAgent
  ├── MCP Server Manager UI panel in Settings
  ├── Curated MCP server catalog (Tier 1: Playwright, Brave, Fetch, Filesystem, Git)
  ├── Refactor FileIOToolsMixin for graceful degradation (§10.1)
  ├── Conditional tool registration for ExternalToolsMixin (§10.3)
  └── Capability-to-MCP mapper (curated catalog — §13.1)

WEEK 3-4: Wire Existing Mixins + MCP Tier 1
  ├── Add FileIOToolsMixin to ChatAgent (file read/write/edit)
  ├── Add ProjectManagementMixin (list_files)
  ├── Add ExternalToolsMixin (web search, conditional)
  ├── Enable Playwright MCP (browser control)
  ├── Enable Brave Search MCP (web search, free)
  ├── Enable Fetch MCP (URL content extraction)
  └── Agent capabilities discovery API (#440)

WEEK 5-6: MCP Tier 2 + Productivity
  ├── MCP Tier 2 servers: Gmail, Outlook, Calendar, Spotify, Obsidian
  ├── MCP install prompt UI ("Install MCP server?" card)
  ├── Skills UI panel in Settings (view/manage SKILL.md files)
  ├── Tool argument streaming (#441)
  └── Per-session MCP server enable/disable
```

### Milestone B — GAIA Agent SDK: New Capabilities (Weeks 3-12+)

```
WEEK 3-5: Guardrails + Safety (PARALLEL with Milestone A)
  ├── Tool execution guardrails framework (#438) ← MUST BE FIRST
  │   ├── OutputHandler.confirm_tool_execution() API
  │   ├── SSE handler → frontend confirmation modal
  │   ├── threading.Event blocking pattern
  │   ├── Allow-list with localStorage persistence
  │   └── Risk classification (read/write/execute/destructive)
  ├── Cooperative execution cancellation (#439)
  └── Cross-platform shell compatibility (#442)

WEEK 5-7: Vision & Media
  ├── ScreenshotToolsMixin — cross-platform (PIL.ImageGrab, mss)
  ├── Wire VLMToolsMixin into ChatAgent (image analysis)
  ├── Image display in Agent UI (base64/file URL)
  ├── Screenshot → VLM → describe workflow
  └── Wire SDToolsMixin (image generation, optional)

WEEK 7-9: SDK Architecture
  ├── Tool categories + lazy loading (§10.2)
  ├── Tool description compression for prompts
  ├── MCP auto-discovery (search npm/GitHub for servers) — §13.1
  ├── SKILL.md support (load, save, search, format) — §13.2
  └── Skill-RAG integration (auto-index skills directory)

WEEK 9-12: Computer Use (CUA)
  ├── Windows Desktop MCP integration
  ├── Mouse/keyboard control (pyautogui) with mandatory guardrails
  ├── Window management (pywinauto/wmctrl)
  ├── VLM-based screen element detection
  └── CUA demo workflows (open apps, fill forms)

LATER: Audio/Voice
  ├── Voice input (Whisper ASR in Agent UI)
  ├── Voice output (Kokoro TTS in Agent UI)
  └── Continuous voice conversation mode
```

### Milestone Dependency Map

```
Milestone A (UI/Wiring)          Milestone B (SDK)
═══════════════════════          ═════════════════
Week 1: MCPClientMixin
Week 2: MCP Server UI            Week 3: Guardrails (#438) ← blocks write tools
Week 3: FileIOToolsMixin ───────────────→ needs guardrails for write ops
Week 4: Playwright MCP            Week 4: Cancellation (#439)
Week 5: Email/Calendar MCP        Week 5: ScreenshotToolsMixin
Week 6: Skills UI ────────────────Week 6: SKILL.md SDK support
                                  Week 7: Tool categories
                                  Week 8: MCP auto-discovery
                                  Week 9-12: Computer Use
```

**Key dependency:** `FileIOToolsMixin` (Milestone A, week 3) needs guardrails
(Milestone B, week 3) to be safe for write operations. These should be developed
in parallel with guardrails landing first or simultaneously.
