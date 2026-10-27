// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The agent acting in the browser pane: trusted input at the target's point,
 * the checks before it lands, the keys it may press, the user taking over a
 * tab, private tabs, and the viewport the user picked winning over the
 * agent's. Each test asserts what reached the page (the CDP commands and
 * scripts), not only that a method ran.
 */

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow } = require("electron");

const PANE = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "browser-pane");
const FIXTURES = path.resolve(__dirname, "..", "fixtures", "browser_pane", "bridge");
const { PaneHost, USER_PAUSE_MS } = require(path.join(PANE, "pane-host.cjs"));
const { KEYS, frameFor } = require(path.join(PANE, "input.cjs"));
const { createHandlers, parseRequest } = require(path.join(PANE, "bridge-server.cjs"));

const fixture = (name) => JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
const CONTRACT = fs.readFileSync(path.join(PANE, "contract.d.ts"), "utf8");
const requests = fixture("requests.json");
const SEL = '[data-gaia-ref="g1e2"]';
const TARGET = { frameId: null, selector: SEL };
const POINT = { x: 120, y: 48, tag: "button", type: "submit", editable: false };

function makeHost() {
  const ses = new EventEmitter();
  ses.cookies = { get: jest.fn(() => Promise.resolve([])) };
  const cdps = [];
  const host = new PaneHost({
    window: new BrowserWindow(),
    session: ses,
    publish: jest.fn(),
    blockedPorts: () => [],
    recentStore: { load: () => [], save: () => {} },
    onToggleRequest: jest.fn(),
    onShowRequest: jest.fn(),
    openExternal: jest.fn(),
    createCdp: () => {
      const cdp = { send: jest.fn(() => Promise.resolve({})), onNavigationStarted: jest.fn(), logs: jest.fn(), screenshot: jest.fn() };
      cdps.push(cdp);
      return cdp;
    },
  });
  host.setActiveSession("chat-1");
  host.navigate(host.activeTabId, "http://127.0.0.1:5000/form.html");
  const tab = host.tabs.get(host.activeTabId);
  const wc = tab.view.webContents;
  return { host, tab, wc, cdp: cdps[0] };
}

/** The page answers the actionability probe with `probe`, and every other script with true. */
function pageSays(wc, probe) {
  wc.mainFrame.executeJavaScript.mockImplementation((code) =>
    Promise.resolve(code.includes("scrollIntoView") ? probe : code.includes("options") ? { chosen: "Pro" } : true)
  );
}

const inputSent = (cdp) => cdp.send.mock.calls.filter(([method]) => method.startsWith("Input."));

describe("trusted input", () => {
  test("a click is a real mouse press and release at the element's centre", async () => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, POINT);
    const reply = await host.agentAct(null, { kind: "click" }, TARGET, []);
    expect(inputSent(cdp)).toEqual([
      ["Input.dispatchMouseEvent", { type: "mouseMoved", x: 120, y: 48 }],
      ["Input.dispatchMouseEvent", { type: "mousePressed", x: 120, y: 48, button: "left", buttons: 1, clickCount: 1 }],
      ["Input.dispatchMouseEvent", { type: "mouseReleased", x: 120, y: 48, button: "left", buttons: 0, clickCount: 1 }],
    ]);
    expect(wc.mainFrame.executeJavaScript.mock.calls[0][0]).toContain(JSON.stringify(SEL));
    expect(reply).toMatchObject({ tabId: host.activeTabId, url: "http://127.0.0.1:5000/form.html" });
  });

  test("typing clicks into the field, selects what it held, and inserts the text", async () => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, { ...POINT, tag: "input", type: "email" });
    await host.agentAct(null, { kind: "fill", text: "ada@example.com", pressEnter: true }, TARGET, []);
    const sent = inputSent(cdp).map(([m, p]) => `${m}:${p.type || p.text}`);
    expect(sent).toEqual([
      "Input.dispatchMouseEvent:mouseMoved",
      "Input.dispatchMouseEvent:mousePressed",
      "Input.dispatchMouseEvent:mouseReleased",
      "Input.insertText:ada@example.com",
      "Input.dispatchKeyEvent:keyDown",
      "Input.dispatchKeyEvent:keyUp",
    ]);
    expect(wc.mainFrame.executeJavaScript.mock.calls.some(([code]) => code.includes("el.select()"))).toBe(true);
  });

  test("a dropdown option is picked by its label, with no mouse events", async () => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, { ...POINT, tag: "select", type: "" });
    await host.agentAct(null, { kind: "fill", text: "Pro", pressEnter: false }, TARGET, []);
    expect(inputSent(cdp)).toEqual([]);
    expect(wc.mainFrame.executeJavaScript.mock.calls.some(([code]) => code.includes('"Pro"'))).toBe(true);
  });

  test("a key press with no target goes to whatever has focus", async () => {
    const { host, wc, cdp } = makeHost();
    await host.agentAct(null, { kind: "press", key: "Escape" }, null, []);
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
    expect(inputSent(cdp)).toEqual([
      ["Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 }],
      ["Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 }],
    ]);
  });

  test("the strip says GAIA is using the tab, and the pane is shown", async () => {
    const { host, tab, wc } = makeHost();
    pageSays(wc, POINT);
    await host.agentAct(null, { kind: "click" }, TARGET, []);
    expect(tab.state.agentActing).toBe(true);
    expect(host.deps.onShowRequest).toHaveBeenCalled();
  });
});

