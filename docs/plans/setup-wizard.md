# Setup Wizard and Onboarding Plan

> **Date:** 2026-04-01
>
> **Status:** Draft
>
> **Milestones:** v0.17.2 (first-run detection) -> v0.20.0 (system scanner, wizard UI) -> v0.24.0 (progressive install, guided task)
>
> **Goal:** First useful AI interaction in under 5 minutes from install.

---

## 1. Overview

GAIA targets three user personas with very different technical baselines:

| Persona | Comfort Level | What They Need |
|---------|---------------|----------------|
| **Privacy Paul** | Power user, has CLI experience | Skip wizard, `gaia init --profile code --yes` |
| **Curious Carla** | Non-technical, first time with local AI | Full wizard with visual feedback and guidance |
| **Enterprise Eric** | Technical but compliance-focused | Audit-ready setup, model provenance, offline mode |

The setup wizard must satisfy all three. The primary success metric is **time from first
launch to first useful response** -- target is under 5 minutes on a broadband connection
with an AMD Ryzen AI system.

### Design Principles

1. **Never show a blank screen.** Every state (loading, downloading, error) has visible feedback.
2. **Every step is skippable.** Power users can bail out at any point.
3. **Resume, never restart.** Interrupted downloads pick up where they left off.
4. **One shared state file.** CLI and UI wizard read/write the same `~/.gaia/setup-state.json`.
5. **Hardware-aware defaults.** Model recommendations adapt to detected RAM, disk, and NPU.

---

## 2. First-Run Detection

### Trigger Logic

```
On any `gaia` CLI command OR Electron app launch:
  1. Check for ~/.gaia/initialized          (file marker)
  2. Check for ~/.gaia/setup-state.json     (partial setup in progress)
  3. If neither exists -> trigger onboarding
  4. If setup-state.json exists but initialized does not -> resume onboarding
  5. If initialized exists -> skip onboarding, proceed normally
```

### State File: `~/.gaia/setup-state.json`

```json
{
  "version": 1,
  "started_at": "2026-04-01T12:00:00Z",
  "completed_at": null,
  "current_step": "model_selection",
  "system_scan": {
    "os": "windows",
    "os_version": "11 Pro 10.0.26200",
    "arch": "x86_64",
    "ram_gb": 32,
    "disk_free_gb": 120,
    "amd_gpu": "Radeon 780M",
    "amd_npu": "Ryzen AI",
    "npu_detected": true,
    "node_version": "20.11.0",
    "python_version": "3.11.8",
    "lemonade_installed": true,
    "lemonade_version": "9.2.0",
    "lemonade_running": false
  },
  "selected_profile": "chat",
  "models_downloaded": ["Qwen3-0.6B-GGUF"],
  "models_pending": ["Qwen3-Coder-30B-A3B-Instruct-GGUF", "nomic-embed-text-v1.5-GGUF"],
  "skipped_steps": [],
  "error_log": []
}
```

### CLI Behavior

```
$ gaia chat
  [First run detected]
  Welcome to GAIA! Run `gaia init` to set up, or pass --skip-setup to proceed manually.

$ gaia init
  [Launches CLI onboarding wizard]

$ gaia init --profile chat --yes
  [Silent mode -- no prompts, uses defaults for the chat profile]
```

### Electron Behavior

On first Electron launch, the main window renders the onboarding wizard instead of the
chat view. The wizard is a series of full-screen steps within the existing Electron shell.
After completion, the window transitions to the normal chat UI.

**Related issues:** #469 (first-run detection), #597 (Agent UI wizard)

---

## 3. System Scanner

The system scanner runs as Step 1 of the wizard and produces the `system_scan` block in
the state file. It must complete in under 5 seconds.

### Detection Matrix

