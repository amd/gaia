# GAIA Tray App — Integrated into Agent UI

> **Branch:** `kalin/chat-ui`
> **Date:** 2026-03-10
> **Prerequisite:** [Agent UI Agent Capabilities Plan](agent-ui-agent-capabilities-plan.md)
> **Supersedes:** `gaia5/docs/spec/os-agents-tray-app-milestone.md` (.NET WinForms approach)

---

## Overview

Integrate **system tray functionality** directly into the existing **GAIA Agent UI** Electron app (`src/gaia/apps/webui/`). Instead of building a separate .NET WinForms tray application, we extend the current Electron + React architecture to support:

- **Always-on system tray icon** with context menu
- **Agent process management** (start/stop/monitor OS agents)
- **Desktop notifications** and permission prompts
- **Agent terminal** (live stdout/stderr streaming)
- **Interactive agent chat** per agent
- **Background operation** (minimize to tray on close)

### Why Integrate Instead of Separate .NET App?

| Criterion | Separate .NET WinForms | Integrated Electron (chosen) |
|-----------|----------------------|------------------------------|
| Codebase reuse | None — new codebase | Full — reuse React components, stores, styles |
| MCP client | Must rebuild in C# | Already exists (`@amd-gaia/electron` MCPClient) |
| Subprocess mgmt | Must rebuild in C# | Already exists (`main.cjs` backend spawning) |
| Agent UI | Must rebuild in WinForms | Already exists (ChatView, MessageBubble, etc.) |
| Cross-platform | Windows only | Windows + macOS + Linux |
| Memory footprint | ~15-30 MB | ~80-120 MB (Chromium) but only ONE app instead of TWO |
| Development velocity | Slower (new stack, new team skills) | Faster (existing codebase, existing skills) |
| Maintenance cost | Two apps to maintain | One app to maintain |
| Startup time | <500ms | 2-3s (acceptable — it auto-starts and stays resident) |
| UI richness | Limited (WinForms) | Full React — markdown, syntax highlighting, charts |

**Decision:** The Agent UI is already an Electron app that runs alongside the user's workflow. Adding tray support is a natural extension. The memory overhead (~80-120 MB vs ~15-30 MB) is acceptable because:
1. Users already run the Agent UI — no additional memory cost
2. One app is simpler than two apps communicating
3. React UI is far more capable than WinForms for agent interaction
4. Electron's `Tray` API provides native system tray integration

> **Escape hatch:** If memory becomes a concern on low-end devices, we can later extract a minimal Electron tray-only app (~40 MB) that launches the full UI on demand. But start integrated.

---

## Known Risks & Mitigations

Issues identified during architecture review against the actual codebase. Each fix is incorporated into the relevant issue below.

### Critical

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| C1 | **Two main process entry points** — `main.cjs` (standalone installer) and `src/gaia/electron/src/main.js` (shared framework) are separate codebases. `main.cjs` does NOT use `AppController`, `WindowManager`, or the shared `MCPClient`. | New services placed in the shared framework won't be loadable from `main.cjs`. | **T0 prerequisite:** Refactor `main.cjs` to import services from the shared `@amd-gaia/electron` package, or co-locate tray services alongside `main.cjs` in `src/gaia/apps/webui/`. See Issue T0. |
| C2 | **No preload script** — `main.cjs` creates `BrowserWindow` with `contextIsolation: true` but no preload. `window.electronAPI` is undefined in the renderer. All IPC channels (`agent:*`, `tray:*`, `notification:*`) are dead on arrival. | Every React component that uses IPC will fail silently. | **T1 prerequisite:** Create `preload.cjs` alongside `main.cjs` that exposes IPC channels via `contextBridge`. See Issue T1. |
| C3 | **SIGTERM doesn't work on Windows** — `child_process.kill('SIGTERM')` on Windows sends `TerminateProcess` (immediate, ungraceful — equivalent to SIGKILL). C++ MCP agents cannot clean up. | Agents may leave zombie child processes, corrupt state, or lose in-flight data. | Define cross-platform shutdown protocol: (1) Send JSON-RPC `{"method": "shutdown"}` via stdin, (2) wait 5s for clean exit, (3) `process.kill()` as last resort. See Issue T2. |
| C4 | **`window-all-closed` kills the app** — `main.cjs:270-275` calls `cleanup()` (kills backend) then `app.quit()` on window close. Tray icon will flash and disappear. | Minimize-to-tray is impossible without changing this handler. | Intercept `mainWindow.on('close')` with `event.preventDefault()` + `window.hide()`. Make `window-all-closed` a no-op when tray mode is active. See Issue T1. |

