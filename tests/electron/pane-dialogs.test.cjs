// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Page boxes (alert, confirm, prompt, "leave this page?") and browser_eval's
 * scripts in the browser pane. Each test drives the page the way Electron
 * does (its internal -run-dialog event, will-prevent-unload) and asserts what
 * the page was answered and what reached it.
 */

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow } = require("electron");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const PANE = path.join(WEBUI, "services", "browser-pane");
const FIXTURES = path.resolve(__dirname, "..", "fixtures", "browser_pane", "bridge");
const { PaneHost, RUN_DIALOG, CANCEL_DIALOGS, LEAVE_MESSAGE } = require(path.join(PANE, "pane-host.cjs"));
const { createHandlers, parseRequest } = require(path.join(PANE, "bridge-server.cjs"));

const fixture = (name) => JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
const requests = fixture("requests.json");
const SEL = '[data-gaia-ref="g1e2"]';
const TARGET = { frameId: null, selector: SEL };
const POINT = { x: 120, y: 48, tag: "button", type: "button", editable: false };
const PAGE = "http://127.0.0.1:5000/dialogs.html";

// Electron versions whose lib/browser/api/web-contents.ts was read for RUN_DIALOG and CANCEL_DIALOGS.
const READ_IN_ELECTRON = ["45"];

function makeHost(url = PAGE) {
  const ses = new EventEmitter();
  ses.cookies = { get: jest.fn(() => Promise.resolve([])) };
  const cdps = [];
  const host = new PaneHost({
    window: new BrowserWindow(),
    session: ses,
    publish: jest.fn(),
    blockedPorts: () => [],
    recentStore: { load: () => new Map(), save: () => {} },
    goneChats: new Set(),
    onToggleRequest: jest.fn(),
    onShowRequest: jest.fn(),
    openExternal: jest.fn(),
    createCdp: () => {
      const cdp = { send: jest.fn(() => Promise.resolve({})), onNavigationStarted: jest.fn(), logs: jest.fn(), screenshot: jest.fn() };
      cdps.push(cdp);
      return cdp;
    },
  });
  host.setView("chat-1", false);
  host.openTab(url);
  const tab = host.tabs.get(host.activeTabId);
  const wc = tab.view.webContents;
  wc.mainFrame.executeJavaScript.mockImplementation((code) => Promise.resolve(code.includes("el.disabled") ? POINT : true));
  return { host, tab, wc, cdp: cdps[0] };
}

/** The page opens a box; resolves with what it was answered. */
function pageOpens(wc, dialogType, messageText, defaultPromptText = "") {
  return new Promise((resolve) => {
    wc.emit(RUN_DIALOG, { frame: { url: wc.getURL() }, dialogType, messageText, defaultPromptText }, (accept, text) => resolve({ accept, text }));
  });
}

/** Clicking the target opens `dialogType`; the click's last event waits on the box, as a frozen page does. */
function clickOpens(wc, cdp, dialogType, message) {
  const answered = { value: null };
  cdp.send.mockImplementation((method, params) => {
    if (params && params.type === "mouseReleased") {
      return pageOpens(wc, dialogType, message).then((a) => {
        answered.value = a;
        return {};
      });
    }
    return Promise.resolve({});
  });
  return answered;
}

const inputSent = (cdp) => cdp.send.mock.calls.filter(([method]) => method.startsWith("Input."));
const flush = () => new Promise((r) => setImmediate(r));

