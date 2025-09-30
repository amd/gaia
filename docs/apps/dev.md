# Building GAIA Apps

## Quick Start

Create AI-powered web apps that connect to GAIA's natural language capabilities.

## Step 1: Create Your App Structure

```
src/gaia/apps/[your-app-name]/
└── webui/
    ├── app.config.json    # App metadata (required)
    ├── package.json       # Dependencies
    └── public/
        └── index.html     # Your app's UI
```

## Step 2: Create app.config.json

```json
{
  "name": "your-app",
  "displayName": "Your App Name",
  "version": "1.0.0",
  "description": "What your app does",
  "window": {
    "width": 1200,
    "height": 800
  },
  "devServer": {
    "port": 5000  // Pick unique port
  }
}
```

## Step 3: Build Your Web App

Create a web app in `public/` that connects to GAIA's MCP server.

**Key Points:**
- Dev server injects `window.ENV` with environment variables
- Use `window.ENV?.GAIA_MCP_URL` for the MCP server URL
- See [MCP Documentation](../mcp.md#quick-start) for server setup and default configuration

See [MCP Documentation](../mcp.md#api-endpoints) for available endpoints and complete API reference.

## Step 4: Add Development Scripts

### NPM Scripts
Add to root `package.json`:

```json
{
  "scripts": {
    "app:your-app:run:dev": "node src/gaia/apps/_shared/dev-server.js your-app"
  }
}
```

### Local Build Scripts
Create `run.sh` (Linux) and `run.ps1` (Windows) in your webui folder for building and testing Electron installers locally.

See the [example app](../../src/gaia/apps/example/webui/) for complete working examples of:
- `run.sh` - Linux build script
- `run.ps1` - Windows build script
- `package.json` with Electron Forge configuration
- `src/main.js` and `src/preload.js` - Electron setup files

## Dependency Management

All app dependencies are automatically managed through NPM workspaces. The root `package.json` defines:
```json
"workspaces": [
  "src/gaia/electron",
  "src/gaia/apps/*/webui"
]
```

**To install all dependencies (including apps):**
```bash
# From project root - installs everything
npm install
```

This single command installs:
- Root dependencies
- Electron framework dependencies
- All app webui dependencies

### Package Lock Files (Required for CI/CD)

**IMPORTANT:** Each app must have its own `package-lock.json` file in the webui directory for CI/CD builds to work.

**Why it's required:**
- CI/CD workflows use `npm ci` which requires `package-lock.json`
- Ensures reproducible builds with exact dependency versions
- Prevents dependency version drift between environments

**How to generate:**
```bash
cd src/gaia/apps/your-app/webui
npm install --legacy-peer-deps --no-workspaces
```

This creates a standalone `package-lock.json` for your app, independent of the root workspace lock file.

**Important notes:**
- ✅ **DO** commit `package-lock.json` to version control
- ✅ **DO** regenerate it when you add/update dependencies
- ❌ **DON'T** delete it or add it to `.gitignore`
- ❌ **DON'T** run `npm install` without the flags above (it won't generate the lock file due to workspace mode)

## Step 5: Run Your App

### Development Modes

GAIA apps support two development modes:

#### Browser Mode (Rapid Development)
Best for UI development and quick iteration:
```bash
# Start from root directory
npm run app:your-app:run:dev
# Opens at http://localhost:[port]
```

#### Electron Mode (Desktop Testing)
For testing desktop features and building installers:
```bash
cd src/gaia/apps/your-app/webui
./run.sh   # Linux
.\run.ps1  # Windows
```

**Note:** The Windows run.ps1 script must include `npm install --no-workspaces` to ensure electron is available locally.

### MCP Server Setup
- **Local development:** Start MCP server with `gaia mcp start`
- **Remote server:** Configure `.env` with remote URL (see Environment Variables section)

## Environment Variables

Optional `.env` file in webui folder for configuring MCP connection and other settings.

See [MCP Documentation](../mcp.md#configuration-optional) for environment variable configuration details.

### Remote MCP Server Configuration

When using a remote MCP server (e.g., via ngrok tunnel or a different port):
- Configure your `.env` file with the remote server URL:
  ```
  GAIA_MCP_URL=https://example.ngrok.app  # Example: ngrok tunnel
  GAIA_MCP_URL=http://localhost:9000      # Example: custom port
  ```
- Note: You don't need to start a local MCP server when using a remote endpoint
- The dev server will inject this URL via `window.ENV.GAIA_MCP_URL`

## Examples

### Starter Example
See [`example` app](../../src/gaia/apps/example/webui/) - Simple working example with:
- Basic Electron setup (`src/main.js`, `src/preload.js`)
- Simple web UI (`public/index.html`)
- Build scripts (`run.sh`, `run.ps1`)
- Environment variable usage (`.env`)
- MCP integration demo

### Full-Featured App
See [`jira` app](../../src/gaia/apps/jira/webui/) - Complex production app with:
- Custom Electron setup with IPC handlers
- Advanced UI with multiple components (`public/` and `src/renderer/`)
- Service layer architecture (`src/app-controller.js`, `src/services/`)
- Complex build scripts with workspace management
- Full Jira integration

## CI/CD

Apps with `app.config.json` are automatically discovered and built by CI/CD.

## See Also

- [MCP Documentation](../mcp.md) - API reference
- [Electron Framework](../../src/gaia/electron/README.md) - Shared framework details
- [Development Guide](../dev.md) - General setup

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT