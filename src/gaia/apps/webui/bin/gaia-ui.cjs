#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Agent UI CLI
// Usage:
//   gaia-ui                Start the app (backend + browser)
//   gaia-ui --serve        Serve frontend only (no backend auto-start)
//   gaia-ui --port 4200    Custom backend port
//   gaia-ui --help         Show help
//
// On first run, automatically installs the Python backend (amd-gaia[ui])
// using uv + Python 3.12 via the shared services/backend-installer.cjs
// module. That same module is used by main.cjs for the Electron app's
// first-run bootstrap.
//
// NOTE: This is .cjs (CommonJS) — package.json has `"type": "module"`, so
// .js files are ESM by default. The shared install module is pure CommonJS
// and is require()'d from both bin/gaia-ui.cjs and main.cjs.

"use strict";

const { spawn, exec } = require("child_process");
const path = require("path");
const fs = require("fs");
const { createServer } = require("http");
const { readFile } = require("fs/promises");

const installer = require("../services/backend-installer.cjs");

const ROOT_DIR = path.join(__dirname, "..");

const args = process.argv.slice(2);

function getArg(name, defaultValue) {
  const idx = args.indexOf(name);
  if (idx === -1) return defaultValue;
  return args[idx + 1] || defaultValue;
}

const hasFlag = (name) => args.includes(name);

const PORT = parseInt(getArg("--port", "4200"), 10);
const SERVE_ONLY = hasFlag("--serve");
const OPEN_BROWSER = !hasFlag("--no-open");
const GAIA_VERSION_OVERRIDE = getArg("--gaia-version", null);

function readPkg() {
  try {
    return JSON.parse(
      fs.readFileSync(path.join(ROOT_DIR, "package.json"), "utf-8")
    );
  } catch {
    return { version: "unknown" };
  }
}

function printHelp() {
  const pkg = readPkg();
  console.log(`
GAIA - Run AI agents locally on your PC
Version: ${pkg.version}

Usage: gaia-ui [options]

Options:
  --port <port>          Backend port (default: 4200)
  --gaia-version <ver>   Install a specific GAIA version (e.g. 0.16.1)
  --serve                Serve frontend only (skip Python backend)
  --no-open              Don't auto-open browser
  --help, -h             Show this help
  --version, -v          Show version

On first run, GAIA automatically installs the Python backend
(uv, Python 3.12, amd-gaia[ui]==${pkg.version}) into ~/.gaia/venv.
On subsequent runs, it auto-updates if the version doesn't match.

Logs: ~/.gaia/electron-install.log

Update:   npm install -g @amd-gaia/agent-ui@latest
Uninstall: npm uninstall -g @amd-gaia/agent-ui && rm -rf ~/.gaia

Documentation: https://amd-gaia.ai/guides/agent-ui
`);
}

function printVersion() {
  const pkg = readPkg();
  console.log(`gaia-ui v${pkg.version}`);
}

// ── Backend launch ──────────────────────────────────────────────────────────

/**
 * Wait for a URL to respond with 200.
 */
async function waitForServer(url, timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return true;
    } catch {
      // Server not ready yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

/**
 * Open a URL in the default browser.
 */
function openBrowser(url) {
  const platform = process.platform;
  let cmd;
  if (platform === "win32") {
    cmd = `start "" "${url}"`;
  } else if (platform === "darwin") {
    cmd = `open "${url}"`;
  } else {
    cmd = `xdg-open "${url}"`;
  }
  exec(cmd, (err) => {
    if (err) {
      console.log(`  Open manually: ${url}`);
    }
  });
}

/**
 * Start the Python backend.
 * Uses "chat --ui" for compatibility with all gaia versions.
 */
function startBackend(gaiaBin, port) {
  console.log(`Starting GAIA backend on port ${port}...`);

  const child = spawn(
    gaiaBin,
    [
      "chat",
      "--ui",
      "--ui-port",
      String(port),
      "--ui-dist",
      path.join(ROOT_DIR, "dist"),
    ],
    {
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    }
  );

  child.stdout.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`  [backend] ${line}`);
  });

  child.stderr.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`  [backend] ${line}`);
  });

  child.on("error", (err) => {
    console.error(`Failed to start backend: ${err.message}`);
    process.exit(1);
  });

  child.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      console.error(`Backend exited with code ${code}`);
    }
  });

  return child;
}

/**
 * Serve the pre-built frontend with a lightweight Node.js HTTP server.
 */
