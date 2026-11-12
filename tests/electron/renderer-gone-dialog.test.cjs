// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The dialog shown when GAIA's window stops working while GAIA itself runs on
 * (src/gaia/apps/webui/services/renderer-gone-dialog.cjs): plain words, no
 * stack trace, and a button that brings the window back.
 */

const { showRendererGoneDialog } = require("../../src/gaia/apps/webui/services/renderer-gone-dialog.cjs");

function fakes(response) {
  const shown = [];
  const dialog = {
    showMessageBox: jest.fn(async (...args) => {
      shown.push({ owner: args.length > 1 ? args[0] : null, opts: args[args.length - 1] });
      return { response };
    }),
  };
  const parent = {
    isDestroyed: () => false,
    isMinimized: jest.fn(() => true),
    restore: jest.fn(),
    show: jest.fn(),
    focus: jest.fn(),
  };
  return { dialog, parent, shown };
}

test("Reload window is the first choice, and the default", async () => {
  const { dialog, parent, shown } = fakes(0);
  await expect(showRendererGoneDialog({ dialog, parent, reason: "crashed" })).resolves.toBe("reload");
  const { opts, owner } = shown[0];
  expect(owner).toBe(parent);
  expect(opts.buttons).toEqual(["Reload window", "Quit GAIA"]);
  expect(opts.defaultId).toBe(0);
  // Esc reloads too: closing the box must not quit GAIA.
  expect(opts.cancelId).toBe(0);
});

test("Quit GAIA is the second choice", async () => {
  const { dialog, parent } = fakes(1);
  await expect(showRendererGoneDialog({ dialog, parent, reason: "crashed" })).resolves.toBe("quit");
});

test.each([["crashed"], ["oom"], ["killed"], ["abnormal-exit"], ["launch-failed"]])(
  "the words for %s are plain: no stack, no event name",
  async (reason) => {
    const { dialog, parent, shown } = fakes(0);
    await showRendererGoneDialog({ dialog, parent, reason });
    const { opts } = shown[0];
    const text = `${opts.title}\n${opts.message}\n${opts.detail}`;
    expect(text).toMatch(/still running/);
    expect(text).toMatch(/Reload window/);
    expect(text).not.toMatch(/render-process-gone|\.cjs|at EventEmitter|Error:/);
  }
);

test("running out of memory is said as such", async () => {
  const { dialog, parent, shown } = fakes(0);
  await showRendererGoneDialog({ dialog, parent, reason: "oom" });
  expect(shown[0].opts.detail).toMatch(/memory/);
});

test("a hidden or minimized window is brought forward first", async () => {
  const { dialog, parent } = fakes(0);
  await showRendererGoneDialog({ dialog, parent, reason: "crashed" });
  expect(parent.restore).toHaveBeenCalled();
  expect(parent.show).toHaveBeenCalled();
});

test("with no window left the dialog still shows, unparented", async () => {
  const { dialog, shown } = fakes(0);
  await showRendererGoneDialog({ dialog, parent: null, reason: "crashed" });
  expect(shown[0].owner).toBeNull();
});