### Significant

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| S1 | **MCP `initialize` as heartbeat** — Spec used `{"method": "initialize"}` every 30s. MCP's `initialize` is a one-time handshake; re-sending it may reset agent state or be rejected. | Agents may drop sessions, re-initialize tools, or return errors on duplicate init. | Use `{"method": "ping"}` (MCP standard) for health checks. See Issue T2. |
| S2 | **Config path inconsistency** — Spec used `%LOCALAPPDATA%\GAIA\` but the Python backend stores everything in `~/.gaia/` (`%USERPROFILE%\.gaia\`). Two locations = confused users and code. | Agent configs, permissions, and chat history disconnected from existing GAIA data. | Use `~/.gaia/` for all config. Specifically: `~/.gaia/tray-config.json`, `~/.gaia/agents/`, `~/.gaia/permissions.json`, `~/.gaia/agent-chat/`. See all issues. |
| S3 | **ChatView transport coupling** — `ChatView` is tightly coupled to HTTP SSE (via `sendMessageStream()` in `api.ts`). Agent chat uses IPC → stdio JSON-RPC. Modifying ChatView to support both transports is a significant refactor. | Risk of breaking existing chat when adding agent chat transport. | `AgentChat` imports `MessageBubble` directly — does NOT wrap or modify `ChatView`. Own message send/receive logic over IPC. Less coupling, no risk to existing chat. See Issue T6. |
| S4 | **Zustand `Map` serialization** — `agentStore` and `terminalStore` use `Map<string, T>`. Zustand devtools and persist middleware don't serialize Maps. | Store state invisible in devtools; persist middleware silently drops Map data. | Use `Record<string, T>` instead of `Map<string, T>` in all store definitions. See Issues T3, T4. |
| S5 | **Windows toast notifications lack action buttons** — Electron's `Notification` API on Windows does not support custom action buttons. "Approve/Deny" on a toast is not possible without `electron-windows-notifications` (WinRT bindings). | Permission prompts cannot be answered from the toast notification on Windows. | OS native toasts are click-to-focus only ("Process Intel needs your attention — click to respond"). Actual Approve/Deny happens in the in-app `PermissionPrompt` modal. See Issue T5. |

### Minor

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| M1 | T2 false dependency on T1 | Blocks parallel work | T2 blocked by: nothing. T3 blocked by: T1, T2. |
| M2 | Agent manifest is Windows-only (`"binary": "process_mcp.exe"`) | No macOS/Linux support | Platform binary map in manifest. See Issue T2. |
| M3 | "Stderr" tab label is developer jargon | Confuses non-developer users | Rename to "Activity / Logs / Raw". See Issue T4. |
| M4 | No first-run empty state wireframe | Poor first impression when no agents installed | Add empty state UX. See Issue T3. |
| M5 | No `electron-forge` config changes documented | Tray icons missing from packaged builds | Add `extraResource` for assets. See Issue T12. |
| M6 | No accessibility (ARIA, keyboard nav, focus management) | Fails accessibility standards | Follow existing `aria-label`/`aria-hidden` patterns. See all UI issues. |

---

## Issue T0: Main Process Unification (Prerequisite)

**Priority:** p0 | **Labels:** `electron`, `architecture`

The webui currently has two separate main process entry points that share no code:
- `src/gaia/apps/webui/main.cjs` — Self-contained (packaged installer)
- `src/gaia/electron/src/main.js` — Shared framework (`AppController`, `WindowManager`, `MCPClient`)

**Problem:** `main.cjs` duplicates subprocess management, window creation, and health checking without using the shared framework. New tray services cannot be shared between them.

**Resolution:** Refactor `main.cjs` to:
1. Import and use `AppController` from `@amd-gaia/electron` for window + IPC management
2. Keep self-contained backend spawning (since `main.js` doesn't do this)
3. Add a `tray-manager.js` service that both entry points can consume

**Alternatively** (simpler): Co-locate all new tray services in `src/gaia/apps/webui/services/` alongside `main.cjs`, making them self-contained to the webui app. The shared `@amd-gaia/electron` framework stays untouched until a second app (e.g., JAX) also needs tray support.

**Recommendation:** Start with the simpler co-location approach. Extract to shared framework later if needed.

**Modified files:**
```
src/gaia/apps/webui/
├── main.cjs                           # Refactor to use services/
├── preload.cjs                        # NEW — contextBridge for IPC (see C2)
├── services/
│   ├── tray-manager.js                # NEW (was in shared framework)
│   ├── agent-process-manager.js       # NEW (was in shared framework)
│   ├── agent-registry.js              # NEW
│   ├── agent-health-checker.js        # NEW
│   └── notification-service.js        # NEW
```

**Blocked by:** Nothing (start immediately, before T1)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  GAIA Agent UI (Electron)                                       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Main Process (main.cjs / tray-manager.cjs)              │   │
│  │                                                           │   │
│  │  ┌────────────┐  ┌──────────────┐  ┌─────────────────┐  │   │
│  │  │ Electron   │  │ Agent Process│  │ Notification     │  │   │
│  │  │ Tray +     │  │ Manager      │  │ Service          │  │   │
│  │  │ Context    │  │ (spawn/kill/ │  │ (Windows toast + │  │   │
│  │  │ Menu       │  │  health)     │  │  Electron notif) │  │   │
│  │  └─────┬──────┘  └──────┬───────┘  └────────┬────────┘  │   │
│  │        │                │                    │            │   │
│  │  ┌─────▼────────────────▼────────────────────▼────────┐  │   │
│  │  │  IPC Bridge (ipcMain ↔ ipcRenderer)                │  │   │
│  │  │  Channels: agent:*, tray:*, notification:*         │  │   │
│  │  └──────────────────────┬─────────────────────────────┘  │   │
│  └─────────────────────────┼────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────▼────────────────────────────────┐   │
│  │  Renderer Process (React SPA)                             │   │
│  │                                                           │   │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │   │
│  │  │ Chat    │ │ Agent    │ │ Agent    │ │ Notification │  │   │
│  │  │ View    │ │ Manager  │ │ Terminal │ │ Center       │  │   │
│  │  │(existing)│ │ Panel    │ │ View     │ │              │  │   │
│  │  └─────────┘ └──────────┘ └──────────┘ └─────────────┘  │   │
│  │                                                           │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │  Stores (Zustand)                                   │  │   │
│  │  │  agentStore | notificationStore | terminalStore     │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │  Managed Agent Processes (subprocesses)                    │   │
│  │                                                            │   │
│  │  gaia chat --ui (Python backend) ← already managed        │   │
│  │  process_mcp.exe    ← OS agent (C++)                      │   │
│  │  network_mcp.exe    ← OS agent (C++)                      │   │
│  │  gaming_mcp.exe     ← OS agent (C++)                      │   │
│  │  GaiaOS.Security.exe ← OS agent (.NET)                    │   │
│  │                                                            │   │
│  │  Communication:                                            │   │
│  │  ├── stdout → JSON-RPC 2.0 (MCP protocol + GAIA exts)    │   │
│  │  ├── stderr → Structured logs → Terminal View              │   │
│  │  └── HTTP → FastAPI backend (port 4200) for Agent UI      │   │
│  └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Issues

### Issue T1: System Tray Integration — Tray Icon, Context Menu, Minimize-to-Tray

**Priority:** p0 | **Labels:** `electron`, `tray`, `gui`

Add Electron `Tray` support to the existing Agent UI so it persists in the system tray.

**Modified files:**
```
src/gaia/apps/webui/
├── main.cjs                        # Add tray lifecycle, fix window-all-closed (existing)
├── preload.cjs                     # NEW — contextBridge exposing IPC channels
├── services/
│   └── tray-manager.js             # NEW — Electron Tray + context menu manager
```

> **Critical prerequisite (C2):** `main.cjs` currently creates `BrowserWindow` with `contextIsolation: true` but NO preload script. All IPC channels defined in T2-T5 require a preload to work.

**New file: `preload.cjs`**
```javascript
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gaiaAPI', {
  // Agent process management (T2)
  agent: {
    start: (id) => ipcRenderer.invoke('agent:start', id),
    stop: (id) => ipcRenderer.invoke('agent:stop', id),
    restart: (id) => ipcRenderer.invoke('agent:restart', id),
    status: (id) => ipcRenderer.invoke('agent:status', id),
    statusAll: () => ipcRenderer.invoke('agent:status-all'),
    sendRpc: (id, method, params) => ipcRenderer.invoke('agent:send-rpc', id, method, params),
    onStdout: (cb) => ipcRenderer.on('agent:stdout', (_, data) => cb(data)),
    onStderr: (cb) => ipcRenderer.on('agent:stderr', (_, data) => cb(data)),
    onCrashed: (cb) => ipcRenderer.on('agent:crashed', (_, data) => cb(data)),
  },
  // Tray (T1)
  tray: {
    getConfig: () => ipcRenderer.invoke('tray:get-config'),
    setConfig: (cfg) => ipcRenderer.invoke('tray:set-config', cfg),
  },
  // Notifications (T5)
  notification: {
    onPermissionRequest: (cb) => ipcRenderer.on('notification:permission-request', (_, data) => cb(data)),
    respondPermission: (id, action, remember) => ipcRenderer.invoke('notification:respond', id, action, remember),
    onNotification: (cb) => ipcRenderer.on('notification:new', (_, data) => cb(data)),
  },
});
```

**Wire preload in `main.cjs`:** (line ~167)
```javascript
webPreferences: {
  nodeIntegration: false,
  contextIsolation: true,
  preload: path.join(__dirname, 'preload.cjs'),  // ← ADD THIS
},
```

> **Critical fix (C4):** The current `window-all-closed` handler at `main.cjs:270-275` calls `cleanup()` + `app.quit()`, which kills the backend and exits. This must change for tray mode.

**Required `main.cjs` changes for minimize-to-tray:**
```javascript
let isQuitting = false;
let minimizeToTray = true; // loaded from tray-config.json

