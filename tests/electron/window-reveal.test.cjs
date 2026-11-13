// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// On Wayland "ready-to-show" never fires for a hidden window, so GAIA started
// with no window at all. These pin the page-load fallback that shows it anyway.

const { EventEmitter } = require("events");
const fs = require("fs");
const path = require("path");

const { revealWhenReady, PAINT_WAIT_MS } = require("../../src/gaia/apps/webui/services/window-reveal.cjs");

function fakeWindow() {
  const win = new EventEmitter();
  win.webContents = new EventEmitter();
  return win;
}

function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    setTimer: (fn, ms) => {
      pending.set(next, { fn, ms });
      return next++;
    },
    clearTimer: (id) => pending.delete(id),
    pending,
    fire: () => [...pending.values()].forEach(({ fn }) => fn()),
  };
}

test("a painted window is shown on ready-to-show", () => {
  const win = fakeWindow();
  const reveal = jest.fn();
  revealWhenReady(win, reveal, fakeTimers());
  win.emit("ready-to-show");
  expect(reveal).toHaveBeenCalledTimes(1);
  expect(reveal).toHaveBeenCalledWith("ready-to-show");
});

test("a window that never paints is shown once its page has loaded", () => {
  const win = fakeWindow();
  const reveal = jest.fn();
  const timers = fakeTimers();
  revealWhenReady(win, reveal, timers);
  win.webContents.emit("did-finish-load");
  expect(reveal).not.toHaveBeenCalled();
  expect([...timers.pending.values()].map((t) => t.ms)).toEqual([PAINT_WAIT_MS]);
  timers.fire();
  expect(reveal).toHaveBeenCalledTimes(1);
  expect(reveal).toHaveBeenCalledWith("page-loaded");
});

test("the window is shown once, whichever signal comes first", () => {
  const win = fakeWindow();
  const reveal = jest.fn();
  const timers = fakeTimers();
  revealWhenReady(win, reveal, timers);
  win.webContents.emit("did-finish-load");
  win.emit("ready-to-show");
  expect(timers.pending.size).toBe(0);
  timers.fire();
  win.emit("ready-to-show");
  expect(reveal).toHaveBeenCalledTimes(1);

  const late = fakeWindow();
  const lateReveal = jest.fn();
  const lateTimers = fakeTimers();
  revealWhenReady(late, lateReveal, lateTimers);
  late.emit("ready-to-show");
  late.webContents.emit("did-finish-load");
  expect(lateTimers.pending.size).toBe(0);
  expect(lateReveal).toHaveBeenCalledTimes(1);
});

test("main.cjs shows its windows through revealWhenReady, never a bare ready-to-show", () => {
  const main = fs.readFileSync(path.join(__dirname, "../../src/gaia/apps/webui/main.cjs"), "utf8");
  expect(main).not.toMatch(/once\(\s*["']ready-to-show["']/);
  expect(main.match(/revealWhenReady\(/g).length).toBeGreaterThanOrEqual(2);
});
