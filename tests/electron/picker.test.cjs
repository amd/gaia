// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The element picker in main: the world it runs in, the binding only that
 * world can call, the pick refs it stamps, the main-world React lookup, the
 * crop, and the PickedElement it hands the renderer. The payload is checked
 * against tests/fixtures/browser_pane/picked/, which the UI server's tests
 * validate too.
 */

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow } = require("electron");

const PANE = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "browser-pane");
const PICKED = path.resolve(__dirname, "..", "fixtures", "browser_pane", "picked");
const { PickerSession, buildPickedElement, cropRect, parseComponent } = require(path.join(PANE, "picker.cjs"));
const { PaneHost, isPickShortcut } = require(path.join(PANE, "pane-host.cjs"));
const { TabCdp } = require(path.join(PANE, "cdp-session.cjs"));

const fixture = (name) => JSON.parse(fs.readFileSync(path.join(PICKED, name), "utf8"));
const RAW = fixture("raw_save_button.json");

describe("the PickedElement main builds", () => {
  test("matches the shared fixture the UI server validates", () => {
    expect(buildPickedElement(RAW, { ref: "p1", component: null, crop: null })).toEqual(fixture("save_button.json"));
  });

  test("a page cannot push a field past the server's limits", () => {
    const built = buildPickedElement(
      { ...RAW, text: "x".repeat(5000), name: "n".repeat(500), outerHTML: "<p>" + "h".repeat(5000), tag: "<script>", styles: { color: "red", cursor: "pointer" } },
      { ref: "p2", component: null, crop: null }
    );
    expect(built.text).toHaveLength(300);
    expect(built.name).toHaveLength(200);
    expect(built.outerHTML).toHaveLength(1536);
    expect(built.tag).toBe("element");
    expect(built.styles).toEqual({ color: "red" });
  });

  test("a React component is kept only in the expected shape", () => {
    expect(parseComponent({ name: "SaveButton", source: { file: "/src/SaveButton.tsx", line: 12.7 } })).toEqual({
      name: "SaveButton",
      source: { file: "/src/SaveButton.tsx", line: 12 },
    });
    expect(parseComponent({ name: "SaveButton", source: { file: 5 } })).toEqual({ name: "SaveButton", source: null });
    expect(parseComponent({ name: { toString: () => "x" } })).toBeNull();
    expect(parseComponent(null)).toBeNull();
  });

  test("the crop is taken in page coordinates, and not at all for an element with no size", () => {
    expect(cropRect(RAW)).toEqual({ x: 24, y: 266.5, width: 64, height: 36 });
    expect(cropRect({ ...RAW, rect: { ...RAW.rect, width: 0 } })).toBeNull();
  });
});

function fakeCdp() {
  const cdp = new EventEmitter();
  const listeners = new Set();
  cdp.onBinding = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  cdp.fire = (params) => listeners.forEach((fn) => fn(params));
  cdp.send = jest.fn((method) => {
    if (method === "Page.getFrameTree") return Promise.resolve({ frameTree: { frame: { id: "frame-main" } } });
    if (method === "Page.createIsolatedWorld") return Promise.resolve({ executionContextId: 42 });
    return Promise.resolve({ result: { value: true } });
  });
  cdp.screenshot = jest.fn(() => Promise.resolve(Buffer.from("png")));
  return cdp;
}

function makeSession(over = {}) {
  const cdp = fakeCdp();
  const wc = { mainFrame: { executeJavaScript: jest.fn(() => Promise.resolve({ name: "SaveButton", source: null })) } };
  let n = 0;
  const deps = {
    cdp,
    wc,
    allocateRef: () => `p${++n}`,
    encodeCrop: jest.fn(() => "Y3JvcA=="),
    onSelection: jest.fn(),
    onEnd: jest.fn(),
    logger: { error: jest.fn() },
    ...over,
  };
  return { session: new PickerSession(deps), cdp, wc, deps };
}

const sent = (cdp, method) => cdp.send.mock.calls.filter(([m]) => m === method).map(([, params]) => params);
const evaluated = (cdp) => sent(cdp, "Runtime.evaluate").map((p) => p.expression);
const settle = () => new Promise((r) => setImmediate(r));