// Intercept window close — hide instead of closing
mainWindow.on('close', (event) => {
  if (minimizeToTray && !isQuitting) {
    event.preventDefault();
    mainWindow.hide();
  }
});

// Don't quit when window is hidden (tray keeps app alive)
app.on('window-all-closed', () => {
  // No-op when tray is active — app stays running via Tray
  if (!minimizeToTray) {
    cleanup();
    app.quit();
  }
});

// Set isQuitting flag when user actually quits (via tray menu "Quit")
app.on('before-quit', () => {
  isQuitting = true;
});
```

**New file: `tray-manager.js`**

Responsibilities:
- Create `Tray` instance with GAIA icon on app startup
- Build and update context menu dynamically (agent list, status indicators)
- Handle "minimize to tray" on window close (configurable)
- Handle "show window" on tray icon click/double-click
- Animate tray icon when agents are active (swap between `gaia-tray.png` and `gaia-tray-active.png`)
- Expose IPC handlers for renderer to query/update tray state

**Context menu structure:**
```
GAIA Agent UI
├── Show Window                    → BrowserWindow.show()
├── ── (separator) ──
├── Chat Agent             ● Running    ► [Stop] [Terminal]
├── Process Intelligence   ○ Stopped    ► [Start] [Terminal]
├── Network Intelligence   ◌ Not Installed
├── ── (separator) ──
├── Start All Enabled
├── Stop All
├── ── (separator) ──
├── Notifications (3)              → Focus notification panel in UI
├── Settings                       → Focus settings in UI
├── ── (separator) ──
├── About GAIA
└── Quit                           → app.quit() (stops all agents)
```

**Behavior:**
- App starts → tray icon appears + main window opens
- User closes window → window hides, tray icon remains (configurable: can change to "quit on close")
- User clicks tray icon → window shows and focuses
- Right-click tray icon → context menu
- "Quit" → gracefully stops all managed agents, then exits
- On Windows: tray icon in system tray area (taskbar)
- On macOS: menu bar icon
- On Linux: system tray (AppIndicator)

**Assets needed:**
```
src/gaia/apps/webui/assets/
├── tray-icon.png           # 16x16 tray icon (Windows/Linux)
├── tray-icon@2x.png        # 32x32 tray icon (HiDPI)
├── tray-icon-active.png    # Active state (agents running)
├── tray-icon-active@2x.png # Active state HiDPI
├── tray-icon.ico           # Windows .ico format
└── tray-iconTemplate.png   # macOS template image (for dark/light menu bar)
```

**Settings (added to Settings modal):**
```
Tray Behavior
├── ☑ Minimize to tray on close (default: on)
├── ☑ Start minimized to tray (default: off)
├── ☑ Start on Windows login (default: off)
└── ☑ Show notification count badge (default: on)
```

**Windows auto-start:**
- Registry key: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- Value: `"GAIA Agent UI"="<install_path>\GAIA Agent UI.exe" --minimized`
- Set/remove via Electron `app.setLoginItemSettings()`

**Packaging (electron-forge):**
- Add tray icon assets to `forge.config.cjs` `extraResource` so they're included in packaged builds
- Add `preload.cjs` to `files` array in `package.json`

**Accessibility:**
- Tray context menu items must have accessible labels
- Settings toggles need `aria-label` and `role="switch"` attributes

**Blocked by:** T0 (main process unification)

---

### Issue T2: Agent Process Manager — Start, Stop, Monitor Agent Subprocesses

**Priority:** p0 | **Labels:** `electron`, `tray`, `agents`

Manage OS agent processes (C++ MCP servers, .NET agents) as subprocesses of the Electron app, using the same pattern already used for the Python backend.

**New files:**
```
src/gaia/apps/webui/services/
├── agent-process-manager.js   # Spawn/kill/monitor agent processes
├── agent-registry.js          # Installed agent inventory + manifest
└── agent-health-checker.js    # Periodic health pings via JSON-RPC
```

> **Note:** Services co-located with `main.cjs` per T0 decision. Not in shared `@amd-gaia/electron` framework.

**Agent manifest format (`agent-manifest.json`):**

```json
{
  "manifest_version": 1,
  "agents": [
    {
      "id": "process-intelligence",
      "name": "Process Intelligence",
      "description": "Monitor and manage system processes, detect anomalies",
      "version": "1.0.0",
      "binaries": {
        "win32": "process_mcp.exe",
        "darwin": "process_mcp",
        "linux": "process_mcp"
      },
      "language": "cpp",
      "download_urls": {
        "win32": "https://github.com/amd/gaia/releases/download/os-agents-v1.0.0/process_mcp-win64.exe",
        "darwin": "https://github.com/amd/gaia/releases/download/os-agents-v1.0.0/process_mcp-darwin",
        "linux": "https://github.com/amd/gaia/releases/download/os-agents-v1.0.0/process_mcp-linux"
      },
      "sha256": {
        "win32": "abc123...",
        "darwin": "def456...",
        "linux": "ghi789..."
      },
      "size_bytes": 4404019,
      "tools_count": 18,
      "categories": ["system", "performance"],
      "requires_admin": false,
      "capabilities": {
        "standalone_mode": true,
        "notifications": true,
        "interactive_chat": true
      }
    }
  ]
}
```

> **Fix (M2):** Manifest uses platform-keyed objects (`win32`/`darwin`/`linux`) for binary names, download URLs, and checksums. The installer selects the correct entry based on `process.platform`.

**`agent-process-manager.js` API:**

```javascript
class AgentProcessManager {
  // Lifecycle
  startAgent(agentId)          // Spawn process with --stdio, redirect I/O
  stopAgent(agentId)           // Graceful shutdown (see cross-platform protocol below)
  restartAgent(agentId)        // Stop + start

  // Monitoring
  getAgentStatus(agentId)      // { running, pid, uptime, memoryMB }
  getAllAgentStatuses()         // Map<agentId, status>
  onAgentCrash(agentId, cb)    // Process.on('exit') handler

  // I/O
  getStdoutStream(agentId)     // JSON-RPC messages (parsed)
  getStderrStream(agentId)     // Log lines (raw text)
  sendJsonRpc(agentId, method, params)  // Send JSON-RPC request

