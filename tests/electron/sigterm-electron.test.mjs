// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// SIGTERM must quit the real Electron runtime without the "Quit GAIA?"
// question (#6579). Electron replaces Node's SIGTERM handler with its own,
// which quits through the guard and so waits on a dialog nobody can answer;
// a mocked `process` cannot show that, so this runs the app's Electron.
//
//   cd src/gaia/apps/webui && npm ci
//   node --test tests/electron/sigterm-electron.test.mjs      (Linux: under xvfb-run -a)
//
// POSIX only: Windows has no SIGTERM to deliver.

import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEBUI = path.join(HERE, "..", "..", "src", "gaia", "apps", "webui");
const FIXTURE = path.join(HERE, "fixtures", "sigterm-main.cjs");

test("SIGTERM quits past the quit question in real Electron", { skip: process.platform === "win32" }, async () => {
  const electron = createRequire(path.join(WEBUI, "package.json"))("electron");
  const child = spawn(electron, [FIXTURE, "--no-sandbox"], { stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  child.stdout.on("data", (d) => (out += d));
  child.stderr.on("data", (d) => (out += d));
  const exited = new Promise((done) => child.once("exit", (code, signal) => done({ code, signal })));
  const within = (ms, what) =>
    new Promise((_, fail) => setTimeout(() => fail(new Error(`${what} within ${ms / 1000} s:\n${out}`)), ms).unref());

  try {
    await Promise.race([
      new Promise((done) => child.stdout.on("data", () => out.includes("FIXTURE ready") && done())),
      exited.then(() => {
        throw new Error(`Electron exited before it was ready:\n${out}`);
      }),
      within(60_000, "Electron was not ready"),
    ]);
    child.kill("SIGTERM");
    const { code, signal } = await Promise.race([exited, within(15_000, "Electron did not exit after SIGTERM")]);
    assert.equal(signal, null, `Electron was killed by ${signal}, not quit:\n${out}`);
    assert.equal(code, 0, out);
    assert.match(out, /SIGTERM: quitting without asking/);
    assert.doesNotMatch(out, /asked whether to quit/);
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
  }
});