describe("a picking session", () => {
  test("binds only inside its own isolated world, then runs the picker there", async () => {
    const { session, cdp } = makeSession();
    await session.start();
    const methods = cdp.send.mock.calls.map(([m]) => m);
    expect(methods.slice(0, 4)).toEqual(["Runtime.addBinding", "Page.getFrameTree", "Page.createIsolatedWorld", "Runtime.evaluate"]);
    const [binding] = sent(cdp, "Runtime.addBinding");
    const [world] = sent(cdp, "Page.createIsolatedWorld");
    expect(binding.executionContextName).toBe(world.worldName);
    expect(world).toMatchObject({ frameId: "frame-main", grantUniveralAccess: false });
    const [run] = sent(cdp, "Runtime.evaluate");
    expect(run.contextId).toBe(42);
    expect(run.expression).toContain("startPicker");
    expect(run.expression).toContain(binding.name);
  });

  test("a selection gets pick refs stamped in the world, a main-world React read, a crop, and reaches the renderer", async () => {
    const { session, cdp, wc, deps } = makeSession();
    await session.start();
    const binding = sent(cdp, "Runtime.addBinding")[0].name;
    cdp.fire({ name: binding, executionContextId: 42, payload: JSON.stringify({ type: "selection", elements: [RAW] }) });
    await session.queue;

    expect(evaluated(cdp)).toContain('globalThis.__gaiaPicker.stamp({"t1":"p1"})');
    expect(wc.mainFrame.executeJavaScript.mock.calls[0][0]).toContain(JSON.stringify('[data-gaia-pick="p1"]'));
    expect(wc.mainFrame.executeJavaScript.mock.calls[0][1]).toBe(false);
    const hide = evaluated(cdp).findIndex((e) => e.includes("hide(true)"));
    const show = evaluated(cdp).findIndex((e) => e.includes("hide(false)"));
    expect(hide).toBeGreaterThan(-1);
    expect(show).toBeGreaterThan(hide);
    expect(cdp.screenshot).toHaveBeenCalledWith({ x: 24, y: 266.5, width: 64, height: 36 });
    expect(deps.onSelection).toHaveBeenCalledWith([
      { ...fixture("save_button.json"), component: { name: "SaveButton", source: null }, crop: "Y3JvcA==" },
    ]);
  });

  test("Shift-click keeps the first element's ref and adds a new one for the second", async () => {
    const { session, cdp, deps } = makeSession();
    await session.start();
    const binding = sent(cdp, "Runtime.addBinding")[0].name;
    const second = { ...RAW, token: "t2", selector: "section.card" };
    cdp.fire({ name: binding, executionContextId: 42, payload: JSON.stringify({ type: "selection", elements: [RAW] }) });
    cdp.fire({ name: binding, executionContextId: 42, payload: JSON.stringify({ type: "selection", elements: [RAW, second] }) });
    await session.queue;
    expect(deps.onSelection.mock.calls[1][0].map((e) => e.ref)).toEqual(["p1", "p2"]);
    expect(evaluated(cdp).filter((e) => e.includes(".stamp("))).toEqual([
      'globalThis.__gaiaPicker.stamp({"t1":"p1"})',
      'globalThis.__gaiaPicker.stamp({"t2":"p2"})',
    ]);
    // One crop per element, not one per event.
    expect(cdp.screenshot).toHaveBeenCalledTimes(2);
  });

  test("a call on another binding or from another context is ignored", async () => {
    const { session, cdp, deps } = makeSession();
    await session.start();
    const binding = sent(cdp, "Runtime.addBinding")[0].name;
    const payload = JSON.stringify({ type: "selection", elements: [RAW] });
    cdp.fire({ name: binding, executionContextId: 7, payload });
    cdp.fire({ name: "__gaiaPick_forged", executionContextId: 42, payload });
    await session.queue;
    expect(deps.onSelection).not.toHaveBeenCalled();
  });

  test("Escape in the page ends the session and removes the binding", async () => {
    const { session, cdp, deps } = makeSession();
    await session.start();
    const binding = sent(cdp, "Runtime.addBinding")[0].name;
    cdp.fire({ name: binding, executionContextId: 42, payload: JSON.stringify({ type: "end" }) });
    await settle();
    await settle();
    expect(sent(cdp, "Runtime.removeBinding")).toEqual([{ name: binding }]);
    expect(deps.onEnd).toHaveBeenCalledTimes(1);
  });

  test("a crop that fails leaves the chip without a screenshot and says why in the log", async () => {
    const { session, cdp, deps } = makeSession();
    cdp.screenshot.mockImplementation(() => Promise.reject(new Error("capture failed")));
    await session.start();
    const binding = sent(cdp, "Runtime.addBinding")[0].name;
    cdp.fire({ name: binding, executionContextId: 42, payload: JSON.stringify({ type: "selection", elements: [RAW] }) });
    await session.queue;
    expect(deps.onSelection.mock.calls[0][0][0].crop).toBeNull();
    expect(deps.logger.error.mock.calls[0][0]).toMatch(/capture failed/);
  });
});