  // Bulk
  startAllEnabled()            // Start agents marked auto-start
  stopAll()                    // Stop all running agents
}
```

**Process communication:**
- `stdout` → JSON-RPC 2.0 only (MCP protocol + GAIA extensions)
- `stderr` → Structured log lines → piped to terminal view
- Health check: send `{ "jsonrpc": "2.0", "method": "ping", "id": 1 }` every 30s

> **Fix (S1):** Use MCP standard `ping` method, NOT `initialize`. The `initialize` method is a one-time handshake — re-sending it may reset agent state or be rejected by MCP-compliant servers.

**Cross-platform shutdown protocol (Fix C3):**

`SIGTERM` does NOT work on Windows — `child_process.kill('SIGTERM')` sends `TerminateProcess` (immediate, ungraceful). Instead, use a JSON-RPC shutdown protocol that works on all platforms:

```
stopAgent(agentId):
  1. Send JSON-RPC {"method": "shutdown", "id": "shutdown-1"} via stdin
  2. Wait up to 5 seconds for process to exit cleanly
  3. If still running after 5s: process.kill() (TerminateProcess on Windows, SIGKILL on Unix)
  4. Emit 'agent:stopped' event
```

This allows C++ and .NET agents to flush state, close file handles, and clean up child processes before exiting.

**IPC channels (main ↔ renderer):**
```
agent:start         (agentId) → void
agent:stop          (agentId) → void
agent:restart       (agentId) → void
agent:status        (agentId) → AgentStatus
agent:status-all    () → Map<string, AgentStatus>
agent:stdout        (agentId) → stream of JSON-RPC messages
agent:stderr        (agentId) → stream of log lines
agent:send-rpc      (agentId, method, params) → JSON-RPC response
agent:crashed       (agentId, exitCode, signal) → event
```

**Auto-start on app launch:**
- Read `tray-config.json` for agents marked `autoStart: true`
- Start them sequentially (100ms delay between each to avoid resource spike)
- Show notification if any agent fails to start

**Crash recovery:**
- On `process.exit`, check `restartOnCrash` config per agent
- If enabled: restart after 2s delay, max 3 retries in 60s
- Show crash notification (T5) with "Restart" / "View Terminal" actions
- Log crash to `~/.gaia/crash-log.json`

**Config persistence: `~/.gaia/tray-config.json`**

> **Fix (S2):** All config files use `~/.gaia/` to match the existing Python backend (which stores data in `~/.gaia/chat/`, `~/.gaia/file_index.db`, etc.). Do NOT use `%LOCALAPPDATA%\GAIA\` — that creates a second config location.
```json
{
  "agents": {
    "process-intelligence": {
      "autoStart": true,
      "restartOnCrash": true,
      "logLevel": "info"
    }
  },
  "tray": {
    "minimizeToTray": true,
    "startMinimized": false,
    "startOnLogin": false
  }
}
```

**Blocked by:** Nothing (can run in parallel with T1)

> **Fix (M1):** Agent process management is independent of tray icon rendering. T2 can start immediately. Only T3 (Agent Manager UI) depends on both T1 and T2.

---

### Issue T3: Agent Manager Panel — UI for Agent Discovery, Install, Configure

**Priority:** p0 | **Labels:** `react`, `tray`, `gui`

React panel in the Agent UI sidebar for managing OS agents — view installed agents, install new ones, start/stop, and configure.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── AgentManager.tsx           # Agent list + detail panel
│   ├── AgentManager.css
│   ├── AgentCard.tsx              # Per-agent card (status, actions)
│   └── AgentConfigDialog.tsx      # Per-agent settings modal
├── stores/
│   └── agentStore.ts              # Zustand store for agent state
├── types/
│   └── agent.ts                   # AgentInfo, AgentStatus types
```

**UI design (integrated into existing sidebar):**
```
┌──────────────────────┬─────────────────────────────────────┐
│  GAIA Agent UI       │                                     │
│  ─────────────────   │  Agent Manager                      │
│                      │                                     │
│  💬 Chat             │  ┌─────────────────────────────┐   │
│  📁 Files            │  │ ● Process Intelligence       │   │
│  📚 Documents        │  │   Running · PID 4892 · 8 MB  │   │
│  ─────────────────   │  │   18 tools · Uptime: 2h 34m  │   │
│  🤖 Agents     ←NEW  │  │   [Stop] [Terminal] [Chat]    │   │
│  ─────────────────   │  └─────────────────────────────┘   │
│  ⚙ Settings          │  ┌─────────────────────────────┐   │
│                      │  │ ○ Network Intelligence       │   │
│                      │  │   Stopped                     │   │
│                      │  │   12 tools                    │   │
│                      │  │   [Start] [Terminal] [Config]  │   │
│                      │  └─────────────────────────────┘   │
│                      │  ┌─────────────────────────────┐   │
│                      │  │ ◌ Gaming Optimization        │   │
│                      │  │   Not installed · 3.2 MB      │   │
│                      │  │   [Install]                    │   │
│                      │  └─────────────────────────────┘   │
│                      │                                     │
│                      │  [Start All] [Stop All] [Refresh]   │
└──────────────────────┴─────────────────────────────────────┘
```

**Zustand store (`agentStore.ts`):**
```typescript
interface AgentInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  binaries: Record<string, string>;  // platform → binary name
  toolsCount: number;
  categories: string[];
  requiresAdmin: boolean;
  capabilities: {
    standaloneMode: boolean;
    notifications: boolean;
    interactiveChat: boolean;
  };
}

interface AgentStatus {
  installed: boolean;
  running: boolean;
  pid?: number;
  uptime?: number;         // seconds
  memoryMB?: number;
  lastHealthCheck?: number; // timestamp
  healthy?: boolean;
}

interface AgentStore {
  agents: Record<string, AgentInfo>;     // Fix (S4): Record, not Map
  statuses: Record<string, AgentStatus>; // Fix (S4): Record, not Map

  // Actions
  fetchManifest(): Promise<void>;
  startAgent(id: string): Promise<void>;
  stopAgent(id: string): Promise<void>;
  installAgent(id: string): Promise<void>;
  uninstallAgent(id: string): Promise<void>;
}
```

> **Fix (S4):** Use `Record<string, T>` instead of `Map<string, T>`. Zustand's devtools and persist middleware don't serialize `Map` correctly — store state becomes invisible in devtools and persist silently drops data.

**Install flow (UI):**
1. User clicks "Install" on an agent card
2. Progress bar shows download progress
3. SHA-256 verification (show checkmark or error)
4. Agent appears as "Stopped" with [Start] button
5. Toast notification: "Process Intelligence installed successfully"

**Agent config dialog:**
```
┌────────────────────────────────────────────┐
│  Configure: Process Intelligence     [×]    │
├────────────────────────────────────────────┤
│                                            │
│  Auto-start with GAIA          [✓]        │
│  Restart on crash              [✓]        │
│  Log level            [Info ▼]             │
│                                            │
│  Tools: 18 registered                      │
│  ├── 🟢 Auto (12): list_processes, ...    │
│  ├── 🟡 Confirm (5): kill_process, ...    │
│  └── 🔴 Escalate (1): format_drive        │
│                                            │
│  Version: 1.0.0                            │
│  Binary: process_mcp.exe (4.2 MB)         │
│  Location: ~/.gaia/agents/...              │
│                                            │
│           [Save]    [Cancel]               │
└────────────────────────────────────────────┘
```

**Empty state (first-run UX) — Fix (M4):**