describe("the pane answers page boxes, not Electron", () => {
  test(`Electron's ${RUN_DIALOG} event was read for this Electron version`, () => {
    const spec = JSON.parse(fs.readFileSync(path.join(WEBUI, "package.json"), "utf8")).devDependencies.electron;
    const major = /(\d+)/.exec(spec)[1];
    // A new Electron must be checked: lib/browser/api/web-contents.ts still emits RUN_DIALOG and CANCEL_DIALOGS
    // with (info, callback), and RunBeforeUnloadDialog still emits will-prevent-unload. Then add its major here.
    expect(READ_IN_ELECTRON).toContain(major);
  });

  test("each tab's own handler replaces Electron's native box", () => {
    const { wc } = makeHost();
    expect(wc.listenerCount(RUN_DIALOG)).toBe(1);
    expect(jest.isMockFunction(wc.listeners(RUN_DIALOG)[0])).toBe(false);
  });

  test("a box the page opens is the tab's state until it is answered", async () => {
    const { host, tab, wc } = makeHost();
    const answered = pageOpens(wc, "confirm", "Save this draft?");
    expect(tab.state.dialog).toEqual({ type: "confirm", message: "Save this draft?", defaultText: "", url: PAGE, openedBy: "page" });
    expect(host.deps.publish).toHaveBeenLastCalledWith(expect.objectContaining({ tabs: [expect.objectContaining({ dialog: tab.state.dialog })] }));
    host.userAnswerDialog(tab.state.id, true, null);
    await expect(answered).resolves.toEqual({ accept: true, text: "" });
    expect(tab.state.dialog).toBeNull();
  });

  test("a prompt is answered with the user's text, or its own starting text", async () => {
    const { host, tab, wc } = makeHost();
    let answered = pageOpens(wc, "prompt", "What should we call you?", "friend");
    host.userAnswerDialog(tab.state.id, true, "Ada");
    await expect(answered).resolves.toEqual({ accept: true, text: "Ada" });
    answered = pageOpens(wc, "prompt", "What should we call you?", "friend");
    host.userAnswerDialog(tab.state.id, true, null);
    await expect(answered).resolves.toEqual({ accept: true, text: "friend" });
    answered = pageOpens(wc, "prompt", "What should we call you?", "friend");
    host.userAnswerDialog(tab.state.id, false, "ignored");
    await expect(answered).resolves.toEqual({ accept: false, text: "" });
  });

  test("Chromium closing the box (the page went away) clears it", () => {
    const { tab, wc } = makeHost();
    pageOpens(wc, "alert", "Bye");
    wc.emit(CANCEL_DIALOGS, { resetState: false });
    expect(tab.state.dialog).toBeNull();
  });
});

