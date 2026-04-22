// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Tests for src/gaia/apps/webui/services/port-manager.cjs — the module
// extracted from main.cjs for issue #782 / T5. Uses node:test so it can
// run without Jest via `node --test tests/electron/test_port_manager.mjs`.
//
// Coverage:
//   - findFreePort() returns a valid ephemeral TCP port, and two successive
//     calls return distinct ports (validates the listen(0) allocation path).
//   - killBackend() sends SIGTERM and the target exits within the 3 s
//     window; if the target ignores SIGTERM, SIGKILL is used as the escape
//     hatch (validates the zombie-backend fix for AC6).
//   - writeDiagnosticsBundle() produces a real .tgz containing the present
//     log files (validates the `gaia diagnostics` support code from T9).

import test from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const portManagerPath = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "gaia",
  "apps",
  "webui",
  "services",
  "port-manager.cjs"
);

const {
  findFreePort,
  killBackend,
  writeDiagnosticsBundle,
  PortManager,
} = require(portManagerPath);

// ─── findFreePort ────────────────────────────────────────────────────

test("findFreePort returns a valid ephemeral port", async () => {
  const p = await findFreePort();
  assert.equal(typeof p, "number");
  assert.ok(p > 1024, `port ${p} should be > 1024`);
  assert.ok(p < 65536, `port ${p} should be < 65536`);
});

test("findFreePort returns distinct ports across successive calls", async () => {
  // Two synchronous-ish calls; ephemeral allocation should almost always
  // rotate. (The kernel is free to reuse, but in practice back-to-back
  // listen(0) calls return different ports on Linux/macOS.)
  const a = await findFreePort();
  const b = await findFreePort();
  assert.notEqual(a, b, `expected distinct ports, got ${a} and ${b} twice`);
});

// ─── killBackend ─────────────────────────────────────────────────────

function waitExit(child, timeoutMs) {
  // Register the exit handler IMMEDIATELY so we don't race against a
  // child that has already died by the time we await.
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({
      exited: true,
      code: child.exitCode,
      signal: child.signalCode,
    });
  }
  return new Promise((resolve) => {
    let done = false;
    const t = setTimeout(() => {
      if (!done) {
        done = true;
        resolve({ exited: false });
      }
    }, timeoutMs);
    child.on("exit", (code, signal) => {
      if (!done) {
        done = true;
        clearTimeout(t);
        resolve({ exited: true, code, signal });
      }
    });
  });
}

test("killBackend terminates a cooperative SIGTERM-responsive child", async () => {
  // Harmless long-running process; sleep exits cleanly on SIGTERM.
  const child = spawn("sleep", ["300"], { stdio: "ignore" });
  assert.ok(child.pid, "child should have a pid");
  // Pre-register the exit promise so we never miss the event.
  const exitPromise = waitExit(child, 4000);
  await delay(50);

  const start = Date.now();
  await killBackend(child.pid, { log: () => {}, error: () => {} });
  const { exited, signal } = await exitPromise;
  const elapsed = Date.now() - start;

  assert.equal(exited, true, "child should have exited within 4s");
  assert.ok(elapsed < 4000, `elapsed=${elapsed}ms should be < 4000ms`);
  assert.ok(
    signal === "SIGTERM" || signal === null || signal === "SIGKILL",
    `unexpected signal: ${signal}`
  );
});

test("killBackend escalates to SIGKILL when SIGTERM is trapped/ignored", async () => {
  // Shell trap: ignore SIGTERM, sleep long. SIGKILL is the only way out.
  const child = spawn(
    "bash",
    ["-c", "trap '' TERM; sleep 300"],
    { stdio: "ignore" }
  );
  assert.ok(child.pid, "child should have a pid");
  const exitPromise = waitExit(child, 5000);
  // Give bash a beat to install the trap before we signal.
  await delay(150);

  const start = Date.now();
  await killBackend(child.pid, { log: () => {}, error: () => {} });
  // port-manager waits 3s on SIGTERM then SIGKILLs; give a small buffer.
  const { exited, signal } = await exitPromise;
  const elapsed = Date.now() - start;

  assert.equal(exited, true, "child should have been killed via SIGKILL");
  assert.ok(
    elapsed >= 2800,
    `elapsed=${elapsed}ms should reflect ~3s SIGTERM grace before SIGKILL`
  );
  assert.equal(signal, "SIGKILL", `expected SIGKILL, got ${signal}`);
});

// ─── writeDiagnosticsBundle ──────────────────────────────────────────
//
// Redirect GAIA_HOME to a tempdir via `GAIA_HOME_OVERRIDE` so the test
// never touches a developer's real ~/.gaia. The module reads this env
// var at call-time (not require-time), so mutating it here works.

test("writeDiagnosticsBundle produces a tar.gz containing seeded log files", async () => {
  const fakeGaia = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-home-"));
  const markerPath = path.join(fakeGaia, "electron-install.log");
  fs.writeFileSync(markerPath, "test-marker-from-port-manager-test\n");

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-diag-"));
  const dest = path.join(tmpDir, "bundle.tgz");

  const prevOverride = process.env.GAIA_HOME_OVERRIDE;
  process.env.GAIA_HOME_OVERRIDE = fakeGaia;

  try {
    const written = await writeDiagnosticsBundle(dest);
    assert.ok(
      fs.existsSync(written),
      `bundle should exist at ${written}`,
    );
    const stat = fs.statSync(written);
    assert.ok(stat.size > 0, "bundle should be non-empty");

    // If the writer produced a real tgz (expected on Linux/macOS where
    // tar is always present), inspect its entries.
    if (written.endsWith(".tgz")) {
      const listing = execFileSync("tar", ["-tzf", written], {
        encoding: "utf8",
      });
      assert.ok(
        listing.includes("electron-install.log"),
        `archive should contain the seeded electron-install.log; got:\n${listing}`,
      );
    }
  } finally {
    if (prevOverride === undefined) {
      delete process.env.GAIA_HOME_OVERRIDE;
    } else {
      process.env.GAIA_HOME_OVERRIDE = prevOverride;
    }
    fs.rmSync(tmpDir, { recursive: true, force: true });
    fs.rmSync(fakeGaia, { recursive: true, force: true });
  }
});

// ─── PortManager class wrapper ───────────────────────────────────────

test("PortManager class wraps the module-level helpers", async () => {
  const pm = new PortManager({ logger: { log: () => {}, error: () => {} } });
  const p = await pm.findFreePort();
  assert.equal(typeof p, "number");
  assert.ok(p > 1024 && p < 65536);
  // killBackend with a nullish pid is a documented no-op; must not throw.
  await pm.killBackend(null);
});