When no agents are installed, the Agents panel shows:
```
┌─────────────────────────────────────┐
│                                     │
│         🤖                          │
│                                     │
│  No agents installed yet            │
│                                     │
│  Agents extend GAIA with system     │
│  monitoring, gaming optimization,   │
│  network intelligence, and more.    │
│  They run locally on your AMD       │
│  hardware.                          │
│                                     │
│  [Browse Available Agents]          │
│                                     │
└─────────────────────────────────────┘
```

**Accessibility (M6):**
- Agent cards: `role="article"`, `aria-label="Process Intelligence, running"`
- Action buttons: `aria-label="Stop Process Intelligence agent"`
- Config dialog: focus trap, `Escape` to close, `aria-modal="true"`

**Blocked by:** T1, T2

---

### Issue T4: Agent Terminal View — Live Console Output

**Priority:** p0 | **Labels:** `react`, `tray`, `gui`

React component for viewing real-time stdout/stderr from a running agent. Integrated as a panel within the Agent UI, not a separate window.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── AgentTerminal.tsx          # Terminal output view
│   ├── AgentTerminal.css
│   └── TerminalLine.tsx           # Single log line with ANSI color support
├── stores/
│   └── terminalStore.ts           # Zustand store for terminal buffers
```

**UI design:**
```
┌──────────────────────────────────────────────────────┐
│  Process Intelligence — Terminal               [×]   │
├──────────────────────────────────────────────────────┤
│  [Activity] [Logs] [Raw]            🔍 [Filter...]  │
├──────────────────────────────────────────────────────┤
│  12:34:01  INFO   Agent started (PID 4892)           │
│  12:34:01  INFO   18 tools registered                │
│  12:34:02  TOOL   list_processes → 142 processes     │
│  12:34:05  TOOL   get_process_detail(chrome) → ok    │
│  12:34:08  WARN   High CPU: chrome.exe (89%)         │
│  12:34:10  TOOL   kill_process(7234) → 🟡 CONFIRM   │
│  12:34:10  PERM   Waiting for user confirmation...   │
│  12:34:15  PERM   User approved kill_process(7234)   │
│  12:34:15  TOOL   kill_process(7234) → success       │
│  12:34:18  INFO   Anomaly scan complete: 0 threats   │
│                                                      │
│  █                                                   │
├──────────────────────────────────────────────────────┤
│  [Clear] [Export] [Pause]  Auto-scroll ✓   Lines: 42│
└──────────────────────────────────────────────────────┘
```

**Tabs:**
- **Activity** — Parsed, human-friendly view: tool calls, results, errors, permission prompts (parsed from both stdout JSON-RPC and stderr logs)
- **Logs** — Raw stderr output (log lines)
- **Raw** — Raw stdout messages (MCP JSON-RPC protocol) for debugging

> **Fix (M3):** Renamed from "Stderr"/"JSON-RPC" — developer jargon that confuses non-developer users.

**Features:**
- Virtual scrolling (react-window or similar) for performance with 10K+ lines
- Circular buffer: keep last 10,000 lines per agent in memory
- ANSI color code parsing for colored output
- Text filter: real-time regex/text search across visible lines
- Pause/resume auto-scroll without losing new data
- Export: save visible buffer as `.log` file
- Click-to-expand: click a tool call line to see full arguments + response

**Terminal store (`terminalStore.ts`):**
```typescript
interface TerminalStore {
  buffers: Record<string, TerminalLine[]>;   // agentId → lines (Fix S4: Record, not Map)
  filters: Record<string, string>;            // agentId → filter text
  paused: Record<string, boolean>;            // agentId → paused

  appendLine(agentId: string, line: TerminalLine): void;
  clearBuffer(agentId: string): void;
  setFilter(agentId: string, filter: string): void;
  togglePause(agentId: string): void;
}
```

**IPC integration:**
- Listen on `agent:stderr` IPC channel for raw log lines
- Listen on `agent:stdout` IPC channel for parsed JSON-RPC messages
- Both streams are buffered in the terminalStore

**Blocked by:** T2

---

### Issue T5: Notification System — Toasts, Permission Prompts, Notification Center

**Priority:** p0 | **Labels:** `electron`, `react`, `tray`, `gui`

Desktop notifications for agent events: permission requests, security alerts, status changes, and errors. Combines Electron native notifications with in-app notification center.

**New files:**
```
src/gaia/apps/webui/services/
├── notification-service.js        # Route agent notifications to OS + renderer

src/gaia/apps/webui/src/
├── components/
│   ├── NotificationCenter.tsx     # In-app notification list
│   ├── NotificationCenter.css
│   ├── NotificationToast.tsx      # In-app toast popup
│   ├── PermissionPrompt.tsx       # Modal for permission requests
│   └── PermissionPrompt.css
├── stores/
│   └── notificationStore.ts       # Zustand store for notifications
```

**Notification types and display:**

| Type | In-App | OS Native | Sound | Example |
|------|--------|-----------|-------|---------|
| `permission_request` | Modal dialog (blocks action) | Click-to-focus toast | Yes | "Process Intel needs your attention" |
| `security_alert` | Toast + notification center | Click-to-focus toast | Yes | "Unknown process: cryptominer.exe" |
| `status_change` | Toast (auto-dismiss 5s) | Optional | No | "Gaming agent activated Game Mode" |
| `info` | Notification center only | None | No | "Daily security scan: 0 threats" |
| `error` | Toast (persistent until dismissed) | Click-to-focus toast | Yes | "Network agent crashed" |

> **Fix (S5):** Electron's `Notification` API on Windows does NOT support custom action buttons (e.g., "Approve/Deny") in toast notifications. That requires `electron-windows-notifications` with WinRT bindings — heavy and fragile. Instead, all OS native toasts are **click-to-focus only**: clicking the toast shows the main window and focuses the relevant panel (Permission Prompt modal, Terminal, or Notification Center). The actual interaction (Approve/Deny, Restart, etc.) happens **in-app**, which is cross-platform and fully controllable.

**Permission prompt UI (React modal):**
```
┌────────────────────────────────────────────┐
│  ⚠ Permission Required                     │
├────────────────────────────────────────────┤
│                                            │
│  Process Intelligence wants to:            │
│                                            │
│  🟡 kill_process                           │
│                                            │
│  Target: chrome.exe (PID 7234)             │
│  Reason: "Process consuming 89% CPU for    │
│           over 5 minutes"                  │
│                                            │
│  ☐ Remember this choice for this session   │
│  ☐ Always allow this tool (promote to 🟢)  │
│                                            │
│       [Allow]    [Deny]    [View Details]  │
└────────────────────────────────────────────┘
```

**JSON-RPC protocol (agent → tray):**

Agent sends notification via stdout:
```json
{
  "jsonrpc": "2.0",
  "method": "notification/send",
  "params": {
    "type": "permission_request",
    "agent_id": "process-intelligence",
    "title": "Kill Process Request",
    "message": "Process consuming 89% CPU for over 5 minutes",
    "tool": "kill_process",
    "tool_args": {"pid": 7234, "process_name": "chrome.exe"},
    "actions": ["allow", "deny"],
    "timeout_seconds": 30
  }
}
```

Tray responds via stdin:
```json
{
  "jsonrpc": "2.0",
  "method": "notification/response",
  "params": {
    "notification_id": "notif-001",
    "action": "allow",
    "remember": false
  }
}
```

**Electron native notifications:**
- Use `new Notification({ title, body, icon, actions })` for OS-level toasts
- Windows: Windows 10/11 toast notifications (Action Center)
- macOS: Notification Center
- Linux: libnotify
- Click notification → show + focus main window on relevant panel

**Notification center (in-app):**
```
┌──────────────────────────────────────────────────────┐
│  Notifications                                 [×]   │
├──────────────────────────────────────────────────────┤
│  🔴 12:34 — Process Intelligence                    │
│     Permission: kill_process(chrome.exe)  [Approve]  │
│                                                      │
│  🟠 12:33 — Security Agent                          │
│     New unknown process: suspicious.exe              │
│                                                      │
│  🟢 12:30 — Gaming Agent                            │
│     Game Mode activated for Steam                    │
│                                                      │
│  🔵 12:00 — System                                  │
│     Daily security scan complete: 0 threats          │
├──────────────────────────────────────────────────────┤
│  [Mark All Read] [Clear]                             │
└──────────────────────────────────────────────────────┘
```

**Notification badge:**
- Tray icon shows unread count overlay (Electron `tray.setTitle()` on macOS, icon overlay on Windows)
- Sidebar "Agents" tab shows notification count badge

**Blocked by:** T1, T2

---

### Issue T6: Interactive Agent Chat — Per-Agent Conversation

**Priority:** p1 | **Labels:** `react`, `tray`, `gui`

Enable direct conversation with individual OS agents. When a user clicks "Chat" on an agent card, a chat interface opens that communicates with that specific agent via JSON-RPC.

> **Fix (S3):** `AgentChat` is a **standalone component** that imports `MessageBubble` directly — it does NOT wrap or modify `ChatView`. The existing `ChatView` is tightly coupled to HTTP SSE transport (via `sendMessageStream()` in `api.ts`), and modifying it to support a second transport (IPC → stdio) risks breaking the existing chat. Instead, `AgentChat` has its own message send/receive logic over IPC, reusing only the presentational `MessageBubble` component.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── AgentChat.tsx              # NEW — standalone chat using MessageBubble + IPC transport
│   └── AgentChat.css
├── stores/
│   └── agentChatStore.ts         # NEW — separate store for agent chat sessions
```

