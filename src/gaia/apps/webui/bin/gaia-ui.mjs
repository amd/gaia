#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Agent UI CLI
// Usage:
//   gaia                   Start the app (backend + browser)
//   gaia --serve           Serve frontend only (no backend auto-start)
//   gaia --port 4200       Custom backend port
//   gaia --help            Show help
//
// On first run, automatically installs the Python backend (amd-gaia)
// using the GAIA install scripts (uv + Python 3.12 + amd-gaia).

import { spawn, exec, execSync, spawnSync } from "child_process";
import { dirname, join, extname, resolve } from "path";
import { fileURLToPath } from "url";
import { existsSync, readFileSync, mkdirSync } from "fs";
import { readFile } from "fs/promises";
import { createServer } from "http";
import { homedir } from "os";

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

// ── Paths ────────────────────────────────────────────────────────────────────
const IS_WINDOWS = process.platform === "win32";
const GAIA_HOME = join(homedir(), ".gaia");
const GAIA_VENV = join(GAIA_HOME, "venv");
const GAIA_BIN = IS_WINDOWS
  ? join(GAIA_VENV, "Scripts", "gaia.exe")
  : join(GAIA_VENV, "bin", "gaia");

// Install script URLs
const INSTALL_SCRIPT_URL_PS1 = "https://amd-gaia.ai/install.ps1";
const INSTALL_SCRIPT_URL_SH = "https://amd-gaia.ai/install.sh";

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
GAIA - Run AI agents locally on your PC
Version: ${pkg.version}

Usage: gaia [options]

Options:
  --port <port>   Backend port (default: 4200)
  --serve         Serve frontend only (skip Python backend)
  --no-open       Don't auto-open browser
  --help, -h      Show this help
  --version, -v   Show version

Modes:
  Default         Start Python backend + open browser
  --serve         Serve pre-built frontend with a lightweight Node.js server
                  (useful when running the Python backend separately)

On first run, GAIA automatically installs the Python backend
(uv, Python 3.12, amd-gaia) into ~/.gaia/venv.