describe("the agent and page boxes", () => {
  test("a click that opens a box returns at once with the box, instead of timing out", async () => {
    const { host, wc, cdp } = makeHost();
    clickOpens(wc, cdp, "confirm", "Delete account? This cannot be undone.");
    const started = Date.now();
    const reply = await host.agentAct("chat-1", null, { kind: "click" }, TARGET, []);
    expect(Date.now() - started).toBeLessThan(2000);
    expect(reply.dialog).toEqual({ type: "confirm", message: "Delete account? This cannot be undone.", defaultText: "", url: PAGE, openedBy: "agent" });
  });

  test("the rest of an action never lands after its box is answered", async () => {
    const { host, tab, wc, cdp } = makeHost();
    wc.mainFrame.executeJavaScript.mockImplementation((code) => Promise.resolve(code.includes("el.disabled") ? { ...POINT, tag: "input", type: "text" } : true));
    clickOpens(wc, cdp, "alert", "Focus moved");
    const reply = await host.agentAct("chat-1", null, { kind: "fill", text: "Ada", pressEnter: true }, TARGET, []);
    expect(reply.dialog.type).toBe("alert");
    host.userAnswerDialog(tab.state.id, true, null);
    await flush();
    await flush();
    expect(inputSent(cdp).map(([, p]) => p.type || p.text)).toEqual(["mouseMoved", "mousePressed", "mouseReleased"]);
  });

  test.each(["evaluate", "screenshot", "navigate"])("%s while a box is open says so and does not touch the page", async (op) => {
    const { host, wc } = makeHost();
    pageOpens(wc, "confirm", "Save this draft?");
    wc.mainFrame.executeJavaScript.mockClear();
    const handlers = createHandlers({ getHost: () => host, appVersion: "x" });
    await expect(handlers[op](parseRequest(op, requests[op]).req)).rejects.toMatchObject({
      code: "dialog_open",
      data: { dialog: expect.objectContaining({ type: "confirm", message: "Save this draft?" }) },
    });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });

  test("an act while a box is open is refused before any input", async () => {
    const { host, wc, cdp } = makeHost();
    pageOpens(wc, "alert", "Hi");
    await expect(host.agentAct("chat-1", null, { kind: "click" }, TARGET, [])).rejects.toMatchObject({ code: "dialog_open" });
    expect(inputSent(cdp)).toEqual([]);
  });

  test("the agent answers: OK with prompt text, and the reply names the box it answered", async () => {
    const { host, wc } = makeHost();
    const answered = pageOpens(wc, "prompt", "What should we call you?", "friend");
    const handlers = createHandlers({ getHost: () => host, appVersion: "x" });
    const reply = await handlers.dialog(parseRequest("dialog", requests.dialog_answer).req);
    await expect(answered).resolves.toEqual({ accept: true, text: "Ada" });
    expect(reply).toMatchObject({ answered: { type: "prompt", message: "What should we call you?" }, dialog: null, url: PAGE });
    expect(Object.keys(reply).sort()).toEqual(Object.keys(fixture("dialog_answered.json")).sort());
  });

  test("accepting a confirm from a site that is not approved is refused; dismissing it and closing an alert are not", async () => {
    const remote = "https://shop.example/cart";
    const { host, wc } = makeHost(remote);
    let answered = pageOpens(wc, "confirm", "Empty the cart?");
    await expect(host.agentAnswerDialog("chat-1", null, true, null, [])).rejects.toMatchObject({ code: "off_scope", data: { origin: "https://shop.example" } });
    await host.agentAnswerDialog("chat-1", null, false, null, []);
    await expect(answered).resolves.toEqual({ accept: false, text: "" });
    answered = pageOpens(wc, "confirm", "Empty the cart?");
    await host.agentAnswerDialog("chat-1", null, true, null, ["https://shop.example"]);
    await expect(answered).resolves.toEqual({ accept: true, text: "" });
    answered = pageOpens(wc, "alert", "Saved");
    await host.agentAnswerDialog("chat-1", null, true, null, []);
    await expect(answered).resolves.toMatchObject({ accept: true });
  });

  test("the agent never answers a box in a tab the user holds", async () => {
    const { host, tab, wc } = makeHost();
    pageOpens(wc, "confirm", "Save this draft?");
    host.setControl(tab.state.id, "user");
    await expect(host.agentAnswerDialog("chat-1", null, false, null, [])).rejects.toMatchObject({ code: "user_active" });
    expect(host.releaseDialogs("chat-1")).toEqual({ dismissed: 0 });
    expect(tab.state.dialog).not.toBeNull();
  });

  test("no box to answer is said plainly", async () => {
    const { host } = makeHost();
    await expect(host.agentAnswerDialog("chat-1", null, true, null, [])).rejects.toMatchObject({ code: "not_actionable", message: expect.stringMatching(/no box open/) });
  });

  test("the end of the agent's turn dismisses the box its own action opened, and only that one", async () => {
    const { host, tab, wc, cdp } = makeHost();
    const other = host.openTab("http://127.0.0.1:5000/second.html");
    const otherWc = host.tabs.get(other).view.webContents;
    const pageBox = pageOpens(otherWc, "alert", "From a timer");
    host.selectTab(tab.state.id);
    const click = clickOpens(wc, cdp, "confirm", "Save this draft?");
    await host.agentAct("chat-1", null, { kind: "click" }, TARGET, []);
    const handlers = createHandlers({ getHost: () => host, appVersion: "x" });
    await expect(handlers.dialog(parseRequest("dialog", requests.dialog_release).req)).resolves.toEqual({ dismissed: 1 });
    await flush();
    expect(click.value).toEqual({ accept: false, text: "" });
    expect(host.tabs.get(other).state.dialog).toMatchObject({ openedBy: "page" });
    host.userAnswerDialog(other, true, null);
    await expect(pageBox).resolves.toMatchObject({ accept: true });
  });

  test("switching the window to another chat dismisses the boxes the old chat's agent left", async () => {
    const { host, tab, wc, cdp } = makeHost();
    const click = clickOpens(wc, cdp, "alert", "Done");
    await host.agentAct("chat-1", null, { kind: "click" }, TARGET, []);
    host.setView("chat-2", false);
    await flush();
    expect(click.value).toEqual({ accept: false, text: "" });
    expect(tab.state.dialog).toBeNull();
  });
});