**NOT modified:** `ChatView.tsx`, `chatStore.ts` — these remain untouched.

**How it works:**

1. User clicks "Chat" on an agent card
2. `AgentChat` component opens with that agent's ID
3. User types a message
4. Message sent via `window.gaiaAPI.agent.sendRpc(agentId, "agent/chat", { message })`
5. Agent responds via JSON-RPC on stdout: `agent/chat_response`
6. Response rendered using `MessageBubble` (reusing markdown rendering, syntax highlighting)

**Agent chat protocol (JSON-RPC):**
```json
// User → Agent
{
  "jsonrpc": "2.0",
  "method": "agent/chat",
  "id": "msg-001",
  "params": {
    "message": "What's using the most memory right now?",
    "context": "interactive_session"
  }
}

// Agent → User (streamed via notifications or single response)
{
  "jsonrpc": "2.0",
  "result": {
    "message": "Top memory consumers:\n1. chrome.exe — 1.2 GB...",
    "tool_calls": [
      {
        "tool": "list_processes",
        "args": {"sort_by": "memory"},
        "result_summary": "142 processes returned"
      }
    ]
  },
  "id": "msg-001"
}
```

**Quick actions:**
- Per-agent configurable quick action buttons below the input
- Process Intel: [Status] [Top Processes] [Security Scan]
- Gaming: [Game Mode On] [Performance Profile] [FPS Monitor]
- Network: [Active Connections] [Bandwidth] [Block IP]

**Session persistence:**
- Conversation history stored in `~/.gaia/agent-chat/{agentId}.json`
- Configurable retention (default: 100 messages per agent)

**Blocked by:** T2, T3

---

### Issue T7: Agent Marketplace — Download & Install Agents

**Priority:** p1 | **Labels:** `electron`, `react`, `tray`

Agent discovery, download, verification, and installation. Extends the Agent Manager panel with install capabilities.

**New files:**
```
src/gaia/apps/webui/services/
├── agent-installer.js             # Download, verify SHA-256, extract, register
├── agent-manifest-fetcher.js      # Fetch remote manifest from GitHub Releases
└── update-checker.js              # Check for agent updates

src/gaia/apps/webui/src/components/
├── AgentInstallDialog.tsx         # Install progress modal
└── AgentInstallDialog.css
```

**Install flow:**
1. Fetch `agent-manifest.json` from GitHub Releases (or local dev path)
2. Show available agents with descriptions, sizes, categories in Agent Manager
3. User clicks "Install" → download dialog with progress bar
4. Download binary to temp → verify SHA-256 → move to `~/.gaia/agents/{id}/`
5. Register in local agent registry
6. Toast: "✅ Process Intelligence installed"

**Update flow:**
- Periodic check on app startup (configurable: daily/weekly/manual)
- Badge on agent card: "Update available: v1.0.0 → v1.1.0"
- One-click update: stop agent → download → verify → replace → restart

**Security:**
- SHA-256 verification on every download
- Only download from configured URLs (default: GitHub Releases)
- Binary signature verification (future: Windows Authenticode)

**Blocked by:** T2, T3

---

### Issue T8: Permission Management UI

**Priority:** p1 | **Labels:** `react`, `tray`, `security`

UI for viewing and managing tool permission tiers (Auto/Confirm/Escalate) per agent.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── PermissionManager.tsx      # Permission override table
│   └── PermissionManager.css
```

**Integrated into Agent Config Dialog (T3):**
```
Tools & Permissions
┌─────────────────────────────────────────────────────┐
│  Tool                  │ Default │ Override │ Action │
├────────────────────────┼─────────┼──────────┼────────┤
│  list_processes        │   🟢    │ (default)│        │
│  get_process_detail    │   🟢    │ (default)│        │
│  kill_process          │   🟡    │ (default)│ [🟢]   │
│  set_priority          │   🟢    │   🟡     │ [Reset]│
│  quarantine_executable │   🟡    │   🟢     │ [Reset]│
│  format_drive          │   🔴    │ (locked) │        │
└────────────────────────┴─────────┴──────────┴────────┘
```

**Features:**
- Show all tools per agent with their default permission tier
- Allow user to promote (🟡→🟢) or demote (🟢→🟡) tool permissions
- 🔴 Escalate tools cannot be changed (always require escalation)
- Overrides persist to `~/.gaia/permissions.json`
- "Reset All" button to revert to defaults

**Blocked by:** T3, T5

---

### Issue T9: Audit Log Viewer — Action History with Rollback

**Priority:** p2 | **Labels:** `react`, `tray`, `security`

View all actions taken by agents with ability to undo reversible actions.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── AuditLog.tsx               # Action history table
│   └── AuditLog.css
├── stores/
│   └── auditStore.ts              # Zustand store for audit entries
```

