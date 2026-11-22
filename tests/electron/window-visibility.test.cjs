// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for telling the GAIA service when the app is out of sight
 * (src/gaia/apps/webui/services/window-visibility.cjs). GAIA's daemon unloads
 * the models of an app that sits hidden and unused, so a wrong "hidden" costs
 * the person a model reload and a wrong "showing" keeps gigabytes loaded.
 */

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");

const {
  ROUTE,
  RESEND_MS,
  WINDOW_EVENTS,
  appHidden,
  createWindowVisibility,
} = require("../../src/gaia/apps/webui/services/window-visibility.cjs");

/** A window with the three states Electron reports. */
function fakeWindow({ visible = true, minimized = false, destroyed = false } = {}) {
  const win = new EventEmitter();
  Object.assign(win, {
    state: { visible, minimized, destroyed },
    isVisible: () => win.state.visible,
    isMinimized: () => win.state.minimized,
    isDestroyed: () => win.state.destroyed,
  });
  return win;
}

/** A reporter over one window, a service that records what it is told, and timers run by hand. */
function setup({ port = 4200, quitting = () => false, win = fakeWindow() } = {}) {
  const sent = [];
  const timers = [];
  const errors = [];
  const service = { port, fail: null, delay: null };
  const send = jest.fn(async (toPort, route, opts) => {
    if (service.delay) await service.delay;
    if (service.fail) throw new Error(service.fail);
    sent.push({ port: toPort, route, method: opts.method, hidden: opts.body.hidden });
    return opts.body;
  });
  const visibility = createWindowVisibility({
    getPort: () => service.port,
    getWindows: () => (win.state.destroyed ? [] : [win]),
    quitting,
    send,
    log: { log: () => {}, error: (m) => errors.push(m) },
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimer: (id) => {
      if (timers[id - 1]) timers[id - 1].cleared = true;
    },
  });
  visibility.watch(win);
  const runTimers = async () => {
    for (const t of timers.splice(0)) if (!t.cleared) await t.fn();
  };
  return { visibility, win, sent, send, timers, errors, service, runTimers };
}

const hiddenStates = (sent) => sent.map((s) => s.hidden);

describe("appHidden", () => {
  test("a window on screen is not hidden", () => {
    expect(appHidden([fakeWindow()])).toBe(false);
  });

  test.each([
    ["in the tray", { visible: false }],
    ["minimised", { minimized: true }],
    ["destroyed", { destroyed: true }],
  ])("a window %s is hidden", (_name, state) => {
    expect(appHidden([fakeWindow(state)])).toBe(true);
  });

  test("no window at all is hidden: macOS keeps the app running without one", () => {
    expect(appHidden([])).toBe(true);
  });

  test("one window still on screen keeps the app showing", () => {
    expect(appHidden([fakeWindow({ visible: false }), fakeWindow()])).toBe(false);
  });
});

