#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Chat CLI
// Usage:
//   gaia-chat                Start the app (backend + browser)
//   gaia-chat --serve        Serve frontend only (no backend auto-start)
//   gaia-chat --port 4200    Custom backend port
//   gaia-chat --help         Show help

import { spawn, exec, execSync } from "child_process";
import { dirname, join, extname, resolve } from "path";
import { fileURLToPath } from "url";
import { existsSync, readFileSync } from "fs";
import { readFile } from "fs/promises";
import { createServer } from "http";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT_DIR = join(__dirname, "..");

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

function readPkg() {
  try {
    return JSON.parse(readFileSync(join(ROOT_DIR, "package.json"), "utf-8"));
  } catch {
    return { version: "unknown" };
  }
}

function printHelp() {
  const pkg = readPkg();
  console.log(`
GAIA Chat - Privacy-first AI chat desktop application
Version: ${pkg.version}

Usage: gaia-chat [options]

Options:
  --port <port>   Backend port (default: 4200)
  --serve         Serve frontend only (skip Python backend)
  --no-open       Don't auto-open browser
  --help, -h      Show this help
  --version, -v   Show version

Modes:
  Default         Start Python backend (gaia chat --ui) and open browser
  --serve         Serve pre-built frontend with a lightweight Node.js server
                  (useful when running the Python backend separately)

Prerequisites:
  - Python gaia package: pip install amd-gaia
  - Lemonade Server: lemonade-server serve

Documentation: https://amd-gaia.ai/guides/chat-ui
`);
}

function printVersion() {
  const pkg = readPkg();
  console.log(`gaia-chat v${pkg.version}`);
}

/**
 * Check if a command exists on PATH.
 */
function commandExists(cmd) {
  const isWindows = process.platform === "win32";
  try {
    const check = isWindows ? `where ${cmd}` : `which ${cmd}`;
    execSync(check, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

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
 * Start the Python backend (gaia chat --ui).
 */
function startBackend(port) {
  const isWindows = process.platform === "win32";
  const gaiaCmd = isWindows ? "gaia.exe" : "gaia";

  // Check if gaia command is available
  if (!commandExists("gaia")) {
    console.error("Error: 'gaia' command not found.");
    console.error("");
    console.error("Install the GAIA Python package:");
    console.error("  pip install amd-gaia");
    console.error("");
    console.error("Or run in serve-only mode (requires backend running separately):");
    console.error("  gaia-chat --serve");
    process.exit(1);
  }

  console.log(`Starting GAIA Chat backend on port ${port}...`);

  const child = spawn(gaiaCmd, ["chat", "--ui", "--ui-port", String(port)], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
    detached: false,
  });

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
    console.error("");
    console.error("Make sure the GAIA Python package is installed:");
    console.error("  pip install amd-gaia");
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
  const distDir = join(ROOT_DIR, "dist");

  if (!existsSync(join(distDir, "index.html"))) {
    console.error("Error: Frontend build not found.");
    console.error(`Expected: ${join(distDir, "index.html")}`);
    console.error("");
    console.error("The npm package may be corrupted. Try reinstalling:");
    console.error("  npm install -g @amd-gaia/chat@latest");
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
    const indexPath = join(distDir, "index.html");

    // Reject null bytes
    if (urlPath.includes("\0")) return indexPath;

    // Reject path traversal patterns before any path operations
    if (urlPath.includes("..")) return indexPath;

    // Only allow safe characters in URL path
    if (!/^[a-zA-Z0-9._\-/]+$/.test(urlPath)) return indexPath;

    const candidate = resolve(distDir, "." + urlPath);
    const resolvedDistDir = resolve(distDir);

    // Verify the resolved path is within the dist directory
    if (!candidate.startsWith(resolvedDistDir + "/") && candidate !== resolvedDistDir) {
      return indexPath;
    }

    // Check the file exists and has an extension (not a directory)
    if (!existsSync(candidate) || !extname(candidate)) {
      return indexPath;
    }

    return candidate;
  }

  const server = createServer(async (req, res) => {
    // Strip query strings
    const urlPath = req.url.split("?")[0];

    // Resolve to a safe file path within distDir (never returns paths outside distDir)
    const safePath = urlPath === "/" ? join(distDir, "index.html") : safeLookup(urlPath);

    try {
      const data = await readFile(safePath);
      const ext = extname(safePath);
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
    console.log(`GAIA Chat frontend serving at http://localhost:${port}`);
  });

  return server;
}

// ── Main ──────────────────────────────────────────────────────────────────────

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
console.log(`  GAIA Chat v${pkg.version}`);
console.log("  Privacy-first AI chat");
console.log("========================================");
console.log("");

let backendProcess = null;

if (SERVE_ONLY) {
  // Serve-only mode: just serve the frontend static files
  console.log("Mode: Frontend-only (--serve)");
  console.log(`Port: ${PORT}`);
  console.log("");

  await serveFrontend(PORT);

  if (OPEN_BROWSER) {
    openBrowser(`http://localhost:${PORT}`);
  }
} else {
  // Full mode: start Python backend + open browser
  console.log("Mode: Full (backend + frontend)");
  console.log(`Port: ${PORT}`);
  console.log("");

  backendProcess = startBackend(PORT);

  // Wait for the backend to be ready
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
    console.log("\nShutting down GAIA Chat...");
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
