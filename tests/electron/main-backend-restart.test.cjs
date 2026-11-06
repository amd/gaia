// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contract for main.cjs (it cannot be required without
 * Electron): a backend that exits on its own goes to the restart supervisor,
 * the crash dialog waits until restarts run out, and the exit leaves evidence.
 */

const fs = require("fs");
const path = require("path");

const main = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
  "utf8"
);
const preload = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "preload.cjs"),
  "utf8"
);

function block(start) {
  const i = main.indexOf(start);
  expect(i).toBeGreaterThan(-1);
  return main.slice(i, main.indexOf("\n  });\n", i));
}

test("main.cjs and preload.cjs compile, since the checks below only read their text", () => {
  const vm = require("vm");
  // Wrapped like Node's CommonJS loader, so a top-level return is legal.
  for (const [name, text] of [["main.cjs", main], ["preload.cjs", preload]]) {
    expect(() => new vm.Script(`(function (exports, require, module, __filename, __dirname) {${text}\n})`, { filename: name })).not.toThrow();
  }
});

test("an unexpected exit goes to the supervisor; the dialog shows only once it stops", () => {
  const onExit = block('child.on("exit"');
  expect(onExit).toMatch(/const crashed = !isIntentionalKill && !isQuitting;/);
  expect(onExit).toMatch(/backendSupervisor\.crashed\(\{ code, signal \}\) === "stopped"[\s\S]*handleBackendCrash/);
  // A later backend's handle is never cleared by an earlier one's exit.
  expect(onExit).toMatch(/if \(backendProcess === child\) backendProcess = null;/);
});

test("the backend's last stderr lines reach app.log once its pipes drain", () => {
  const onClose = block('child.once("close"');
  expect(onClose).toMatch(/backendStderrTail/);
  expect(onClose).toMatch(/console\.error/);
});

test("the backend's output is unbuffered, so its last lines aren't lost", () => {
  expect(main).toMatch(/PYTHONUNBUFFERED: "1"/);
});

test("a quit stops the supervisor before a pending restart can spawn", () => {
  const cleanup = main.slice(main.indexOf("async function cleanup()"));
  expect(cleanup.slice(0, 200)).toMatch(/backendSupervisor\.stop\(\);/);
  expect(main).toMatch(/onQuitting: \(\) => \{[\s\S]{0,300}backendSupervisor\.stop\(\);/);
});

test("the renderer can read the state, follow it and ask for Restart GAIA", () => {
  for (const channel of ["backend:get-state", "backend:restart"]) {
    expect(main).toContain(`ipcMain.handle("${channel}"`);
    expect(preload).toContain(`ipcRenderer.invoke("${channel}")`);
  }
  expect(main).toContain('webContents.send("backend:state"');
  expect(preload).toContain('onEvent("backend:state", cb)');
});