| Check | Windows | Linux | macOS | Method |
|-------|---------|-------|-------|--------|
| OS version | `ver` / registry | `uname -r` / `/etc/os-release` | `sw_vers` | `platform` stdlib |
| RAM | `wmic` / `GlobalMemoryStatusEx` | `/proc/meminfo` | `sysctl hw.memsize` | `psutil.virtual_memory()` |
| Free disk | `shutil.disk_usage()` | same | same | stdlib |
| AMD GPU | `wmic path win32_VideoController` | `lspci` | `system_profiler SPDisplaysDataType` | subprocess |
| AMD NPU | `lspci` / device enumeration | `lspci` | N/A (no NPU on macOS) | AMD driver query |
| Node.js | `node --version` | same | same | `shutil.which` + subprocess |
| Python | `sys.version` | same | same | stdlib |
| Lemonade installed | `lemonade-server --version` | same | same | `shutil.which` + subprocess |
| Lemonade running | HTTP GET `http://localhost:8399/api/version` | same | same | `requests.get` with 2s timeout |
| Models available | Lemonade `/api/models` endpoint | same | same | HTTP GET |
| Disk write speed | Write 10 MB temp file, measure time | same | same | `tempfile` + `time` |

### Hardware Tier Classification

Based on scan results, classify the system into a hardware tier that drives model
recommendations:

| Tier | RAM | NPU | GPU | Recommended Profile |
|------|-----|-----|-----|-------------------|
| **Tier 1: Full** | 32+ GB | Yes | Discrete or iGPU | `chat` or `code` (30B model) |
| **Tier 2: Standard** | 16-31 GB | Yes | iGPU | `chat` (8B model) |
| **Tier 3: Light** | 8-15 GB | Optional | Any | `minimal` (0.6B model) |
| **Tier 4: Insufficient** | <8 GB | -- | -- | Warning: may not run well |

### Scanner Output (CLI)

```
System Scan
-----------
OS:            Windows 11 Pro (10.0.26200)
Architecture:  x86_64
RAM:           32 GB                           [OK]
Free disk:     120 GB                          [OK]
AMD GPU:       Radeon 780M (integrated)        [OK]
AMD NPU:       Ryzen AI                        [OK]
Node.js:       v20.11.0                        [OK]
Lemonade:      v9.2.0 (installed, not running) [WARN]
Models:        None downloaded yet             [--]

Hardware tier: Tier 1 (Full)
Recommended profile: chat (~25 GB download)
```

**Related issues:** #466 (system scanner)

---

## 4. Onboarding Wizard Flow

The wizard consists of 7 steps. Steps 5 and 6 are optional and can be skipped.

```
[1] Welcome -> [2] System Scan -> [3] Model Selection -> [4] Model Download
    -> [5] Messaging Setup (optional) -> [6] Persona Interview (optional)
    -> [7] Guided First Task -> [DONE] Setup Complete
```

### Step 1: Welcome

Purpose: Value proposition and set expectations.

```
+------------------------------------------------------------------+
|                                                                    |
|                     Welcome to GAIA                                |
|                                                                    |
|  Your private AI assistant that runs 100% locally.                 |
|  No data ever leaves your device.                                  |
|                                                                    |
|  What we will do:                                                  |
|    1. Scan your hardware                          (~5 seconds)     |
|    2. Download an AI model                        (~3 minutes)     |
|    3. Run your first query                        (~30 seconds)    |
|                                                                    |
|  Total estimated time: under 5 minutes.                            |
|                                                                    |
|                                                                    |
|  [Get Started]                   [Skip -- I know what I'm doing]   |
|                                                                    |
+------------------------------------------------------------------+
```

**Skip behavior:** Jumps to normal chat UI. Writes `~/.gaia/initialized` with
`skipped: true` so the user is never prompted again but can run `gaia init` manually.

### Step 2: System Scan Results

Purpose: Show what was detected, flag any problems, build user confidence.

```
+------------------------------------------------------------------+
|                                                                    |
|  System Scan Results                                               |
|                                                                    |
|  [OK]   AMD Ryzen 9 7945HX with Ryzen AI NPU                     |
|  [OK]   32 GB RAM (recommended: 16+ GB)                           |
|  [OK]   120 GB free disk (need: 25 GB for chat profile)           |
|  [OK]   Lemonade Server v9.2.0 installed                          |
|  [WARN] Lemonade Server is not running (will start automatically) |
|                                                                    |
|  Hardware Tier: Full                                               |
|  You can run the most capable models locally.                      |
|                                                                    |
|  [Continue]                                          [Re-scan]     |
|                                                                    |
+------------------------------------------------------------------+
```

**Error states handled here:**