describe("TabCdp", () => {
  test("hands binding calls to listeners and keeps them out of the console log", () => {
    const wc = { debugger: Object.assign(new EventEmitter(), { attach: jest.fn(), sendCommand: jest.fn(() => Promise.resolve({})) }) };
    const cdp = new TabCdp(wc, { error: jest.fn() });
    const seen = [];
    const off = cdp.onBinding((p) => seen.push(p));
    cdp.onMessage("Runtime.bindingCalled", { name: "b", payload: "{}", executionContextId: 1 });
    off();
    cdp.onMessage("Runtime.bindingCalled", { name: "b", payload: "{}", executionContextId: 1 });
    expect(seen).toHaveLength(1);
    expect(cdp.console.entries).toHaveLength(0);
  });
});

describe("PaneHost and the picker", () => {
  function makeHost() {
    const sessions = [];
    const onPicked = jest.fn();
    const host = new PaneHost({
      window: new BrowserWindow(),
      session: Object.assign(new EventEmitter(), { cookies: { get: jest.fn(() => Promise.resolve([])) } }),
      publish: jest.fn(),
      blockedPorts: () => [],
      recentStore: { load: () => [], save: () => {} },
      goneChats: new Set(),
      onToggleRequest: jest.fn(),
      onShowRequest: jest.fn(),
      openExternal: jest.fn(),
      onPicked,
      createCdp: () => ({ send: jest.fn(() => Promise.resolve({})), onNavigationStarted: jest.fn(), onBinding: jest.fn() }),
      createPicker: (deps) => {
        const s = { deps, start: jest.fn(() => Promise.resolve()), stop: jest.fn(() => Promise.resolve()) };
        sessions.push(s);
        return s;
      },
    });
    host.openTab("http://127.0.0.1:5000/picker.html");
    return { host, sessions, onPicked, wc: host.tabs.get(host.activeTabId).view.webContents };
  }

  test("starts on the active tab's page, focuses it for the keys, and shows in the state", async () => {
    const { host, sessions, wc } = makeHost();
    await host.togglePicking();
    expect(sessions).toHaveLength(1);
    expect(sessions[0].start).toHaveBeenCalled();
    expect(wc.focus).toHaveBeenCalled();
    expect(host.state().picking).toBe(true);
  });

  test("refs count up across runs, so a later pick never reuses p1", async () => {
    const { host, sessions } = makeHost();
    await host.togglePicking();
    expect([sessions[0].deps.allocateRef(), sessions[0].deps.allocateRef()]).toEqual(["p1", "p2"]);
    await host.togglePicking();
    await host.togglePicking();
    expect(sessions[1].deps.allocateRef()).toBe("p3");
  });

  test("a selection goes to the renderer tagged with its run", async () => {
    const { host, sessions, onPicked } = makeHost();
    await host.togglePicking();
    sessions[0].deps.onSelection([{ ref: "p1" }]);
    expect(onPicked).toHaveBeenCalledWith({ run: 1, elements: [{ ref: "p1" }] });
  });

  test.each([
    ["the user switches tabs", (host) => host.selectTab(host.openTab(null))],
    ["the pane closes", (host) => host.setView(null, false)],
    ["the page navigates", (host, wc) => wc.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false })],
    ["the toggle runs again", (host) => host.togglePicking()],
  ])("picking ends when %s", async (_what, act) => {
    const { host, sessions, wc } = makeHost();
    await host.togglePicking();
    await act(host, wc);
    expect(sessions[0].stop).toHaveBeenCalled();
    expect(host.state().picking).toBe(false);
  });

  test("a new tab page has nothing to pick, and says so", async () => {
    const { host } = makeHost();
    host.openTab(null);
    await expect(host.togglePicking()).rejects.toThrow(/open a page/);
    expect(host.state().picking).toBe(false);
  });

  test("Cmd+Shift+S inside the page toggles picking and never reaches the page", async () => {
    const { host, sessions, wc } = makeHost();
    const event = { preventDefault: jest.fn() };
    wc.emit("before-input-event", event, { type: "keyDown", meta: true, shift: true, alt: false, control: false, key: "S" });
    await new Promise((r) => setImmediate(r));
    expect(event.preventDefault).toHaveBeenCalled();
    expect(sessions).toHaveLength(1);
    expect(isPickShortcut({ type: "keyDown", control: true, shift: true, alt: false, key: "s" })).toBe(true);
    expect(isPickShortcut({ type: "keyDown", meta: true, shift: false, alt: false, key: "s" })).toBe(false);
  });
});