Documentation: https://amd-gaia.ai/guides/agent-ui
`);
}

function printVersion() {
  const pkg = readPkg();
  console.log(`gaia v${pkg.version}`);
}

/**
 * Check if a command exists on PATH.
 */
function commandExists(cmd) {
  try {
    const check = IS_WINDOWS ? `where ${cmd}` : `which ${cmd}`;
    execSync(check, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

/**
 * Check if a resolved gaia binary is the Python CLI (not this Node.js shim).
 * Prevents infinite spawn loops when the npm "gaia" bin shadows the Python one.
 */
function isPythonGaia(binPath) {
  try {
    const result = spawnSync(binPath, ["--version"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5000,
    });
    // Python gaia prints a version string; Node.js gaia-ui.mjs prints "gaia v..."
    // Check that the binary is not this script (Node.js)
    const stdout = (result.stdout || "").toString().trim();
    // If it outputs nothing or errors, it's not the Python CLI
    if (result.status !== 0 || !stdout) return false;
    // The Python CLI outputs just a version like "0.17.0" or "gaia 0.17.0"
    // The Node.js shim outputs "gaia v0.17.0" (from printVersion)
    // As a safeguard, check the binary is not this exact file
    const resolvedBin = resolve(binPath);
    const thisScript = resolve(__filename);
    if (resolvedBin === thisScript) return false;
    return true;
  } catch {
    return false;
  }
}

/**
 * Find the gaia binary - check venv first, then PATH.
 * Returns the path to the gaia executable, or null if not found.
 * Guards against finding this Node.js shim instead of the Python CLI.
 */
function findGaiaBin() {
  // Check the managed venv first (always the Python binary)
  if (existsSync(GAIA_BIN)) {
    return GAIA_BIN;
  }
  // Fall back to PATH, but verify it's the Python CLI
  if (commandExists("gaia")) {
    // Resolve the full path to avoid spawning ourselves
    try {
      const which = IS_WINDOWS ? "where gaia" : "which gaia";
      const resolved = execSync(which, { stdio: ["ignore", "pipe", "ignore"] })
        .toString()
        .trim()
        .split("\n")[0]
        .trim();
      if (resolved && resolve(resolved) !== resolve(__filename) && isPythonGaia(resolved)) {
        return resolved;
      }
    } catch {
      // Fall through
    }
  }
  return null;
}

/**
 * Install the GAIA Python backend by running the install script.
 * Windows: PowerShell install.ps1
 * Linux/macOS: bash install.sh
 */
function installBackend() {
  console.log("========================================");
  console.log("  First-time setup: Installing GAIA backend");
  console.log("========================================");
  console.log("");
  console.log("This installs uv, Python 3.12, and the amd-gaia package");
  console.log(`into ${GAIA_VENV}`);
  console.log("");

  let result;

  if (IS_WINDOWS) {
    // Run install.ps1 via PowerShell
    console.log("Running GAIA installer for Windows...");
    console.log("");
    result = spawnSync(
      "powershell",
      [
        "-ExecutionPolicy", "Bypass",
        "-Command",
        `irm ${INSTALL_SCRIPT_URL_PS1} | iex`,
      ],
      { stdio: "inherit", env: { ...process.env } }
    );
  } else {
    // Run install.sh via bash
    console.log("Running GAIA installer for Linux/macOS...");
    console.log("");
    result = spawnSync(
      "bash",
      ["-c", `curl -fsSL ${INSTALL_SCRIPT_URL_SH} | sh`],
      { stdio: "inherit", env: { ...process.env } }
    );
  }

  if (result.status !== 0) {
    console.error("");
    console.error("Backend installation failed.");
    console.error("Try installing manually: https://amd-gaia.ai/quickstart");
    process.exit(1);
  }

  console.log("");

  // Verify the install worked
  if (!existsSync(GAIA_BIN)) {
    console.error(`Error: Expected ${GAIA_BIN} after installation, but not found.`);
    console.error("Try installing manually: https://amd-gaia.ai/quickstart");
    process.exit(1);
  }

  console.log("Backend installed successfully!");
  console.log("");
}

/**
 * Ensure the GAIA Python backend is available.
 * If not found, auto-install via the install scripts.
 */
function ensureBackend() {
  const gaiaBin = findGaiaBin();
  if (gaiaBin) {
    return gaiaBin;
  }

  // Not found — install automatically
  installBackend();

  // Re-check after install
  const installed = findGaiaBin();
  if (!installed) {
    console.error("Error: GAIA backend not found after installation.");
    console.error("");
    console.error("Try installing manually:");
    console.error("  https://amd-gaia.ai/quickstart");
    process.exit(1);
  }
  return installed;
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
 * Start the Python backend (gaia --ui).
 */
function startBackend(gaiaBin, port) {
  console.log(`Starting GAIA backend on port ${port}...`);

  const child = spawn(gaiaBin, ["--ui", "--ui-port", String(port)], {
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
    const indexPath = join(distDir, "index.html");

    // Reject null bytes
    if (urlPath.includes("\0")) return indexPath;

    // Reject path traversal patterns before any path operations
    if (urlPath.includes("..")) return indexPath;

    // Only allow safe characters in URL path
    if (!/^[a-zA-Z0-9._\-/]+$/.test(urlPath)) return indexPath;

    const candidate = resolve(distDir, "." + urlPath);
    const resolvedDistDir = resolve(distDir);

    // Verify the resolved path is within the dist directory.
    // Use path.sep for cross-platform safety (Windows uses "\", Unix uses "/").
    const sep = resolvedDistDir.includes("\\") ? "\\" : "/";
    if (!candidate.startsWith(resolvedDistDir + sep) && candidate !== resolvedDistDir) {
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
    console.log(`GAIA Agent UI serving at http://localhost:${port}`);
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
console.log(`  GAIA Agent UI v${pkg.version}`);
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
  // Full mode: ensure backend is installed, start it, open browser
  const gaiaBin = ensureBackend();

  backendProcess = startBackend(gaiaBin, PORT);

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