| Condition | UI Treatment |
|-----------|-------------|
| RAM < 8 GB | Warning banner: "Your system has limited RAM. We recommend the minimal profile (0.6B model)." |
| Disk < 5 GB free | Blocking error: "Not enough disk space. Free up space and re-scan." |
| Lemonade not installed | Action button: "Install Lemonade Server" (triggers download) |
| No AMD GPU/NPU | Info banner: "No AMD accelerator detected. GAIA will use CPU inference (slower)." |
| No internet | Info banner: "No internet detected. Connect to download models, or point to local model files." |

### Step 3: Model Selection

Purpose: Let user choose what to download. Defaults are pre-selected based on hardware tier.

```
+------------------------------------------------------------------+
|                                                                    |
|  Choose Your Setup                                                 |
|                                                                    |
|  Based on your hardware (Tier 1: Full), we recommend:              |
|                                                                    |
|  (*) Chat        Interactive chat + document Q&A     ~25 GB        |
|      Models: Qwen3-Coder-30B, Nomic-Embed-Text                    |
|                                                                    |
|  ( ) Code        Autonomous coding assistant         ~18 GB        |
|      Models: Qwen3-Coder-30B                                      |
|                                                                    |
|  ( ) Minimal     Lightweight quick-start             ~400 MB       |
|      Models: Qwen3-0.6B                                           |
|                                                                    |
|  ( ) All         Every model for every agent         ~26 GB        |
|      Models: Qwen3-Coder-30B, Nomic-Embed, Qwen3-VL-4B           |
|                                                                    |
|  Estimated download time: ~3 min at 100 Mbps                      |
|                                                                    |
|  [Download Selected]                                 [Skip Models] |
|                                                                    |
+------------------------------------------------------------------+
```

**Profile-to-model mapping** reuses the existing `INIT_PROFILES` dict from
`src/gaia/installer/init_command.py` (lines 39-100). No duplication -- the wizard reads
the same profile definitions the CLI uses.

### Step 4: Model Download

Purpose: Download models with clear progress and the ability to resume on failure.

```
+------------------------------------------------------------------+
|                                                                    |
|  Downloading Models                                                |
|                                                                    |
|  Qwen3-Coder-30B-A3B-Instruct-GGUF                               |
|  [===================>          ]  62%   11.2 / 18.0 GB            |
|  Speed: 45 MB/s    ETA: 2 min 30 sec                              |
|                                                                    |
|  Nomic-Embed-Text-v1.5-GGUF                        [Queued]       |
|                                                                    |
|  Starting Lemonade Server...                        [Waiting]      |
|                                                                    |
|                                                                    |
|  [Cancel]                                                          |
|                                                                    |
+------------------------------------------------------------------+
```

**Download implementation details:**

- Downloads use HTTP range requests for resume-on-failure.
- Each model file is downloaded to `~/.gaia/downloads/` with a `.part` suffix, renamed on completion.
- SHA256 checksum verification after each download.
- Parallel downloads when bandwidth allows (configurable, default: 1 concurrent download).
- Progress is written to `setup-state.json` after each chunk so resume works after crash or reboot.
- If Lemonade Server is not running, it is started automatically after the first model finishes.

**Error handling:**

| Error | Recovery |
|-------|----------|
| Network drop mid-download | Pause, retry 3 times with exponential backoff, then prompt user |
| Disk full during download | Stop, show "Free X GB and retry" with a [Retry] button |
| Checksum mismatch | Delete corrupt file, re-download from scratch |
| Lemonade fails to start | Show error log snippet, link to troubleshooting docs |

### Step 5: Messaging Platform Setup (Optional, deferred to v0.24.0+)

Purpose: Connect GAIA to Discord, Slack, or Telegram for remote access.

This step is skipped by default in the initial release. The wizard shows:

```
+------------------------------------------------------------------+
|                                                                    |
|  Messaging Integrations (Optional)                                 |
|                                                                    |
|  Connect GAIA to your messaging platforms for remote access.       |
|  You can set this up later in Settings.                            |
|                                                                    |
|  [ ] Discord     Not configured                                   |
|  [ ] Slack       Not configured                                   |
|  [ ] Telegram    Not configured                                   |
|                                                                    |
|  [Configure Later]                                   [Set Up Now]  |
|                                                                    |
+------------------------------------------------------------------+
```

**Related issues:** Messaging integrations plan (`docs/plans/messaging-integrations-plan.md`)

