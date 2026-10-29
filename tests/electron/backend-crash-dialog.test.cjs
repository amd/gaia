// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The dialog shown when the backend exits on its own
 * (src/gaia/apps/webui/services/backend-crash-dialog.cjs): every choice ends
 * in a restart or a quit, and the log tail reads without colour codes.
 */

const {
  showBackendCrashDialog,
  crashDetail,
  stripAnsi,
  relaunchOptions,
} = require("../../src/gaia/apps/webui/services/backend-crash-dialog.cjs");

function fakes(responses) {
  const shown = [];
  const dialog = {
    // Electron's signature: showMessageBox([window], options).
    showMessageBox: jest.fn(async (...args) => {
      const opts = args[args.length - 1];
      shown.push(opts);
      return { response: opts.buttons.length === 1 ? 0 : responses.shift() };
    }),
    showErrorBox: jest.fn(),
  };
  const app = { quit: jest.fn(), relaunch: jest.fn() };
  return { dialog, app, shown };
}

const base = { code: 1, signal: null, tailLines: ["\u001b[37mINFO\u001b[0m | started"] };

test("Quit quits the app (it used to only close the dialog)", async () => {
  const { dialog, app } = fakes([2]);
  const beforeQuit = jest.fn();
  const choice = await showBackendCrashDialog({ ...base, dialog, app, beforeQuit, writeDiagnostics: jest.fn() });
  expect(choice).toBe("quit");
  expect(beforeQuit).toHaveBeenCalledTimes(1);
  expect(app.quit).toHaveBeenCalledTimes(1);
  expect(app.relaunch).not.toHaveBeenCalled();
});

test("Restart relaunches and quits, and is the default", async () => {
  const { dialog, app, shown } = fakes([0]);
  const choice = await showBackendCrashDialog({ ...base, dialog, app, writeDiagnostics: jest.fn() });
  expect(choice).toBe("restart");
  expect(app.relaunch).toHaveBeenCalledTimes(1);
  expect(app.quit).toHaveBeenCalledTimes(1);
  expect(shown[0].buttons[shown[0].defaultId]).toBe("Restart GAIA");
  expect(shown[0].buttons[shown[0].cancelId]).toBe("Quit");
});

test("Save diagnostics writes the bundle, then asks again", async () => {
  const { dialog, app, shown } = fakes([1, 2]);
  const writeDiagnostics = jest.fn(async () => "C:\\gaia\\diagnostics.tgz");
  await showBackendCrashDialog({ ...base, dialog, app, writeDiagnostics });
  expect(writeDiagnostics).toHaveBeenCalledTimes(1);
  expect(shown.some((o) => /diagnostics\.tgz/.test(o.detail || ""))).toBe(true);
  expect(app.quit).toHaveBeenCalledTimes(1);
});

test("a failed diagnostics write is reported, and the choice is offered again", async () => {
  const { dialog, app } = fakes([1, 0]);
  const writeDiagnostics = jest.fn(async () => { throw new Error("disk full"); });
  await showBackendCrashDialog({ ...base, dialog, app, writeDiagnostics });
  expect(dialog.showErrorBox).toHaveBeenCalledWith("Could not save diagnostics", expect.stringContaining("disk full"));
  expect(app.relaunch).toHaveBeenCalledTimes(1);
});

function fakeWindow({ minimized = false, destroyed = false } = {}) {
  return {
    isDestroyed: () => destroyed,
    isMinimized: () => minimized,
    restore: jest.fn(),
    show: jest.fn(),
    focus: jest.fn(),
  };
}

test("with a parent window, every box is modal to it and the window comes to the front", async () => {
  const { dialog, app } = fakes([1, 2]);
  const parent = fakeWindow({ minimized: true });
  await showBackendCrashDialog({
    ...base, dialog, app, parent, writeDiagnostics: jest.fn(async () => "/tmp/d.tgz"),
  });
  expect(parent.restore).toHaveBeenCalled();
  expect(parent.focus).toHaveBeenCalled();
  // crash box, "Diagnostics saved" box, crash box again
  expect(dialog.showMessageBox).toHaveBeenCalledTimes(3);
  for (const call of dialog.showMessageBox.mock.calls) expect(call[0]).toBe(parent);
});

test("without a usable parent (none yet, or destroyed) the dialog still shows, unowned", async () => {
  for (const parent of [undefined, null, fakeWindow({ destroyed: true })]) {
    const { dialog, app } = fakes([2]);
    await showBackendCrashDialog({ ...base, dialog, app, parent, writeDiagnostics: jest.fn() });
    expect(dialog.showMessageBox).toHaveBeenCalledTimes(1);
    expect(dialog.showMessageBox.mock.calls[0]).toHaveLength(1);
    expect(app.quit).toHaveBeenCalledTimes(1);
  }
});

test("the log tail has no colour codes and the detail says what to do", () => {
  expect(stripAnsi("\u001b[33mWARNING\u001b[0m x")).toBe("WARNING x");
  const detail = crashDetail(1, null, base.tailLines);
  expect(detail).not.toMatch(/\u001b/);
  expect(detail).toMatch(/exit code 1/);
  expect(detail).toMatch(/Restart GAIA/);
  expect(crashDetail(null, "SIGKILL", [])).toMatch(/signal SIGKILL[\s\S]*nothing was logged/);
});

test("an AppImage restarts from the AppImage file, not its temporary mount", async () => {
  const { dialog, app } = fakes([0]);
  await showBackendCrashDialog({
    ...base, dialog, app, writeDiagnostics: jest.fn(),
    env: { APPIMAGE: "/home/u/GAIA.AppImage" },
  });
  expect(app.relaunch).toHaveBeenCalledWith(
    expect.objectContaining({ execPath: "/home/u/GAIA.AppImage" })
  );
  expect(relaunchOptions({}, ["electron", "."])).toBeUndefined();
});