describe('"leave this page?"', () => {
  function leaveAsked(wc) {
    const event = { preventDefault: jest.fn() };
    wc.emit("will-prevent-unload", event);
    return event;
  }

  test("an agent navigation the page holds up reports the box, and accepting it leaves", async () => {
    const { host, tab, wc } = makeHost("http://127.0.0.1:5000/unsaved.html");
    const to = "http://127.0.0.1:5000/second.html";
    wc.loadURL.mockImplementationOnce(() => {
      leaveAsked(wc);
      return Promise.reject(new Error("ERR_ABORTED (-3)"));
    });
    await expect(host.agentNavigate("chat-1", null, to, [])).rejects.toMatchObject({
      code: "dialog_open",
      data: { dialog: { type: "beforeunload", message: LEAVE_MESSAGE, url: "http://127.0.0.1:5000/unsaved.html", openedBy: "agent", defaultText: "" } },
    });
    expect(tab.state.dialog.type).toBe("beforeunload");
    wc.loadURL.mockImplementationOnce((url) => {
      expect(leaveAsked(wc).preventDefault).toHaveBeenCalled();
      wc._url = url;
      return Promise.resolve();
    });
    const reply = await host.agentAnswerDialog("chat-1", null, true, null, []);
    expect(wc.loadURL).toHaveBeenLastCalledWith(to);
    expect(reply).toMatchObject({ answered: { type: "beforeunload" }, url: to, dialog: null });
  });

  test("staying keeps the page and asks again next time", async () => {
    const { host, tab, wc } = makeHost("http://127.0.0.1:5000/unsaved.html");
    host.navigate(tab.state.id, "http://127.0.0.1:5000/second.html");
    leaveAsked(wc);
    expect(tab.state.dialog).toMatchObject({ type: "beforeunload", openedBy: "page" });
    const loads = wc.loadURL.mock.calls.length;
    host.userAnswerDialog(tab.state.id, false, null);
    expect(wc.loadURL.mock.calls.length).toBe(loads);
    expect(leaveAsked(wc).preventDefault).not.toHaveBeenCalled();
  });

  test("a leave the page started itself can't be repeated, so accepting lets the next one through", () => {
    const { host, tab, wc } = makeHost("http://127.0.0.1:5000/unsaved.html");
    wc.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
    leaveAsked(wc);
    const loads = wc.loadURL.mock.calls.length;
    host.userAnswerDialog(tab.state.id, true, null);
    expect(wc.loadURL.mock.calls.length).toBe(loads);
    expect(leaveAsked(wc).preventDefault).toHaveBeenCalled();
    expect(leaveAsked(wc).preventDefault).not.toHaveBeenCalled();
  });
});

describe("browser_eval scripts", () => {
  test("run in the page's main world on loopback, and come back as capped JSON", async () => {
    const { host, wc } = makeHost("http://127.0.0.1:5000/app-state.html");
    wc.mainFrame.executeJavaScript.mockResolvedValue({ text: '"c-4821"', truncated: false });
    const handlers = createHandlers({ getHost: () => host, appVersion: "x" });
    const reply = await handlers.script(parseRequest("script", requests.script).req);
    expect(reply).toEqual(fixture("script_ok.json"));
    const [code, gesture] = wc.mainFrame.executeJavaScript.mock.calls[0];
    expect(code).toContain("localStorage.getItem('cartId')");
    expect(code).toContain(".slice(0, 4000)");
    expect(gesture).toBe(false);
  });

  test("a page error comes back as text", async () => {
    const { host, wc } = makeHost("http://127.0.0.1:5000/app-state.html");
    wc.mainFrame.executeJavaScript.mockResolvedValue({ error: "ReferenceError: cart is not defined" });
    await expect(host.agentScript("chat-1", null, "cart.length")).resolves.toEqual(fixture("script_error.json"));
    wc.mainFrame.executeJavaScript.mockRejectedValue(new SyntaxError("Unexpected token ')'"));
    await expect(host.agentScript("chat-1", null, "if (")).resolves.toMatchObject({ ok: false, error: "Unexpected token ')'" });
  });

  test("a trailing semicolon still makes an expression", async () => {
    const { host, wc } = makeHost("http://127.0.0.1:5000/app-state.html");
    wc.mainFrame.executeJavaScript.mockResolvedValue({ text: "1", truncated: false });
    await host.agentScript("chat-1", null, "  window.app.cart.length;  ");
    expect(wc.mainFrame.executeJavaScript.mock.calls[0][0]).toMatch(/\nwindow\.app\.cart\.length\n/);
  });

  test("anything off this computer is refused without running, and never asked about", async () => {
    const { host, wc } = makeHost("https://shop.example/");
    await expect(host.agentScript("chat-1", null, "document.cookie")).rejects.toMatchObject({ code: "unsupported", message: expect.stringMatching(/only on the user's own app/) });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });

  test("a tab the user holds refuses scripts like clicks", async () => {
    const { host, tab, wc } = makeHost("http://127.0.0.1:5000/app-state.html");
    host.setControl(tab.state.id, "user");
    await expect(host.agentScript("chat-1", null, "1")).rejects.toMatchObject({ code: "user_active" });
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });

  test("the expression is bounded on the wire", () => {
    expect(() => parseRequest("script", { ...requests.script, expression: "x".repeat(2001) })).toThrow(/"expression"/);
    expect(() => parseRequest("dialog", { ...requests.dialog_answer, accept: "yes" })).toThrow(/"accept"/);
  });
});
