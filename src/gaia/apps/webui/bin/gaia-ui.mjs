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
// using uv + Python 3.12.

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
const GAIA_VERSION_OVERRIDE = getArg("--gaia-version", null);

// ── Paths ────────────────────────────────────────────────────────────────────
const IS_WINDOWS = process.platform === "win32";
const GAIA_HOME = join(homedir(), ".gaia");
const GAIA_VENV = join(GAIA_HOME, "venv");

// Display-friendly paths (use ~ instead of full home directory)
const GAIA_VENV_DISPLAY = "~/.gaia/venv";
const GAIA_BIN = IS_WINDOWS
  ? join(GAIA_VENV, "Scripts", "gaia.exe")
  : join(GAIA_VENV, "bin", "gaia");


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

Update:   npm install -g @amd-gaia/agent-ui@latest
Uninstall: npm uninstall -g @amd-gaia/agent-ui && rm -rf ~/.gaia

Documentation: https://amd-gaia.ai/guides/agent-ui
`);
}

function printVersion() {
  const pkg = readPkg();
  console.log(`gaia-ui v${pkg.version}`);
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
 * Find the gaia binary - check venv first, then PATH.
 * Returns the path to the gaia executable, or null if not found.
 */
function findGaiaBin() {
  // Check the managed venv first
  if (existsSync(GAIA_BIN)) {
    return GAIA_BIN;
  }
  // Fall back to PATH
  if (commandExists("gaia")) {
    return "gaia";
  }
  return null;
}

/**
 * Ensure uv is available. Install it if not found.
 */
function ensureUv() {
  if (commandExists("uv")) return;

  console.log("Installing uv (Python package manager)...");

  let result;
  if (IS_WINDOWS) {
    result = spawnSync(
      "powershell",
      ["-ExecutionPolicy", "Bypass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"],
      { stdio: "inherit", env: { ...process.env } }
    );
  } else {
    result = spawnSync(
      "bash",
      ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
      { stdio: "inherit", env: { ...process.env } }
    );
  }

  if (result.status !== 0 || !commandExists("uv")) {
    console.error("");
    console.error("Could not install uv automatically.");
    console.error("This can happen behind corporate proxies or on restricted systems.");
    console.error("");
    console.error("Install uv manually, then re-run gaia-ui:");
    if (IS_WINDOWS) {
      console.error("  powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"");
    } else {
      console.error("  curl -LsSf https://astral.sh/uv/install.sh | sh");
    }
    console.error("");
    console.error("Or install the GAIA backend manually:");
    console.error("  https://amd-gaia.ai/quickstart#cli-install");
    process.exit(1);
  }
}

/**
 * Install the exact GAIA Python backend version that matches this npm package.
 * Uses uv to create a venv and install the pinned amd-gaia[ui] package.
 */
function installBackend() {
  const pkg = readPkg();
  const gaiaVersion = GAIA_VERSION_OVERRIDE || pkg.version;
  const pipPackage = `amd-gaia[ui]==${gaiaVersion}`;

  console.log("========================================");
  console.log("  First-time setup: Installing GAIA backend");
  console.log("========================================");
  console.log("");
  console.log(`  Package: ${pipPackage}`);
  console.log(`  Location: ${GAIA_VENV_DISPLAY}`);
  console.log("");

  // Step 1: Ensure uv is available
  ensureUv();

  // Step 2: Create venv if it doesn't exist
  if (!existsSync(GAIA_VENV)) {
    console.log("Creating Python environment...");
    mkdirSync(GAIA_HOME, { recursive: true });

    const venvResult = spawnSync("uv", ["venv", GAIA_VENV, "--python", "3.12"], {
      stdio: "inherit",
    });

    if (venvResult.status !== 0) {
      console.error("");
      console.error("Failed to create Python environment.");
      console.error("This may happen if Python 3.12 could not be downloaded.");
      console.error("");
      console.error("Try creating it manually, then re-run gaia-ui:");
      console.error(`  uv venv ${GAIA_VENV_DISPLAY} --python 3.12`);
      console.error("");
      console.error("Full manual install: https://amd-gaia.ai/quickstart#cli-install");
      process.exit(1);
    }
  }

  // Step 3: Install pinned amd-gaia[ui] into the venv
  console.log(`Installing ${pipPackage}...`);

  const pipArgs = ["pip", "install", pipPackage, "--python", join(GAIA_VENV, IS_WINDOWS ? "Scripts/python.exe" : "bin/python")];

  // Linux: use CPU-only PyTorch to avoid large CUDA packages
  if (!IS_WINDOWS) {
    pipArgs.push("--extra-index-url", "https://download.pytorch.org/whl/cpu");
  }

  const installResult = spawnSync("uv", pipArgs, {
    stdio: "inherit",
    env: { ...process.env },
  });

  if (installResult.status !== 0) {
    console.error("");
    console.error(`Failed to install ${pipPackage}.`);
    console.error("This can happen if the version is not available on PyPI or due to network issues.");
    console.error("");
    console.error("Try installing manually, then re-run gaia-ui:");
    const pythonBinDisplay = IS_WINDOWS ? `${GAIA_VENV_DISPLAY}/Scripts/python.exe` : `${GAIA_VENV_DISPLAY}/bin/python`;
    console.error(`  uv pip install ${pipPackage} --python ${pythonBinDisplay}`);
    console.error("");
    console.error("Full manual install: https://amd-gaia.ai/quickstart#cli-install");
    process.exit(1);
  }

  // Verify the install worked
  if (!existsSync(GAIA_BIN)) {
    console.error("");
    console.error(`Expected gaia binary at ${GAIA_VENV_DISPLAY} after installation, but not found.`);
    console.error("");
    console.error("Try installing manually: https://amd-gaia.ai/quickstart#cli-install");
    process.exit(1);
  }

  console.log("");
  console.log("Backend installed successfully!");
  console.log("");

  // Run gaia init to install Lemonade Server and download models
  console.log("Setting up Lemonade Server and downloading models...");
  console.log("(This may take a few minutes on first run)");
  console.log("");

  const initResult = spawnSync(GAIA_BIN, ["init", "--profile", "minimal"], {
    stdio: "inherit",
    env: { ...process.env },
  });

  if (initResult.status !== 0) {
    console.log("");
    console.log("Warning: gaia init did not complete successfully.");
    console.log("You can run it manually later: gaia init --profile minimal");
    console.log("");
  }
}

/**
 * Get the installed Python gaia version by running `gaia --version`.
 * Returns the version string (e.g. "0.17.0") or null if unknown.
 */
function getInstalledVersion(gaiaBin) {
  try {
    const result = spawnSync(gaiaBin, ["--version"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5000,
    });
    if (result.status === 0 && result.stdout) {
      // Output may be "0.17.0" or "gaia 0.17.0" — extract the version number
      const match = result.stdout.toString().trim().match(/(\d+\.\d+\.\d+)/);
      return match ? match[1] : null;
    }
  } catch {
    // ignore
  }
  return null;
}

/**
 * Ensure the GAIA Python backend is available and matches the expected version.
 * Installs or upgrades automatically if needed.
 */
function ensureBackend() {
  const pkg = readPkg();
  const expectedVersion = GAIA_VERSION_OVERRIDE || pkg.version;

  const gaiaBin = findGaiaBin();
  if (gaiaBin) {
    // Check if the installed version matches
    const installedVersion = getInstalledVersion(gaiaBin);
    if (installedVersion === expectedVersion) {
      return gaiaBin;
    }

    // Version mismatch — upgrade
    if (installedVersion) {
      console.log(`Updating GAIA backend: ${installedVersion} → ${expectedVersion}`);
    }
    installBackend();

    const upgraded = findGaiaBin();
    if (upgraded) return upgraded;
  } else {
    // Not found — install from scratch
    installBackend();
  }

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
 * Start the Python backend.
 * Uses "chat --ui" for compatibility with all gaia versions.
 */
function startBackend(gaiaBin, port) {
  console.log(`Starting GAIA backend on port ${port}...`);

  const child = spawn(gaiaBin, ["chat", "--ui", "--ui-port", String(port)], {
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
