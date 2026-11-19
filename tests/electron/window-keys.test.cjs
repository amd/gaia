// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Tests for the menu-less window's keys (services/window-keys.cjs). */

const {
  windowKeyAction,
  installWindowKeys,
  ZOOM_MAX,
} = require("../../src/gaia/apps/webui/services/window-keys.cjs");

const down = (key, mods = {}) => ({ type: "keyDown", key, control: false, meta: false, shift: false, alt: false, ...mods });

describe("windowKeyAction", () => {
  test.each([
    [down("=", { control: true }), "zoom-in"],
    [down("+", { control: true, shift: true }), "zoom-in"],
    [down("-", { control: true }), "zoom-out"],
    [down("0", { control: true }), "zoom-reset"],
    [down("r", { control: true }), "reload"],
    [down("R", { control: true }), "reload"],
  ])("maps %o to %s", (input, action) => {
    expect(windowKeyAction(input, { devTools: false })).toBe(action);
  });

  test("DevTools keys only work when DevTools are allowed", () => {
    expect(windowKeyAction(down("I", { control: true, shift: true }), { devTools: false })).toBeNull();
    expect(windowKeyAction(down("F12"), { devTools: false })).toBeNull();
    expect(windowKeyAction(down("I", { control: true, shift: true }), { devTools: true })).toBe("devtools");
    expect(windowKeyAction(down("F12"), { devTools: true })).toBe("devtools");
  });

  test.each([
    down("=", {}),
    down("r", { control: true, shift: true }),
    down("0", { control: true, alt: true }),
    { ...down("=", { control: true }), type: "keyUp" },
    down("c", { control: true }),
  ])("leaves %o to the page", (input) => {
    expect(windowKeyAction(input, { devTools: true })).toBeNull();
  });
});

describe("installWindowKeys", () => {
  function fakeContents(level = 0) {
    const handlers = {};
    return {
      level,
      on: (name, fn) => { handlers[name] = fn; },
      getZoomLevel() { return this.level; },
      setZoomLevel(l) { this.level = l; },
      reload: jest.fn(),
      toggleDevTools: jest.fn(),
      press(input) {
        const event = { preventDefault: jest.fn() };
        handlers["before-input-event"](event, input);
        return event;
      },
    };
  }

  test("zooms in, out and back, and stops at the limit", () => {
    const wc = fakeContents(ZOOM_MAX - 0.25);
    installWindowKeys(wc, { devTools: false });
    expect(wc.press(down("=", { control: true })).preventDefault).toHaveBeenCalled();
    expect(wc.level).toBe(ZOOM_MAX);
    wc.press(down("-", { control: true }));
    expect(wc.level).toBe(ZOOM_MAX - 0.5);
    wc.press(down("0", { control: true }));
    expect(wc.level).toBe(0);
  });

  test("reloads, and lets ordinary keys through", () => {
    const wc = fakeContents();
    installWindowKeys(wc, { devTools: false });
    wc.press(down("r", { control: true }));
    expect(wc.reload).toHaveBeenCalledTimes(1);
    expect(wc.press(down("c", { control: true })).preventDefault).not.toHaveBeenCalled();
  });
});