### Step 6: Persona Interview (Optional)

Purpose: Tailor the experience to the user's goals. Feeds into system prompt and
suggested first tasks.

```
+------------------------------------------------------------------+
|                                                                    |
|  What will you use GAIA for? (select all that apply)               |
|                                                                    |
|  [x] Chat with AI privately                                       |
|  [ ] Ask questions about my documents (PDFs, code, notes)         |
|  [ ] Generate or edit code                                        |
|  [ ] Automate tasks (Jira, Blender, Docker)                       |
|  [ ] Voice interaction                                            |
|  [ ] Just exploring                                                |
|                                                                    |
|  [Continue]                                          [Skip]        |
|                                                                    |
+------------------------------------------------------------------+
```

The selections are stored in `~/.gaia/preferences.json` and used to:

1. Customize the guided first task (Step 7).
2. Set a tailored system prompt for the default chat session.
3. Pre-enable relevant MCP servers if applicable.

**Related issues:** #467 (onboarding agent / persona interview)

### Step 7: Guided First Task

Purpose: Get the user to their first successful AI interaction immediately.

Based on persona selections and profile, present a context-aware starter prompt:

```
+------------------------------------------------------------------+
|                                                                    |
|  Setup Complete -- Try Your First Task                             |
|                                                                    |
|  Everything is ready. Here are some things you can try:            |
|                                                                    |
|  > "Summarize this PDF for me"          [drag a file here]        |
|                                                                    |
|  > "Write a Python function that sorts a list of dictionaries     |
|     by a given key"                                                |
|                                                                    |
|  > "What can you help me with?"                                   |
|                                                                    |
|  Or type your own question below:                                  |
|                                                                    |
|  +--------------------------------------------------------------+ |
|  |                                                              | |
|  +--------------------------------------------------------------+ |
|  [Send]                                                            |
|                                                                    |
+------------------------------------------------------------------+
```

The guided first task view transitions seamlessly into the normal chat UI. The message
input is the same component used in regular chat -- the user is already "in" the app.

**Related issues:** #471 (guided first task)

---

## 5. Error States -- Full Reference

| Error | When | CLI Behavior | UI Behavior |
|-------|------|-------------|-------------|
| Lemonade not installed | System scan | Print install instructions, offer `gaia init` auto-install | Show "Install Lemonade" button |
| Lemonade wrong version | System scan | Print upgrade command | Show "Upgrade Lemonade" button |
| Lemonade won't start | Post-download | Print stderr, suggest port conflict check | Show log viewer with retry button |
| RAM < 8 GB | System scan | Warn, suggest minimal profile | Warning banner, pre-select minimal |
| Disk < 5 GB | System scan | Block with error message | Blocking modal, re-scan button |
| Download failed (network) | Model download | Retry with backoff, then abort with resume hint | Retry button, show partial progress |
| Download failed (disk full) | Model download | Abort, print space needed | Modal: "Free X GB", retry button |
| Checksum mismatch | Model download | Delete + re-download | Auto-retry once, then manual retry |
| Model load failed | Verification | Print Lemonade error, suggest `gaia init --force-models` | Show error detail, offer re-download |
| Node.js missing | System scan (Electron only) | N/A (CLI does not need Node) | Prompt to install Node.js |
| Python < 3.10 | System scan | Block, print version requirement | Block, link to Python download |

---

## 6. Skip / Power-User Path

Every wizard step includes a skip option. Power users have three fast paths:

### Path A: CLI Silent Mode

```bash
gaia init --profile chat --yes
```

Runs the full init sequence non-interactively. Uses defaults for hardware tier.
Prints progress to stdout. Returns exit code 0 on success.

### Path B: Skip Button in UI

The "Skip -- I know what I'm doing" button on the Welcome screen writes the
`~/.gaia/initialized` marker and jumps directly to the chat UI. The user can then
configure everything manually through Settings.

### Path C: Environment Variable

```bash
export GAIA_SKIP_ONBOARDING=1
gaia chat
```

Permanently suppresses the onboarding prompt. Useful for CI, Docker containers,
and automated testing.

---

## 7. Progressive Installation

### Resume After Interruption

The `setup-state.json` file tracks progress at a granular level:

```
1. User starts wizard, reaches Step 4 (downloading Qwen3-Coder-30B at 62%)
2. User closes laptop / loses power / kills process
3. On next launch, GAIA reads setup-state.json
4. Finds current_step = "model_download", models_downloaded = [], partial file exists
5. Resumes download from byte offset using HTTP Range header
6. Continues wizard from Step 4
```

### Rollback on Failure

If a step fails and cannot be recovered:

1. The failed step is logged in `setup-state.json.error_log`.
2. Previously completed steps are NOT rolled back (models already downloaded stay).
3. The user is offered: [Retry This Step] or [Skip and Continue].
4. If the failure is in Lemonade installation, the cleanup removes partially extracted
   files but preserves any existing working installation.

### Parallel Downloads

When the user selects a profile that requires multiple models (e.g., `chat` needs both
a language model and an embedding model):

- Download the primary model first (it is needed to verify the setup works).
- Queue secondary models to download in the background after the primary completes.
- In future versions (v0.24.0+), allow true parallel downloads with bandwidth sharing.

**Related issues:** #468 (setup executor with resume, rollback, parallel downloads)

---

## 8. CLI vs UI Onboarding

Both CLI and UI onboarding share the same backend logic and the same state file.

### Shared Components

| Component | Location | Used By |
|-----------|----------|---------|
| `InitCommand` | `src/gaia/installer/init_command.py` | CLI, UI (via API) |
| `LemonadeInstaller` | `src/gaia/installer/lemonade_installer.py` | CLI, UI (via API) |
| `INIT_PROFILES` | `src/gaia/installer/init_command.py` | CLI, UI (via API) |
| `setup-state.json` | `~/.gaia/setup-state.json` | CLI, UI (shared state) |
| System scanner | `src/gaia/installer/system_scanner.py` (new) | CLI, UI (via API) |

### CLI-Specific (`gaia init`)

- Rich terminal output with progress bars (via `rich` library).
- Interactive prompts for profile selection and confirmations.
- `--yes` flag for non-interactive mode.
- `--profile` flag to skip profile selection.
- `--skip-models` and `--skip-lemonade` for partial setup.

### UI-Specific (Electron wizard)

- Full-screen wizard steps rendered in the Electron window.
- API calls to a local onboarding endpoint (e.g., `POST /api/setup/scan`,
  `POST /api/setup/download`) that delegates to the same `InitCommand` backend.
- Real-time progress via Server-Sent Events (SSE) or WebSocket.
- Drag-and-drop for document upload in the guided first task.

### Handoff Between CLI and UI

A user who starts setup via CLI and later opens the Electron app (or vice versa) should
see consistent state:

```
1. User runs `gaia init --profile chat` in terminal
2. Downloads complete, Lemonade running, models loaded
3. User opens Electron app
4. App reads ~/.gaia/initialized -> skips wizard, goes to chat
```

```
1. User opens Electron app first, starts wizard
2. Closes app mid-download at 40%
3. Runs `gaia init` in terminal
4. CLI reads setup-state.json, resumes download from 40%
```

---

## 9. Post-Onboarding

### Guided First Task (Expanded)

After setup completes, the system offers context-aware starter prompts based on the
persona interview and detected capabilities:

| Persona Selection | Suggested First Task |
|-------------------|---------------------|
| Chat privately | "Ask me anything -- your conversation stays on this device." |
| Document Q&A | "Drag a PDF here and ask a question about it." |
| Code generation | "Describe a function you need and I will write it." |
| Task automation | "Try: 'List my open Jira tickets' or 'Create a Docker container'" |
| Voice interaction | "Click the microphone icon to talk to me." |
| Just exploring | "Ask me 'What can you do?' to see my capabilities." |

### Settings Reminder

After the first session, show a non-intrusive banner:

```
Tip: Visit Settings to customize your model, enable voice, or add document folders.
[Open Settings]  [Dismiss]
```

### Telemetry Opt-In (Optional, v0.24.0+)

After the first successful interaction, optionally ask:

```
Help improve GAIA by sharing anonymous usage statistics?
No personal data or conversation content is ever sent.
[Yes, help improve GAIA]  [No thanks]
```

Default is opt-out. Stored in `~/.gaia/preferences.json`.

---

## 10. Cross-Platform Considerations

### Windows (Primary)