**UI:**
```
┌──────────────────────────────────────────────────────┐
│  Action History                                       │
├──────────┬──────────────┬──────┬────────┬─────┬──────┤
│ Time     │ Agent        │ Tool │ Tier   │ OK? │ Undo │
├──────────┼──────────────┼──────┼────────┼─────┼──────┤
│ 12:34:01 │ Process      │ kill │ 🟡     │ ✅  │      │
│ 12:34:45 │ Network      │ block│ 🟢     │ ✅  │ [↩]  │
│ 12:35:12 │ Storage      │ clean│ 🟡     │ ✅  │ [↩]  │
│ 12:36:00 │ Security     │ scan │ 🟢     │ ✅  │      │
├──────────┴──────────────┴──────┴────────┴─────┴──────┤
│ Filter: [All Agents ▼] [All Tiers ▼] [Today ▼]      │
│ [Export CSV]                                          │
└──────────────────────────────────────────────────────┘
```

**Data source:** Each agent maintains an audit log (SQLite or JSON). The viewer aggregates across all agents via JSON-RPC `audit/list` calls.

**Rollback:** For reversible actions, the "Undo" button calls the agent's `rollback_action` tool with the action ID.

**Blocked by:** T2, T3

---

### Issue T10: System Dashboard — Real-Time OS Overview

**Priority:** p2 | **Labels:** `react`, `tray`, `gui`

Live system overview panel aggregating data from running OS agents.

**New files:**
```
src/gaia/apps/webui/src/
├── components/
│   ├── SystemDashboard.tsx        # System overview with gauges
│   ├── SystemDashboard.css
│   ├── MetricGauge.tsx            # Circular gauge (CPU, RAM, GPU)
│   └── ProcessTable.tsx           # Top processes by resource usage
```

**UI:**
```
┌──────────┬──────────┬──────────┬────────────────────┐
│  CPU     │  RAM     │  GPU     │  Disk I/O          │
│  [45%]   │  [68%]   │  [12%]   │  R: 45 MB/s        │
├──────────┴──────────┴──────────┴────────────────────┤
│  Top Processes                                       │
│  chrome.exe     1.2 GB   34%   34 tabs              │
│  teams.exe      890 MB   12%                        │
│  code.exe       654 MB    8%                        │
├──────────────────────────────────────────────────────┤
│  Temperature            │  Network                   │
│  CPU: 62°C  GPU: 48°C  │  ↓ 23.4 Mbps  ↑ 2.1 Mbps │
│  SSD: 38°C  Fan: 1200  │  Connections: 287          │
└─────────────────────────┴────────────────────────────┘
```

**Data sources:** Calls OS agent tools via JSON-RPC:
- Process agent: `list_processes`, `get_resource_usage`
- Thermal agent: `get_thermal_status`
- Network agent: `list_connections`, `get_bandwidth`
- Storage agent: `get_disk_usage`

**Polling:** 1s interval for metrics, 5s for process list

**Blocked by:** T2, T3, OS agents being available

---

### Issue T11: Windows Auto-Start & Login Integration

**Priority:** p1 | **Labels:** `electron`, `tray`

Windows integration for startup, minimized launch, and system hooks.

**Implementation:**
```javascript
// In main.cjs
const { app } = require('electron');

// Set login item
app.setLoginItemSettings({
  openAtLogin: true,
  path: app.getPath('exe'),
  args: ['--minimized']
});
```

**Features:**
- Register/unregister from Windows startup via Settings toggle
- `--minimized` flag: start with window hidden, tray icon only
- Appear in Windows Settings → Startup Apps
- Jump list entries (right-click taskbar): "New Chat", "Agent Manager", "Quit"

**Blocked by:** T1

---

### Issue T12: Testing — Electron + React Component Tests

**Priority:** p1 | **Labels:** `tests`, `electron`, `react`

Test suite for all new tray, agent management, and notification components.

**New files:**
```
src/gaia/apps/webui/
├── __tests__/
│   ├── tray-manager.test.js           # Electron tray lifecycle
│   ├── agent-process-manager.test.js  # Process spawn/kill/health
│   ├── agent-registry.test.js         # Manifest parsing
│   ├── agent-installer.test.js        # Download + verify + install
│   ├── notification-service.test.js   # Notification routing
│   ├── AgentManager.test.tsx          # React component tests
│   ├── AgentTerminal.test.tsx         # Terminal rendering
│   ├── NotificationCenter.test.tsx    # Notification display
│   ├── PermissionPrompt.test.tsx      # Permission modal
│   └── AgentChat.test.tsx             # Agent interaction
```

**CI workflow addition to existing `.github/workflows/`:**
```yaml
tray-tests:
  runs-on: windows-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: '20' }
    - run: cd src/gaia/apps/webui && npm ci
    - run: cd src/gaia/apps/webui && npm test
```

**Test approach:**
- React components: Jest + React Testing Library
- Electron main process: Jest with mocked Electron APIs
- IPC integration: Mock `ipcMain`/`ipcRenderer` bridges
- Agent process manager: Mock `child_process.spawn`

**Blocked by:** T1-T8

---

## Implementation Order

```
Phase 0 — Prerequisites (1 week)
└── T0: Main Process Unification (co-locate services, add preload.cjs)

Phase 1 — Foundation (2 weeks)
├── T1: System Tray Integration (tray icon, context menu, minimize-to-tray)
├── T2: Agent Process Manager (subprocess lifecycle, I/O streaming) — PARALLEL with T1
└── T11: Windows Auto-Start

Phase 2 — Core UI (2 weeks)
├── T3: Agent Manager Panel (list, status, start/stop, install)
├── T4: Agent Terminal View (live stdout/stderr)
└── T5: Notification System (toasts, permission prompts)

Phase 3 — Interaction & Marketplace (2 weeks)
├── T6: Interactive Agent Chat (per-agent conversation, standalone component)
├── T7: Agent Marketplace (download, verify, install)
└── T8: Permission Management UI

Phase 4 — Advanced & Testing (1-2 weeks)
├── T9: Audit Log Viewer
├── T10: System Dashboard
└── T12: Testing
```

**Total estimate: 8-9 weeks with a single developer.**
Phase 0+1+2 deliver a usable product; Phase 3+4 are polish.

---

## File Layout Summary