describe("checks before input lands", () => {
  test.each([
    [{ fail: "gone" }, "element_not_found", /not on the page any more/],
    [{ fail: "hidden" }, "not_actionable", /not visible/],
    [{ fail: "disabled" }, "not_actionable", /disabled/],
    [{ fail: "covered", by: '"Accept cookies"' }, "not_actionable", /covered by "Accept cookies"/],
  ])("%j is refused as %s, and nothing is clicked", async (probe, code, message) => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, probe);
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).rejects.toMatchObject({ code, message: expect.stringMatching(message) });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("a password field is never typed into", async () => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, { ...POINT, tag: "input", type: "password" });
    await expect(host.agentAct(null, { kind: "fill", text: "hunter2", pressEnter: false }, TARGET, [])).rejects.toMatchObject({
      code: "not_actionable",
      message: expect.stringMatching(/never types passwords/),
    });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("a checkbox takes a click, not typing", async () => {
    const { host, wc, cdp } = makeHost();
    pageSays(wc, { ...POINT, tag: "input", type: "checkbox" });
    await expect(host.agentAct(null, { kind: "fill", text: "yes", pressEnter: false }, TARGET, [])).rejects.toMatchObject({
      code: "not_actionable",
      message: expect.stringMatching(/browser_click/),
    });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("a page off the approved sites is not acted on", async () => {
    const { host, wc, cdp } = makeHost();
    wc._url = "https://example.org/";
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).rejects.toMatchObject({ code: "off_scope" });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
    expect(inputSent(cdp)).toEqual([]);
  });
});

/**
 * A child frame on another site, `depth` levels below the main frame. Each
 * frame answers the probe in its own viewport; each parent says where the
 * child's content box starts in its own.
 */
function frameChain(wc, { depth = 1, probe = POINT, boxes = [], origin = "http://localhost:5000" } = {}) {
  const main = wc.mainFrame;
  const frames = [];
  let parent = main;
  for (let level = 0; level < depth; level += 1) {
    const frame = {
      processId: 9 + level,
      routingId: 3,
      url: `${origin}/frame-${level}.html`,
      origin,
      parent,
      executeJavaScript: jest.fn(),
    };
    frames.push(frame);
    parent = frame;
  }
  const answer = (frame, at) => (code) => {
    if (code.includes("parent.length")) return Promise.resolve(0);
    if (code.includes("contentWindow")) return Promise.resolve(boxes[at] ?? { left: 10, top: 20 });
    if (code.includes("el.disabled")) return Promise.resolve(frame === frames[frames.length - 1] ? probe : { fail: "gone" });
    if (code.includes("width: r.width")) return Promise.resolve({ x: 5, y: 6, width: 40, height: 10 });
    if (code.includes("scrollX")) return Promise.resolve({ x: 0, y: 300 });
    return Promise.resolve(true);
  };
  main.executeJavaScript.mockImplementation(answer(main, 0));
  frames.forEach((f, i) => f.executeJavaScript.mockImplementation(answer(f, i + 1)));
  main.framesInSubtree = [main, ...frames];
  const leaf = frames[frames.length - 1];
  return { leaf, target: { frameId: `${leaf.processId}:${leaf.routingId}`, selector: SEL } };
}