- Lemonade Server installed via MSI from GitHub releases.
- `gaia init` handles MSI download and silent install.
- PATH refresh uses Windows registry read (see `init_command.py` line 289-337).
- NPU detection via AMD driver device enumeration.
- Desktop installer via NSIS (`docs/plans/installer.mdx`). Related: #530.

### Linux

- Lemonade Server installed via `.deb` package or pip.
- `gaia init` detects distro and uses appropriate package manager.
- NPU detection via `lspci` and `/sys/class/` device tree.
- Desktop packaging via AppImage.

### macOS

- Lemonade Server installed via Homebrew or pip.
- No NPU support (Apple Silicon uses Metal, not AMD NPU).
- GPU detection via `system_profiler`.
- Desktop packaging via DMG.
- System scanner notes: "AMD NPU not available on macOS. Using CPU inference."

### Platform-Specific UI Differences

| Feature | Windows | Linux | macOS |
|---------|---------|-------|-------|
| System tray icon | Yes (native) | Yes (AppIndicator) | Yes (menu bar) |
| Auto-start on login | Registry entry | `.desktop` autostart | Launch Agent plist |
| Admin required | No (user-space install) | No | No |
| Default install path | `%USERPROFILE%\.gaia\` | `~/.gaia/` | `~/.gaia/` |

---

## 11. Implementation Phases

### Phase 1: v0.17.2 -- Foundation

- [ ] First-run detection (`~/.gaia/initialized` check)
- [ ] `gaia init` improvements: resume support, better progress output
- [ ] State file (`setup-state.json`) read/write
- [ ] Desktop installer (NSIS for Windows) -- #530

### Phase 2: v0.20.0 -- Scanner and Wizard UI

- [ ] System scanner module (`src/gaia/installer/system_scanner.py`) -- #466
- [ ] Hardware tier classification
- [ ] Electron onboarding wizard (Steps 1-4, 7) -- #470
- [ ] API endpoints for setup operations (`/api/setup/*`)
- [ ] Persona interview (Step 6) -- #467

### Phase 3: v0.24.0 -- Progressive Install and Guided Tasks

- [ ] Parallel model downloads with bandwidth management -- #468
- [ ] Full resume-on-failure with byte-level tracking -- #468
- [ ] Rollback on failure -- #468
- [ ] First-run auto-trigger on any `gaia` command -- #469
- [ ] Context-aware guided first task -- #471
- [ ] Messaging platform setup step (optional) -- deferred

---

## 12. GitHub Issue Cross-References

| Issue | Title | Milestone | Covered In |
|-------|-------|-----------|------------|
| #597 | Agent UI first-run setup wizard / onboarding flow | v0.17.2 | Sections 2, 4 |
| #466 | System Scanner: detect hardware, OS, installed tools | v0.20.0 | Section 3 |
| #467 | Onboarding Agent: persona interview and smart install | v0.20.0 | Section 4 (Step 6) |
| #468 | Setup executor: progressive install with resume/rollback | v0.24.0 | Section 7 |
| #469 | First-run detection: auto-trigger on initial gaia command | v0.24.0 | Section 2 |
| #470 | Chat UI onboarding wizard: visual setup flow | v0.20.0 | Section 4 |
| #471 | Guided first task: context-aware onboarding prompt | v0.24.0 | Section 4 (Step 7), Section 9 |
| #530 | Desktop installer: NSIS, DMG, AppImage + auto-update | v0.17.2 | Section 10 |

---

## 13. Open Questions

1. **Model storage location.** Should models live in `~/.gaia/models/` (GAIA-managed) or
   defer to Lemonade Server's own model cache? Current: Lemonade manages its own cache.
   Wizard should show where models are stored regardless.

2. **Offline-first mode.** For Enterprise Eric, the wizard should support a mode where
   model files are provided on a USB drive or network share instead of downloaded from
   the internet. Needs a "Load from file" option in Step 3.

3. **Multi-user systems.** On shared Linux/macOS machines, each user has their own
   `~/.gaia/` directory. On Windows with multiple user profiles, same approach. But
   Lemonade Server is system-wide -- how to handle version conflicts?

4. **Telemetry.** The opt-in telemetry question (Section 9) needs legal review before
   shipping. Consider deferring to v0.24.0+.
