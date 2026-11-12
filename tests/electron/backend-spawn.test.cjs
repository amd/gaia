// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { EventEmitter } = require("events");
const path = require("path");
const { spawn } = require("child_process");

const { waitForSpawn } = require(path.resolve(
  __dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "backend-spawn.cjs"
));

const OPTS = { command: "gaia", logPath: "/logs/main.log" };

test("a program that does not exist rejects at once, naming the spawn error", async () => {
  const child = spawn("gaia-no-such-program-xyz", ["chat"], { stdio: "ignore", windowsHide: true });
  const started = Date.now();
  const err = await waitForSpawn(child, { command: "gaia-no-such-program-xyz", logPath: "/logs/main.log" })
    .then(() => null, (e) => e);
  expect(err).toBeInstanceOf(Error);
  expect(err.message).toMatch(/could not be started \(gaia-no-such-program-xyz\)/);
  expect(err.message).toContain("ENOENT");
  expect(err.message).toContain("Logs: /logs/main.log");
  expect(err.cause && err.cause.code).toBe("ENOENT");
  expect(Date.now() - started).toBeLessThan(5000);
});

test("a program that starts resolves with the child and its pid", async () => {
  const child = spawn(process.execPath, ["-e", ""], { stdio: "ignore", windowsHide: true });
  const resolved = await waitForSpawn(child, OPTS);
  expect(resolved).toBe(child);
  expect(Number.isInteger(resolved.pid)).toBe(true);
  await new Promise((r) => child.once("exit", r));
});

test("a spawn event without a pid rejects instead of resolving", async () => {
  const child = new EventEmitter();
  child.pid = undefined;
  const pending = waitForSpawn(child, OPTS);
  child.emit("spawn");
  await expect(pending).rejects.toThrow(/returned no process id \(pid=undefined\)/);
});

test("its listeners are removed once settled, so a later error is not swallowed", async () => {
  const child = new EventEmitter();
  child.pid = 1234;
  const pending = waitForSpawn(child, OPTS);
  child.emit("spawn");
  await pending;
  expect(child.listenerCount("error")).toBe(0);
  expect(child.listenerCount("spawn")).toBe(0);
});