describe("elements inside frames", () => {
  test("a click in a cross-site frame lands at the element's point plus the frame's offset in the page", async () => {
    const { host, wc, cdp } = makeHost();
    const { target } = frameChain(wc, { boxes: [{ left: 26, top: 433 }] });
    await host.agentAct(null, { kind: "click" }, target, []);
    expect(inputSent(cdp)[0]).toEqual(["Input.dispatchMouseEvent", { type: "mouseMoved", x: 120 + 26, y: 48 + 433 }]);
    const asked = wc.mainFrame.executeJavaScript.mock.calls.map(([code]) => code).find((code) => code.includes("contentWindow"));
    expect(asked).toContain("[0,120,48]");
  });

  test("each frame up the chain adds its own offset", async () => {
    const { host, wc, cdp } = makeHost();
    const { target } = frameChain(wc, { depth: 2, boxes: [{ left: 100, top: 200 }, { left: 7, top: 9 }] });
    await host.agentAct(null, { kind: "click" }, target, []);
    expect(inputSent(cdp)[0][1]).toMatchObject({ x: 120 + 7 + 100, y: 48 + 9 + 200 });
  });

  test("typing in a frame clicks into the field there, selects in that frame, and inserts the text", async () => {
    const { host, wc, cdp } = makeHost();
    const { leaf, target } = frameChain(wc, { probe: { ...POINT, tag: "input", type: "text" } });
    await host.agentAct(null, { kind: "fill", text: "Ada", pressEnter: false }, target, []);
    expect(inputSent(cdp).map(([m, p]) => `${m}:${p.type || p.text}`)).toEqual([
      "Input.dispatchMouseEvent:mouseMoved",
      "Input.dispatchMouseEvent:mousePressed",
      "Input.dispatchMouseEvent:mouseReleased",
      "Input.insertText:Ada",
    ]);
    expect(leaf.executeJavaScript.mock.calls.some(([code]) => code.includes("el.select()"))).toBe(true);
  });

  test.each([
    [{ fail: "covered", by: '"Cookie banner"' }, "not_actionable", /covered by "Cookie banner"/],
    [{ fail: "hidden" }, "not_actionable", /frame is not visible/],
    [{ fail: "frame" }, "unsupported", /frame GAIA can't reach/],
  ])("the parent answering %j refuses the click, and nothing is sent", async (box, code, message) => {
    const { host, wc, cdp } = makeHost();
    const { target } = frameChain(wc, { boxes: [box] });
    await expect(host.agentAct(null, { kind: "click" }, target, [])).rejects.toMatchObject({ code, message: expect.stringMatching(message) });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("a frame that can't find itself in its parent is refused loudly", async () => {
    const { host, wc, cdp } = makeHost();
    const { leaf, target } = frameChain(wc);
    const answer = leaf.executeJavaScript.getMockImplementation();
    leaf.executeJavaScript.mockImplementation((code) => (code.includes("parent.length") ? Promise.resolve(-1) : answer(code)));
    await expect(host.agentAct(null, { kind: "click" }, target, [])).rejects.toMatchObject({ code: "unsupported" });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("acting in a frame from a site that is not approved is refused before the page is touched", async () => {
    const { host, wc, cdp } = makeHost();
    const { leaf, target } = frameChain(wc, { origin: "https://pay.example" });
    await expect(host.agentAct(null, { kind: "click" }, target, [])).rejects.toMatchObject({
      code: "off_scope",
      data: { origin: "https://pay.example" },
    });
    expect(leaf.executeJavaScript).not.toHaveBeenCalled();
    expect(inputSent(cdp)).toEqual([]);
    await expect(host.agentAct(null, { kind: "click" }, target, ["https://pay.example"])).resolves.toBeTruthy();
    expect(inputSent(cdp)).toHaveLength(3);
  });

  test("a gone frame is element_not_found", () => {
    const { wc } = makeHost();
    expect(() => frameFor(wc, "7:7")).toThrow(expect.objectContaining({ code: "element_not_found" }));
  });

  test("a picture of an element in a frame clips at its place in the page", async () => {
    const { host, wc, cdp } = makeHost();
    const { target } = frameChain(wc, { boxes: [{ left: 26, top: 33 }] });
    const { nativeImage } = require("electron");
    const image = { getSize: () => ({ width: 40, height: 10 }), toPNG: () => Buffer.from("png") };
    nativeImage.createFromBuffer = jest.fn(() => image);
    cdp.screenshot.mockResolvedValue(Buffer.from("png"));
    await expect(host.agentScreenshot(null, target, 1280, [])).resolves.toMatchObject({ width: 40, height: 10 });
    // Box (5, 6, 40x10) in the frame, plus the frame's offset, plus the page's scroll of 300.
    expect(cdp.screenshot).toHaveBeenCalledWith({ x: 5 + 26, y: 6 + 33 + 300, width: 40, height: 10 });
  });
});

describe("keys", () => {
  test("the whitelist is the same in input.cjs, the contract and the shared fixture", () => {
    const allowed = CONTRACT.split("export type AllowedKey =")[1].split(";")[0];
    expect(Object.keys(KEYS)).toEqual(fixture("keys.json").keys);
    expect([...allowed.matchAll(/"([A-Za-z]+)"/g)].map((m) => m[1]).sort()).toEqual([...fixture("keys.json").keys].sort());
  });

  test("a key off the list is refused by the parser", () => {
    expect(() => parseRequest("act", { ...requests.act_press, action: { kind: "press", key: "F12" } })).toThrow(/"key" must be one of/);
    expect(() => parseRequest("act", { ...requests.act_press, action: { kind: "press", key: "Meta+Q" } })).toThrow(/"key"/);
  });

  test.each([
    [{ ...requests.act_click, target: null }, /needs a "target"/],
    [{ ...requests.act_fill, action: { kind: "fill", text: "x" } }, /"pressEnter"/],
    [{ ...requests.act_click, action: { kind: "drag" } }, /"kind" must be one of click, fill, press/],
    [{ ...requests.viewport, size: { width: 50, height: 844, mobile: true } }, /"width"/],
  ])("a bad act or viewport body names what is wrong", (body, message) => {
    const op = "action" in body ? "act" : "viewport";
    expect(() => parseRequest(op, body)).toThrow(message);
  });
});

describe("the user takes over", () => {
  test("a tab the user paused refuses agent input and navigation with user_active", async () => {
    const { host, wc, cdp } = makeHost();
    host.setControl(host.activeTabId, "user");
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).rejects.toMatchObject({
      code: "user_active",
      message: expect.stringMatching(/Stop using the browser/),
    });
    await expect(host.agentNavigate(null, "http://127.0.0.1:5000/", [])).rejects.toMatchObject({ code: "user_active" });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
    expect(inputSent(cdp)).toEqual([]);
    host.setControl(host.activeTabId, "shared");
    pageSays(wc, POINT);
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).resolves.toBeTruthy();
  });

  test("a click by the user holds the agent off that tab for a few seconds", async () => {
    const { host, tab, wc } = makeHost();
    const now = jest.spyOn(Date, "now");
    try {
      now.mockReturnValue(1_000_000);
      wc.emit("input-event", {}, { type: "mouseMove" });
      expect(tab.userInputAt).toBe(0);
      wc.emit("input-event", {}, { type: "mouseDown" });
      now.mockReturnValue(1_000_000 + USER_PAUSE_MS - 1);
      await expect(host.agentAct(null, { kind: "press", key: "Tab" }, null, [])).rejects.toMatchObject({ code: "user_active" });
      now.mockReturnValue(1_000_000 + USER_PAUSE_MS + 1);
      await expect(host.agentAct(null, { kind: "press", key: "Tab" }, null, [])).resolves.toBeTruthy();
      expect(tab.state.control).toBe("shared");
    } finally {
      now.mockRestore();
    }
  });

  test("touching the page while GAIA is using it hands the tab to the user until Resume", async () => {
    const { host, tab, wc } = makeHost();
    pageSays(wc, POINT);
    await host.agentAct(null, { kind: "click" }, TARGET, []);
    wc.emit("input-event", {}, { type: "keyDown" });
    expect(tab.state.control).toBe("user");
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).rejects.toMatchObject({ code: "user_active" });
    host.setControl(tab.state.id, "shared");
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).resolves.toBeTruthy();
  });

  test("the agent's own input echoing back is not mistaken for the user", async () => {
    const { host, tab, wc, cdp } = makeHost();
    pageSays(wc, POINT);
    cdp.send.mockImplementation((method, params) => {
      if (params && params.type === "mousePressed") wc.emit("input-event", {}, { type: "mouseDown" });
      return Promise.resolve({});
    });
    await host.agentAct(null, { kind: "click" }, TARGET, []);
    expect(tab.state.control).toBe("shared");
    await expect(host.agentAct(null, { kind: "click" }, TARGET, [])).resolves.toBeTruthy();
  });
});

describe("private tabs", () => {
  test("never appear in the agent's tab list, and an active one reads as none", () => {
    const { host } = makeHost();
    const mine = host.activeTabId;
    const other = host.openTab("http://127.0.0.1:5000/second.html");
    host.setControl(other, "private");
    expect(host.listTabs()).toEqual({
      tabs: [expect.objectContaining({ id: mine, active: false, control: "shared" })],
      activeTabId: null,
    });
  });

  test("refuse every agent op, and a named one looks closed", async () => {
    const { host, wc } = makeHost();
    const id = host.activeTabId;
    host.setControl(id, "private");
    const handlers = createHandlers({ getHost: () => host, appVersion: "x" });
    for (const op of ["evaluate", "screenshot", "logs", "origin", "navigate"]) {
      await expect(handlers[op]({ ...requests[op], tabId: null })).rejects.toMatchObject({ code: "private_tab" });
    }
    await expect(handlers.act(parseRequest("act", requests.act_click).req)).rejects.toMatchObject({ code: "private_tab" });
    await expect(handlers.viewport(parseRequest("viewport", requests.viewport).req)).rejects.toMatchObject({ code: "private_tab" });
    await expect(handlers.tabs({ sessionId: "chat-1", op: "select", tabId: id })).rejects.toMatchObject({ code: "no_such_tab" });
    await expect(handlers.act({ ...parseRequest("act", requests.act_click).req, tabId: id })).rejects.toMatchObject({ code: "no_such_tab" });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });
});

describe("viewport", () => {
  const mobileDark = { size: { width: 390, height: 844, mobile: true }, colorScheme: "dark" };

  test("the agent's request emulates the screen and the color scheme", async () => {
    const { host, cdp } = makeHost();
    await expect(host.agentViewport(null, mobileDark)).resolves.toMatchObject({ applied: true, viewport: { ...mobileDark, source: "agent" } });
    expect(cdp.send).toHaveBeenCalledWith("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 0, mobile: true });
    expect(cdp.send).toHaveBeenCalledWith("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
  });

  test("the user's choice wins over a later agent request", async () => {
    const { host, cdp } = makeHost();
    const tablet = { size: { width: 820, height: 1180, mobile: true }, colorScheme: null };
    await host.setUserViewport(host.activeTabId, tablet);
    cdp.send.mockClear();
    await expect(host.agentViewport(null, mobileDark)).resolves.toMatchObject({ applied: false, viewport: { ...tablet, source: "user" } });
    expect(cdp.send).not.toHaveBeenCalled();
    await host.setUserViewport(host.activeTabId, null);
    expect(cdp.send).toHaveBeenCalledWith("Emulation.clearDeviceMetricsOverride");
    await expect(host.agentViewport(null, mobileDark)).resolves.toMatchObject({ applied: true });
  });
});
