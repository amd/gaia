# @amd-gaia/agent-ui

Privacy-first agentic AI interface with document Q&A — runs **100% locally** on AMD Ryzen AI hardware.

No cloud. No API keys. No data leaves your device.

## Install

```bash
npm install -g @amd-gaia/agent-ui
```

## Prerequisites

GAIA Agent UI requires the Python backend running locally:

```bash
# Install the GAIA Python package
pip install amd-gaia

# Start the LLM backend (AMD Ryzen AI accelerated)
lemonade-server serve
```

## Usage

```bash
# Start GAIA Agent UI (launches backend + opens browser)
gaia-ui

# Custom port
gaia-ui --port 4200

# Frontend-only mode (if backend is already running)
gaia-ui --serve

# Don't auto-open browser
gaia-ui --no-open
```

Then open [http://localhost:4200](http://localhost:4200) in your browser.

## Features

- **Private** — All processing runs locally on your AMD hardware. No data leaves your device.
- **Streaming responses** — Real-time token streaming with live agent activity visualization.
- **Document Q&A** — Upload PDFs, code files, and documents for RAG-powered question answering.
- **Agent activity** — Watch the AI think, plan, and use tools in real time.
- **Session management** — Create, search, rename, export, and delete chat sessions.
- **Dark/light mode** — Automatic theme detection with manual toggle.
- **Mobile access** — Share your local chat to your phone via secure tunnel.
- **Markdown rendering** — Code blocks, bold, italic, lists, and links in responses.
- **Keyboard shortcuts** — Enter to send, Shift+Enter for newlines.

## Architecture

```
Browser  <-->  Python Backend (FastAPI, port 4200)  <-->  Lemonade Server (LLM, port 8000)
                    |
                SQLite DB (~/.gaia/chat/)
```

The npm package includes:
- **Pre-built React frontend** served by the Python backend
- **CLI launcher** (`gaia-ui`) that starts the backend and opens the browser
- **Standalone serve mode** (`--serve`) for serving the frontend independently

## Desktop Installers

For a native desktop experience, download the installer from
[GitHub Releases](https://github.com/amd/gaia/releases):

- **Windows**: `gaia-ui-setup.exe`
- **Ubuntu/Linux**: `gaia-ui-setup.deb`

## Documentation

- [Chat UI Guide](https://amd-gaia.ai/guides/chat-ui) — Full setup and usage guide
- [Chat UI SDK Reference](https://amd-gaia.ai/sdk/sdks/chat-ui) — Backend API, models, endpoints
- [GAIA Documentation](https://amd-gaia.ai) — Complete GAIA framework docs

## Development

```bash
# Clone the repo
git clone https://github.com/amd/gaia.git
cd gaia/src/gaia/apps/webui

# Install dependencies
npm install

# Start dev server (proxies API to localhost:4200)
npm run dev

# Build for production
npm run build
```

## License

MIT — see [LICENSE](./LICENSE)