async function serveFrontend(port) {
  const distDir = path.join(ROOT_DIR, "dist");

  if (!fs.existsSync(path.join(distDir, "index.html"))) {
    console.error("Error: Frontend build not found.");
    console.error(`Expected: ${path.join(distDir, "index.html")}`);
    console.error("");
    console.error("The npm package may be corrupted. Try reinstalling:");
    console.error("  npm install -g @amd-gaia/agent-ui@latest");
    process.exit(1);
  }

  const MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
  };

  /**
   * Sanitize a URL path and return a safe file path within distDir.
   * Returns the index.html path for invalid or non-file requests (SPA fallback).
   */
  function safeLookup(urlPath) {
    const indexPath = path.join(distDir, "index.html");

    if (urlPath.includes("\0")) return indexPath;
    if (urlPath.includes("..")) return indexPath;
    if (!/^[a-zA-Z0-9._\-/]+$/.test(urlPath)) return indexPath;

    const candidate = path.resolve(distDir, "." + urlPath);
    const resolvedDistDir = path.resolve(distDir);

    const sep = resolvedDistDir.includes("\\") ? "\\" : "/";
    if (
      !candidate.startsWith(resolvedDistDir + sep) &&
      candidate !== resolvedDistDir
    ) {
      return indexPath;
    }

    if (!fs.existsSync(candidate) || !path.extname(candidate)) {
      return indexPath;
    }

    return candidate;
  }

  const server = createServer(async (req, res) => {
    const urlPath = req.url.split("?")[0];
    const safePath =
      urlPath === "/" ? path.join(distDir, "index.html") : safeLookup(urlPath);

    try {
      const data = await readFile(safePath);
      const ext = path.extname(safePath);
      res.writeHead(200, {
        "Content-Type": MIME_TYPES[ext] || "application/octet-stream",
      });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  });

  server.listen(port, () => {
    console.log(`GAIA Agent UI serving at http://localhost:${port}`);
  });

  return server;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  if (hasFlag("--help") || hasFlag("-h")) {
    printHelp();
    process.exit(0);
  }

  if (hasFlag("--version") || hasFlag("-v")) {
    printVersion();
    process.exit(0);
  }

  const pkg = readPkg();
  console.log("");
  console.log("========================================");
  console.log(`  GAIA Agent UI v${pkg.version}`);
  console.log("========================================");
  console.log("");

  let backendProcess = null;

  if (SERVE_ONLY) {
    console.log("Mode: Frontend-only (--serve)");
    console.log(`Port: ${PORT}`);
    console.log("");

    await serveFrontend(PORT);

    if (OPEN_BROWSER) {
      openBrowser(`http://localhost:${PORT}`);
    }
  } else {
    // Full mode: ensure backend is installed, start it, open browser.
    // `ensureBackend` uses the shared install module — same code path as
    // the Electron app — and writes logs to ~/.gaia/electron-install.log.
    let gaiaBin;
    try {
      gaiaBin = await installer.ensureBackend({
        version: GAIA_VERSION_OVERRIDE || undefined,
        onProgress: (stage, percent, message) => {
          // Simple CLI progress formatting
          process.stdout.write(
            `  [${stage}] ${percent}% ${message}\n`
          );
        },
      });
    } catch (err) {
      console.error("");
      console.error(`Install failed: ${err.message}`);
      if (err.suggestion) {
        console.error("");
        console.error(err.suggestion);
      }
      console.error("");
      console.error(`See log: ${installer.getLogPath()}`);
      process.exit(1);
    }

    backendProcess = startBackend(gaiaBin, PORT);

    console.log("Waiting for backend to start...");
    const ready = await waitForServer(
      `http://localhost:${PORT}/api/health`,
      30000
    );

    if (ready) {
      console.log("Backend is ready!");
      console.log("");
      console.log(`  Open: http://localhost:${PORT}`);
      console.log("");

      if (OPEN_BROWSER) {
        openBrowser(`http://localhost:${PORT}`);
      }
    } else {
      console.log("WARNING: Backend did not respond within 30 seconds.");
      console.log(`  Try opening manually: http://localhost:${PORT}`);
      console.log("");
    }
  }

  // Graceful shutdown
  function cleanup() {
    if (backendProcess) {
      console.log("\nShutting down GAIA...");
      backendProcess.kill("SIGTERM");
      setTimeout(() => {
        try {
          backendProcess.kill("SIGKILL");
        } catch {
          // Already dead
        }
        process.exit(0);
      }, 3000);
    } else {
      process.exit(0);
    }
  }

  process.on("SIGINT", cleanup);
  process.on("SIGTERM", cleanup);
}

main().catch((err) => {
  console.error("Fatal error:", err && err.stack ? err.stack : err);
  process.exit(1);
});