```
src/gaia/apps/webui/
├── main.cjs                           # T0/T1: Refactored main process
├── preload.cjs                        # T0: contextBridge for IPC channels
├── services/                          # T0: Co-located main process services
│   ├── tray-manager.js                # T1: Electron Tray lifecycle
│   ├── agent-process-manager.js       # T2: Spawn/kill/monitor agents
│   ├── agent-registry.js              # T2: Installed agent inventory
│   ├── agent-health-checker.js        # T2: Periodic health pings (ping, not initialize)
│   ├── agent-installer.js             # T7: Download + verify + extract
│   ├── agent-manifest-fetcher.js      # T7: Fetch remote manifest
│   ├── update-checker.js              # T7: Check for updates
│   └── notification-service.js        # T5: Route notifications to OS + renderer
├── src/
│   ├── components/
│   │   ├── AgentManager.tsx           # T3: Agent list + actions
│   │   ├── AgentManager.css
│   │   ├── AgentCard.tsx              # T3: Per-agent card
│   │   ├── AgentConfigDialog.tsx      # T3: Per-agent settings
│   │   ├── AgentTerminal.tsx          # T4: Live console view
│   │   ├── AgentTerminal.css
│   │   ├── TerminalLine.tsx           # T4: ANSI-colored log line
│   │   ├── NotificationCenter.tsx     # T5: Notification list
│   │   ├── NotificationCenter.css
│   │   ├── NotificationToast.tsx      # T5: In-app toast
│   │   ├── PermissionPrompt.tsx       # T5: Permission modal (in-app, not OS toast)
│   │   ├── PermissionPrompt.css
│   │   ├── AgentChat.tsx              # T6: Standalone agent chat (uses MessageBubble)
│   │   ├── AgentChat.css
│   │   ├── AgentInstallDialog.tsx     # T7: Install progress
│   │   ├── PermissionManager.tsx      # T8: Permission overrides
│   │   ├── AuditLog.tsx               # T9: Action history
│   │   ├── AuditLog.css
│   │   ├── SystemDashboard.tsx        # T10: System overview
│   │   ├── SystemDashboard.css
│   │   ├── MetricGauge.tsx            # T10: Circular gauge
│   │   └── ProcessTable.tsx           # T10: Top processes
│   ├── stores/
│   │   ├── agentStore.ts              # T3: Agent state (Record, not Map)
│   │   ├── agentChatStore.ts          # T6: Agent chat sessions (separate from chatStore)
│   │   ├── terminalStore.ts           # T4: Terminal buffers (Record, not Map)
│   │   ├── notificationStore.ts       # T5: Notifications
│   │   └── auditStore.ts              # T9: Audit entries
│   └── types/
│       └── agent.ts                   # Shared agent types
├── assets/
│   ├── tray-icon.png                  # T1
│   ├── tray-icon@2x.png
│   ├── tray-icon-active.png
│   ├── tray-icon-active@2x.png
│   ├── tray-icon.ico
│   └── tray-iconTemplate.png         # macOS
└── __tests__/                         # T12: All tests

Config files (all under ~/.gaia/):
├── tray-config.json                   # Tray + agent auto-start settings
├── permissions.json                   # Tool permission overrides
├── crash-log.json                     # Agent crash history
├── agents/                            # Installed agent binaries
│   └── {agent-id}/
│       └── {binary}
└── agent-chat/                        # Per-agent conversation history
    └── {agent-id}.json
```

---

## Key Design Decisions

1. **Integrated Electron, not separate .NET app** — One app to build, deploy, and maintain. Reuses existing React components, MCP client, subprocess management. Cross-platform.

2. **Tray is an extension, not a replacement** — The Agent UI window remains the primary interface. Tray adds "always-on" capability and quick access. Users who don't want tray behavior can disable it in settings.

3. **AgentChat is standalone, not a ChatView wrapper** — `AgentChat` imports `MessageBubble` directly for rendering but has its own IPC-based message transport. `ChatView` and `chatStore` remain untouched — no risk of breaking existing HTTP SSE chat. *(Revised from original "reuse ChatView" approach after discovering tight HTTP transport coupling.)*

4. **Same JSON-RPC protocol** — Agents communicate with the tray via the same MCP protocol extensions (`notification/send`, `agent/chat`). No new protocol needed. Defined in the OS Agents MCP milestone.

5. **Progressive disclosure** — If no OS agents are installed, the "Agents" sidebar item shows a simple "Install your first agent" prompt. Tray context menu shows only "Chat Agent" (the Python backend). Complexity appears only when agents are added.

6. **Config stored in `~/.gaia/`** — All agent configs, permissions, and chat history stored alongside the existing Python backend data. Single config location, not two. Nothing sent to any server.

7. **Graceful degradation** — If Electron tray API is unavailable (rare Linux configurations), the app works normally as a windowed app. Tray features are optional.

8. **Cross-platform shutdown, not SIGTERM** — Agent shutdown uses JSON-RPC `{"method": "shutdown"}` via stdin, not OS signals. This works on Windows (where SIGTERM is unavailable) and allows agents to clean up gracefully.

9. **OS notifications are click-to-focus only** — Electron's native notifications on Windows lack action buttons. All interactive prompts (Approve/Deny, Restart, etc.) happen in-app. OS toasts just bring the window to focus.

---

## Relationship to Other Specs

| Spec | Relationship |
|------|-------------|
| [Agent UI Agent Capabilities Plan](agent-ui-agent-capabilities-plan.md) | This spec adds "Agents" panel to the UI built in that plan |
| `gaia5/os-agents-mcp-milestone.md` | OS agents (C++/.NET) are what this tray app manages |
| `gaia5/os-agents-tray-app-milestone.md` | **Superseded** — that spec proposed .NET WinForms; this spec integrates into Electron instead |
| [electron-integration.mdx](electron-integration.mdx) | This spec extends the existing Electron framework documented there |

---

## Issue Summary

| ID | Title | Priority | Labels | Blocked By | Review Fixes |
|----|-------|----------|--------|------------|--------------|
| T0 | Main Process Unification | p0 | electron, architecture | — | C1, C2 |
| T1 | System Tray Integration | p0 | electron, tray, gui | T0 | C2, C4, M5 |
| T2 | Agent Process Manager | p0 | electron, tray, agents | — | C3, S1, S2, M1, M2 |
| T3 | Agent Manager Panel | p0 | react, tray, gui | T1, T2 | S4, M4, M6 |
| T4 | Agent Terminal View | p0 | react, tray, gui | T2 | S4, M3 |
| T5 | Notification System | p0 | electron, react, tray, gui | T1, T2 | S5 |
| T6 | Interactive Agent Chat | p1 | react, tray, gui | T2, T3 | S3, S2 |
| T7 | Agent Marketplace | p1 | electron, react, tray | T2, T3 | S2 |
| T8 | Permission Management UI | p1 | react, tray, security | T3, T5 | S2 |
| T9 | Audit Log Viewer | p2 | react, tray, security | T2, T3 | — |
| T10 | System Dashboard | p2 | react, tray, gui | T2, T3 | — |
| T11 | Windows Auto-Start | p1 | electron, tray | T1 | — |
| T12 | Testing | p1 | tests, electron, react | T1-T8 | M5 |

**Total: 13 issues (T0-T12)**

### Review Fixes Applied

All 15 findings from the architecture review have been incorporated:
- **4 critical** (C1-C4): main process unification, preload script, Windows shutdown protocol, window-all-closed handler
- **5 significant** (S1-S5): ping healthcheck, ~/.gaia/ config path, standalone AgentChat, Record types, click-to-focus notifications
- **6 minor** (M1-M6): T2 unblocked, platform manifests, tab labels, empty state, forge config, accessibility
