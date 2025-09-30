# GAIA Apps - Shared Infrastructure

This directory contains shared infrastructure and utilities used by GAIA web applications.

## dev-server.js

A web development server that serves GAIA apps for browser-based testing and development.

### Purpose

- **Web Browser Development Only** - This is NOT part of the Electron framework
- Serves HTML/CSS/JavaScript files via HTTP for browser testing
- Injects environment variables into the browser context
- Provides hot configuration reloading from `.env` files

### How It Works

1. **Environment Loading**: Reads `.env.development` (defaults) and `.env` (overrides)
2. **Static File Serving**: Serves files from the app's `public` directory
3. **Environment Injection**: Injects `window.ENV` with configuration into HTML
4. **Direct MCP Connection**: Apps connect directly to MCP server (no proxy needed)

### Usage

```bash
# From project root
npm run app:jira:run:dev

# Or directly
node src/gaia/apps/_shared/dev-server.js jira
```

### Important Notes

- This server is for **web development mode** only
- For **Electron desktop apps**, use `npm run app:jira:run:electron` which uses the Electron framework
- The MCP server provides proper CORS headers, so no proxy endpoints are needed
- Apps should read `window.ENV?.GAIA_MCP_URL` to get the configured MCP server URL

### Architecture

```
Web Development Mode:
Browser → HTTP → dev-server.js → Serves files + injects ENV
Browser → Direct connection → MCP Server (with CORS headers)

Electron Mode:
Electron App → main.js → Loads files directly from disk
(dev-server.js is NOT used in Electron mode)
```