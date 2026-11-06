// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Where the "quit while work is running?" question opens: in front of GAIA's
// window, and never inside a minimized window it can't be seen in.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const main = fs
  .readFileSync(path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"), "utf8")
  .replace(/\r\n/g, "\n");

const source = main.match(/showMessageBox: (\(opts\) => \{[\s\S]*?\n    \}),/);

function showWith({ platform, minimized, visible, focusedAfterShow }) {
  const win = {
    isDestroyed: () => false,
    isMinimized: jest.fn(() => minimized),
    restore: jest.fn(() => {
      minimized = false;
    }),
    isVisible: jest.fn(() => visible),
    show: jest.fn(() => {
      visible = true;
    }),
    isFocused: jest.fn(() => focusedAfterShow),
  };
  const dialog = { showMessageBox: jest.fn(async () => ({ response: 0 })) };
  const show = vm.runInNewContext(`(${source[1]})`, {
    mainWindow: win,
    dialog,
    process: { platform },
  });
  show({ message: "An answer is still being written." });
  return { win, dialog };
}

test("main.cjs still has the question's showMessageBox", () => {
  expect(source).not.toBeNull();
});

test("a window GAIA minimized on Linux gets the question on its own, so it shows", () => {
  const { win, dialog } = showWith({ platform: "linux", minimized: true, visible: true, focusedAfterShow: false });
  expect(win.restore).toHaveBeenCalled();
  expect(dialog.showMessageBox).toHaveBeenCalledWith({ message: "An answer is still being written." });
});

test("a window hidden in the tray (KDE) keeps the question attached, though focus arrives late", () => {
  const { win, dialog } = showWith({ platform: "linux", minimized: false, visible: false, focusedAfterShow: false });
  expect(win.show).toHaveBeenCalled();
  expect(win.restore).not.toHaveBeenCalled();
  expect(dialog.showMessageBox).toHaveBeenCalledWith(win, { message: "An answer is still being written." });
});

test("a minimized window that comes back focused keeps the question attached", () => {
  const { win, dialog } = showWith({ platform: "linux", minimized: true, visible: true, focusedAfterShow: true });
  expect(dialog.showMessageBox).toHaveBeenCalledWith(win, expect.any(Object));
});

test("Windows restores a minimized window and attaches the question to it", () => {
  const { win, dialog } = showWith({ platform: "win32", minimized: true, visible: true, focusedAfterShow: false });
  expect(win.restore).toHaveBeenCalled();
  expect(dialog.showMessageBox).toHaveBeenCalledWith(win, expect.any(Object));
});

test("a maximized, visible window isn't restored (that would un-maximize it)", () => {
  const { win } = showWith({ platform: "win32", minimized: false, visible: true, focusedAfterShow: true });
  expect(win.restore).not.toHaveBeenCalled();
  expect(win.show).not.toHaveBeenCalled();
});