describe("createWindowVisibility", () => {
  test("the service hears once it is up, in the state the window is in", async () => {
    const { visibility, sent } = setup({ win: fakeWindow({ visible: false }) });
    await visibility.backendStarted();
    expect(sent).toEqual([{ port: 4200, route: ROUTE, method: "POST", hidden: true }]);
  });

  test("hiding to the tray and showing again are each reported", async () => {
    const { visibility, win, sent } = setup();
    await visibility.backendStarted();
    win.state.visible = false;
    win.emit("hide");
    await visibility.report();
    win.state.visible = true;
    win.emit("show");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false, true, false]);
  });

  test("minimising and restoring are each reported", async () => {
    const { visibility, win, sent } = setup();
    await visibility.backendStarted();
    win.state.minimized = true;
    win.emit("minimize");
    await visibility.report();
    win.state.minimized = false;
    win.emit("restore");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false, true, false]);
  });

  test("a closed window counts as hidden", async () => {
    const { visibility, win, sent } = setup();
    await visibility.backendStarted();
    win.state.destroyed = true;
    win.emit("closed");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false, true]);
  });

  test("an event that changes nothing sends nothing", async () => {
    const { visibility, win, sent } = setup();
    await visibility.backendStarted();
    win.emit("show");
    win.emit("restore");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false]);
  });

  test("it follows every window event the app can raise", () => {
    expect(WINDOW_EVENTS).toEqual(["show", "hide", "minimize", "restore", "closed"]);
  });

  test("a slow 'hidden' cannot land after a later 'showing'", async () => {
    const { visibility, win, sent, service } = setup();
    await visibility.backendStarted();
    let release;
    service.delay = new Promise((resolve) => (release = resolve));
    win.state.visible = false;
    win.emit("hide");
    win.state.visible = true;
    win.emit("show");
    release();
    service.delay = null;
    await visibility.report();
    // The window ended showing, so the service must end on "showing".
    expect(hiddenStates(sent).at(-1)).toBe(false);
  });

  test("nothing is sent while the service is down; it hears when it starts", async () => {
    const { visibility, win, sent, send, service } = setup({ port: null });
    win.state.visible = false;
    win.emit("hide");
    await visibility.report();
    expect(send).not.toHaveBeenCalled();
    service.port = 4300;
    await visibility.backendStarted();
    expect(sent).toEqual([{ port: 4300, route: ROUTE, method: "POST", hidden: true }]);
  });

  test("a restarted service is told again, though the window did not change", async () => {
    const { visibility, sent } = setup({ win: fakeWindow({ visible: false }) });
    await visibility.backendStarted();
    await visibility.backendStarted();
    expect(hiddenStates(sent)).toEqual([true, true]);
  });

  test("a report that fails is sent again until the service hears it", async () => {
    const { visibility, win, sent, timers, errors, service, runTimers } = setup();
    await visibility.backendStarted();
    service.fail = "the GAIA service did not answer";
    win.state.visible = false;
    win.emit("hide");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false]);
    // Each failed attempt replaces the pending resend: exactly one is live.
    expect(timers.filter((t) => !t.cleared).map((t) => t.ms)).toEqual([RESEND_MS]);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("the GAIA service did not answer");
    expect(errors[0]).toContain("hidden");

    await runTimers();
    expect(errors).toHaveLength(1); // the same failure is not logged every five seconds

    service.fail = null;
    await runTimers();
    expect(hiddenStates(sent)).toEqual([false, true]);
    expect(timers).toHaveLength(0);
  });

  test("a failed 'showing' is resent too: a stale 'hidden' would unload under an open window", async () => {
    const { visibility, win, sent, service, runTimers } = setup({ win: fakeWindow({ visible: false }) });
    await visibility.backendStarted();
    service.fail = "timeout";
    win.state.visible = true;
    win.emit("show");
    await visibility.report();
    service.fail = null;
    await runTimers();
    expect(hiddenStates(sent)).toEqual([true, false]);
  });

  test("a change that arrives before the resend goes at once and cancels it", async () => {
    const { visibility, win, sent, timers, service, runTimers } = setup();
    await visibility.backendStarted();
    service.fail = "timeout";
    win.state.visible = false;
    win.emit("hide");
    await visibility.report();
    service.fail = null;
    win.emit("hide");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false, true]);
    await runTimers();
    expect(hiddenStates(sent)).toEqual([false, true]);
    expect(timers.every((t) => t.cleared)).toBe(true);
  });

  test("nothing is sent once a quit is under way", async () => {
    let quitting = false;
    const { visibility, win, sent } = setup({ quitting: () => quitting });
    await visibility.backendStarted();
    quitting = true;
    win.state.destroyed = true;
    win.emit("closed");
    await visibility.report();
    expect(hiddenStates(sent)).toEqual([false]);
  });
});

describe("main.cjs wiring", () => {
  const main = fs.readFileSync(
    path.join(__dirname, "../../src/gaia/apps/webui/main.cjs"),
    "utf8"
  );

  test("the main window is followed from the moment it is created", () => {
    const created = main.indexOf("function createWindow()");
    const next = main.indexOf("\nfunction ", created + 1);
    expect(main.slice(created, next)).toContain("windowVisibility.watch(mainWindow)");
  });

  test("the service is told at startup and after every restart", () => {
    expect(main.match(/windowVisibility\.backendStarted\(\)/g)).toHaveLength(2);
  });

  test("the window is read from Electron, not from the page", () => {
    // Chromium marks a page hidden when another window merely covers it.
    expect(main).not.toContain("visibilitychange");
    expect(main).not.toContain("visibilityState");
  });
});
