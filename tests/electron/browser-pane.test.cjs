// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane's main-process half: tabs as WebContentsViews, placement
 * over the renderer's placeholder, hiding, the pages' navigation and
 * permission policy, the IPC surface, and dev QA mode.
 */

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow, WebContentsView, ipcMain } = require("electron");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const { PaneHost, PANE_PARTITION, toViewBounds } = require(path.join(WEBUI, "services/browser-pane/pane-host.cjs"));
const {
  attachTabPolicy,
  classifyNavigation,
  installSessionPolicy,
} = require(path.join(WEBUI, "services/browser-pane/navigation-policy.cjs"));
const { CHANNELS, createBrowserPaneService, fileRecentStore } = require(path.join(WEBUI, "services/browser-pane/ipc.cjs"));
const { resolveDevQaMode } = require(path.join(WEBUI, "services/dev-qa-mode.cjs"));
const { chromiumSwitches } = require(path.join(WEBUI, "services/chromium-switches.cjs"));

const BACKEND_PORT = 4321;

function fakeSession() {
  const ses = new EventEmitter();
  ses.setPermissionRequestHandler = jest.fn((h) => { ses.requestHandler = h; });
  ses.setPermissionCheckHandler = jest.fn((h) => { ses.checkHandler = h; });
  ses.setDevicePermissionHandler = jest.fn((h) => { ses.deviceHandler = h; });
  ses.webRequest = { onBeforeRequest: jest.fn((h) => { ses.beforeRequest = h; }) };
  ses.clearStorageData = jest.fn(() => Promise.resolve());
  ses.clearCache = jest.fn(() => Promise.resolve());
  ses.clearAuthCache = jest.fn(() => Promise.resolve());
  return ses;
}

function memoryRecentStore(initial = []) {
  const store = { pages: initial, load: () => store.pages.slice(), save: jest.fn((p) => { store.pages = p; }) };
  return store;
}

function makeHost(overrides = {}) {
  const window = new BrowserWindow();
  const published = [];
  const deps = {
    window,
    session: fakeSession(),
    publish: (s) => published.push(s),
    blockedPorts: () => [BACKEND_PORT],
    recentStore: memoryRecentStore(),
    goneChats: new Set(),
    onToggleRequest: jest.fn(),
    openExternal: jest.fn(),
    ...overrides,
  };
  const host = new PaneHost(deps);
  host.openTab(null);
  return { host, window, deps, published, last: () => published[published.length - 1] };
}

const viewOf = (host, id) => host.tabs.get(id).view;

beforeEach(() => {
  WebContentsView.instances.length = 0;
});

describe("tabs", () => {
  test("opening a tab loads the page in a sandboxed view on the pane partition with no preload", () => {
    const { host, window, last } = makeHost();
    const id = host.openTab("https://example.com/");
    const view = viewOf(host, id);
    expect(view.options.webPreferences).toEqual({
      partition: "persist:gaia-browser-pane",
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    });
    expect(PANE_PARTITION).toBe("persist:gaia-browser-pane");
    expect(view.webContents.loadURL).toHaveBeenCalledWith("https://example.com/");
    expect(window.contentView.children).toContain(view);
    expect(last().activeTabId).toBe(id);
    expect(last().tabs.map((t) => t.url)).toEqual([null, "https://example.com/"]);
  });

  test("closing the middle tab activates the one to its right and destroys its page", () => {
    const { host, window, last } = makeHost();
    const first = host.state().activeTabId;
    host.navigate(first, "https://a.test/");
    const middle = host.openTab("https://b.test/");
    const right = host.openTab("https://c.test/");
    host.selectTab(middle);
    const middleView = viewOf(host, middle);

    host.closeTab(middle);

    expect(last().tabs.map((t) => t.id)).toEqual([first, right]);
    expect(last().activeTabId).toBe(right);
    expect(middleView.webContents.close).toHaveBeenCalled();
    expect(window.contentView.children).not.toContain(middleView);
  });

  test("closing the last tab on the right activates its left neighbour", () => {
    const { host, last } = makeHost();
    const first = host.state().activeTabId;
    const second = host.openTab("https://b.test/");
    host.closeTab(second);
    expect(last().activeTabId).toBe(first);
  });

  test("closing the only tab leaves a new tab page, never an empty pane", () => {
    const { host, last } = makeHost();
    const only = host.state().activeTabId;
    host.closeTab(only);
    expect(last().tabs).toHaveLength(1);
    expect(last().tabs[0]).toMatchObject({ url: null });
    expect(last().activeTabId).not.toBe(only);
  });

  test("an unknown tab id fails loudly", () => {
    const { host } = makeHost();
    expect(() => host.selectTab("tab-99")).toThrow(/No browser pane tab "tab-99"/);
  });

  test("page events reach the mirrored state", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://a.test/");
    const wc = viewOf(host, id).webContents;
    wc.emit("did-start-loading");
    expect(last().tabs[1].loading).toBe(true);
    wc.navigationHistory.canGoBack.mockReturnValue(true);
    wc.emit("did-navigate", {}, "https://a.test/next");
    wc.emit("page-title-updated", {}, "Next page");
    wc.emit("did-stop-loading");
    expect(last().tabs[1]).toMatchObject({
      url: "https://a.test/next",
      title: "Next page",
      loading: false,
      canGoBack: true,
      canGoForward: false,
    });
    expect(last().recent[0]).toEqual({ url: "https://a.test/next", title: "Next page" });
  });

  test("a failed load is shown on the tab; an aborted one is not", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://a.test/");
    const wc = viewOf(host, id).webContents;
    wc.emit("did-fail-load", {}, -3, "ERR_ABORTED", "https://a.test/", true);
    expect(last().tabs[1].notice).toBeNull();
    wc.emit("did-fail-load", {}, -105, "ERR_NAME_NOT_RESOLVED", "https://nope.test/", true);
    expect(last().tabs[1].notice).toEqual({
      kind: "load-failed",
      url: "https://nope.test/",
      message: "ERR_NAME_NOT_RESOLVED (-105)",
    });
    wc.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
    expect(last().tabs[1].notice).toBeNull();
  });

  test("history buttons drive the active page", () => {
    const { host } = makeHost();
    const id = host.openTab("https://a.test/");
    const wc = viewOf(host, id).webContents;
    host.history(id, "back");
    host.history(id, "forward");
    host.history(id, "reload");
    host.history(id, "stop");
    expect(wc.navigationHistory.goBack).toHaveBeenCalledTimes(1);
    expect(wc.navigationHistory.goForward).toHaveBeenCalledTimes(1);
    expect(wc.reload).toHaveBeenCalledTimes(1);
    expect(wc.stop).toHaveBeenCalledTimes(1);
    expect(() => host.history(host.openTab(null), "back")).toThrow(/has no page/);
  });

  test("DevTools toggles docked in the tab's own view", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://a.test/");
    const wc = viewOf(host, id).webContents;
    host.toggleDevTools(id);
    expect(wc.openDevTools).toHaveBeenCalledWith({ mode: "bottom" });
    wc.emit("devtools-opened");
    expect(last().tabs[1].devToolsOpen).toBe(true);
    host.toggleDevTools(id);
    expect(wc.closeDevTools).toHaveBeenCalled();
  });

  test("Cmd or Ctrl+Shift+B inside a page asks the window to toggle the pane", () => {
    const { host, deps } = makeHost();
    const wc = viewOf(host, host.openTab("https://a.test/")).webContents;
    const event = { preventDefault: jest.fn() };
    wc.emit("before-input-event", event, { type: "keyDown", meta: true, shift: true, key: "B" });
    wc.emit("before-input-event", { preventDefault: jest.fn() }, { type: "keyDown", control: true, key: "b" });
    expect(deps.onToggleRequest).toHaveBeenCalledTimes(1);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  test("clearing browsing data clears the pane session, histories and recent pages", async () => {
    const { host, deps, last } = makeHost({ recentStore: memoryRecentStore([{ url: "https://x.test/", title: "X" }]) });
    const wc = viewOf(host, host.openTab("https://a.test/")).webContents;
    await host.clearBrowsingData();
    expect(deps.session.clearStorageData).toHaveBeenCalled();
    expect(deps.session.clearCache).toHaveBeenCalled();
    expect(wc.navigationHistory.clear).toHaveBeenCalled();
    expect(last().recent).toEqual([]);
    expect(deps.recentStore.save).toHaveBeenLastCalledWith([]);
  });

  test("open in system browser hands over web pages only", () => {
    const { host, deps } = makeHost();
    const id = host.openTab("https://a.test/");
    host.openExternal(id);
    expect(deps.openExternal).toHaveBeenCalledWith("https://a.test/");
    expect(() => host.openExternal(host.openTab(null))).toThrow(/no web page/);
  });
});

describe("placement", () => {
  test.each([
    ["inside the window", { x: 700, y: 90, width: 500, height: 600 }, 1, { x: 700, y: 90, width: 500, height: 600 }],
    ["zoomed 125%", { x: 400, y: 80, width: 320, height: 400 }, 1.25, { x: 500, y: 100, width: 400, height: 500 }],
    ["past the right and bottom edges", { x: 900, y: 700, width: 500, height: 300 }, 1, { x: 900, y: 700, width: 300, height: 100 }],
    ["past the top-left corner", { x: -20, y: -10, width: 100, height: 100 }, 1, { x: 0, y: 0, width: 80, height: 90 }],
    ["fully outside", { x: 1300, y: 0, width: 200, height: 200 }, 1, null],
    ["collapsed to nothing", { x: 100, y: 100, width: 0, height: 300 }, 1, null],
  ])("CSS rect %s becomes clamped window bounds", (_name, rect, zoom, expected) => {
    expect(toViewBounds(rect, zoom, { width: 1200, height: 800 })).toEqual(expected);
  });

  test("bounds follow the window's zoom factor", () => {
    const { host, window } = makeHost();
    window.webContents.getZoomFactor.mockReturnValue(1.5);
    const view = viewOf(host, host.openTab("https://a.test/"));
    host.setBounds({ x: 100, y: 40, width: 200, height: 300 });
    expect(view.bounds).toEqual({ x: 150, y: 60, width: 300, height: 450 });
  });

  test("only the active tab shows, and only while the pane is visible with bounds", () => {
    const { host } = makeHost();
    const a = host.openTab("https://a.test/");
    const b = host.openTab("https://b.test/");
    expect(viewOf(host, b).visible).toBe(false);
    host.setView(null, true);
    expect(viewOf(host, b).visible).toBe(false);
    host.setBounds({ x: 700, y: 90, width: 500, height: 600 });
    expect(viewOf(host, b).visible).toBe(true);
    expect(viewOf(host, a).visible).toBe(false);
    host.selectTab(a);
    expect(viewOf(host, a).visible).toBe(true);
    expect(viewOf(host, b).visible).toBe(false);
  });

  test("hiding the pane uses setVisible(false), never zero or offscreen bounds", () => {
    const { host } = makeHost();
    const view = viewOf(host, host.openTab("https://a.test/"));
    host.setView(null, true);
    host.setBounds({ x: 700, y: 90, width: 500, height: 600 });
    host.setView(null, false);
    host.setBounds({ x: 700, y: 90, width: 0, height: 0 });
    host.setBounds({ x: 5000, y: 5000, width: 500, height: 600 });
    expect(view.visible).toBe(false);
    for (const [b] of view.setBounds.mock.calls) {
      expect(b.width).toBeGreaterThan(0);
      expect(b.height).toBeGreaterThan(0);
      expect(b.x).toBeGreaterThanOrEqual(0);
      expect(b.x + b.width).toBeLessThanOrEqual(1200);
    }
  });

  test("an app overlay hides the live page and returns a still frame of it", async () => {
    const { host, last } = makeHost();
    const view = viewOf(host, host.openTab("https://a.test/"));
    host.setView(null, true);
    host.setBounds({ x: 700, y: 90, width: 500, height: 600 });

    const frame = await host.setOccluded(true);
    expect(frame).toBe("data:image/png;base64,frame");
    expect(view.visible).toBe(false);
    expect(last().occluded).toBe(true);

    expect(await host.setOccluded(false)).toBeNull();
    expect(view.visible).toBe(true);
  });

  test("an overlay over a new tab page has no frame to freeze", async () => {
    const { host } = makeHost();
    host.setView(null, true);
    host.setBounds({ x: 700, y: 90, width: 500, height: 600 });
    expect(await host.setOccluded(true)).toBeNull();
  });
});

describe("navigation policy", () => {
  const ctx = { blockedPorts: [BACKEND_PORT] };
  test.each([
    ["https://example.com/", true],
    ["http://example.com/path?q=1", true],
    ["http://localhost:5173/", true],
    ["http://127.0.0.1:8080/", true],
    ["about:blank", true],
    ["file:///etc/hosts", false],
    ["chrome://settings", false],
    ["devtools://devtools/bundled/inspector.html", false],
    ["javascript:alert(1)", false],
    ["data:text/html,<p>hi</p>", false],
    ["ftp://example.com/", false],
    ["gaia://hub/install/x", false],
    [`http://127.0.0.1:${BACKEND_PORT}/api/health`, false],
    [`http://localhost:${BACKEND_PORT}/`, false],
    [`http://app.localhost:${BACKEND_PORT}/`, false],
    [`http://[::1]:${BACKEND_PORT}/`, false],
    [`http://0.0.0.0:${BACKEND_PORT}/`, false],
    [`http://example.com:${BACKEND_PORT}/`, true],
    ["not a url", false],
  ])("%s allowed: %s", (url, allowed) => {
    const verdict = classifyNavigation(url, ctx);
    expect(verdict.allowed).toBe(allowed);
    if (!allowed) expect(verdict.message).toMatch(/\S/);
  });

  test.each([
    ["media", false],
    ["geolocation", false],
    ["notifications", false],
    ["midi", false],
    ["clipboard-read", false],
    ["fullscreen", false],
    ["clipboard-sanitized-write", true],
  ])("a page asking for %s gets %s", (permission, granted) => {
    const ses = fakeSession();
    const denied = jest.fn();
    installSessionPolicy(ses, { blockedPorts: () => [BACKEND_PORT], onPermissionDenied: denied, downloadsDir: () => "/dl" });
    const callback = jest.fn();
    const wc = { getURL: () => "https://site.test/page" };
    ses.requestHandler(wc, permission, callback, { requestingUrl: "https://site.test/page" });
    expect(callback).toHaveBeenCalledWith(granted);
    expect(ses.checkHandler(wc, permission)).toBe(granted);
    if (granted) expect(denied).not.toHaveBeenCalled();
    else expect(denied).toHaveBeenCalledWith(wc, permission, "https://site.test");
  });

  test("device pickers are refused and downloads always ask where to save", () => {
    const ses = fakeSession();
    installSessionPolicy(ses, { blockedPorts: () => [BACKEND_PORT], onPermissionDenied: jest.fn(), downloadsDir: () => "/dl" });
    expect(ses.deviceHandler({ deviceType: "usb" })).toBe(false);
    const item = { setSaveDialogOptions: jest.fn(), setSavePath: jest.fn(), getFilename: () => "report.pdf" };
    ses.emit("will-download", {}, item);
    expect(item.setSavePath).not.toHaveBeenCalled();
    expect(item.setSaveDialogOptions).toHaveBeenCalledWith(
      expect.objectContaining({ defaultPath: path.join("/dl", "report.pdf") })
    );
  });

  test.each([
    [`http://127.0.0.1:${BACKEND_PORT}/api/sessions`, true],
    [`ws://localhost:${BACKEND_PORT}/socket`, true],
    ["ws://localhost:5173/hmr", false],
    ["https://cdn.test/app.js", false],
    ["data:image/png;base64,AAAA", false],
    ["blob:https://site.test/1234", false],
  ])("any request to %s is cancelled: %s", (url, cancel) => {
    const ses = fakeSession();
    installSessionPolicy(ses, { blockedPorts: () => [BACKEND_PORT], onPermissionDenied: jest.fn(), downloadsDir: () => "/dl" });
    const callback = jest.fn();
    ses.beforeRequest({ url }, callback);
    expect(callback).toHaveBeenCalledWith({ cancel });
  });

  test("popups open as pane tabs and blocked links say why", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://a.test/");
    const wc = viewOf(host, id).webContents;

    expect(wc.windowOpenHandler({ url: "https://a.test/popup" })).toEqual({ action: "deny" });
    expect(last().tabs.map((t) => t.url)).toContain("https://a.test/popup");
    expect(last().activeTabId).not.toBe(id);

    host.selectTab(id);
    const event = { url: "file:///etc/hosts", preventDefault: jest.fn() };
    wc.emit("will-navigate", event);
    expect(event.preventDefault).toHaveBeenCalled();
    const tab = last().tabs.find((t) => t.id === id);
    expect(tab.notice).toMatchObject({ kind: "blocked", url: "file:///etc/hosts" });

    const redirect = { url: `http://localhost:${BACKEND_PORT}/`, preventDefault: jest.fn() };
    wc.emit("will-redirect", redirect);
    expect(redirect.preventDefault).toHaveBeenCalled();
  });

  test("a file: link Chromium refuses inside a page is still reported", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://a.test/");
    viewOf(host, id).webContents.emit("console-message", {
      message: "Not allowed to load local resource: file:///etc/hosts",
    });
    expect(last().tabs.find((t) => t.id === id).notice).toMatchObject({
      kind: "blocked",
      url: "file:///etc/hosts",
      message: expect.stringMatching(/only http and https/),
    });
  });

  test("a typed address the policy refuses is reported and never loaded", () => {
    const { host, last } = makeHost();
    const id = host.state().activeTabId;
    host.navigate(id, "file:///etc/passwd");
    expect(WebContentsView.instances).toHaveLength(0);
    expect(last().tabs[0].notice).toMatchObject({ kind: "blocked", url: "file:///etc/passwd" });
  });

  test("a refused permission is shown on the tab that asked", () => {
    const { host, last } = makeHost();
    const id = host.openTab("https://site.test/");
    host.notePermissionDenied(viewOf(host, id).webContents, "media", "https://site.test");
    expect(last().tabs.find((t) => t.id === id).notice).toEqual({
      kind: "permission-denied",
      permission: "media",
      origin: "https://site.test",
    });
  });

  test("the tab policy works on a bare webContents", () => {
    const wc = new EventEmitter();
    wc.setWindowOpenHandler = jest.fn();
    const openTab = jest.fn();
    const onBlocked = jest.fn();
    attachTabPolicy(wc, { blockedPorts: () => [], openTab, onBlocked });
    const handler = wc.setWindowOpenHandler.mock.calls[0][0];
    handler({ url: "chrome://gpu" });
    expect(openTab).not.toHaveBeenCalled();
    expect(onBlocked).toHaveBeenCalledWith("chrome://gpu", expect.stringMatching(/http and https/));
  });
});

describe("IPC", () => {
  function startService() {
    const window = new BrowserWindow();
    const userDataDir = fs.mkdtempSync(path.join(require("os").tmpdir(), "pane-ipc-"));
    const service = createBrowserPaneService({
      ipcMain,
      session: fakeSession(),
      shell: { openExternal: jest.fn(() => Promise.resolve()) },
      userDataDir,
      downloadsDir: () => "/dl",
      blockedPorts: () => [BACKEND_PORT],
      devServers,
    });
    const host = service.attach(window);
    const invoke = (channel, sender, ...args) => ipcMain._handlers.get(channel)({ sender }, ...args);
    return { window, host, invoke, service };
  }
  const running = { key: "0123456789abcdef", name: "web", port: 41730, status: "ready" };
  const devServers = { list: jest.fn(() => [running]), stop: jest.fn(() => Promise.resolve(running)) };

  test("the state carries the app's dev servers, and Stop in the menu reaches the supervisor", async () => {
    const { window, invoke, service } = startService();
    expect((await invoke(CHANNELS.getState, window.webContents)).devServers).toEqual([running]);
    window.webContents.send.mockClear();
    service.publish();
    expect(window.webContents.send).toHaveBeenLastCalledWith(CHANNELS.state, expect.objectContaining({ devServers: [running] }));
    await invoke(CHANNELS.stopDevServer, window.webContents, running.key);
    expect(devServers.stop).toHaveBeenCalledWith(running.key);
    expect(() => invoke(CHANNELS.stopDevServer, { send: jest.fn() }, running.key)).toThrow(/Only the GAIA window/);
  });

  test("the app window drives the pane and gets every change pushed", async () => {
    const { window, invoke } = startService();
    const id = await invoke(CHANNELS.openTab, window.webContents, "https://a.test/");
    expect(id).toMatch(/^tab-/);
    expect(window.webContents.send).toHaveBeenLastCalledWith(
      CHANNELS.state,
      expect.objectContaining({ activeTabId: id })
    );
    const state = await invoke(CHANNELS.getState, window.webContents);
    expect(state.tabs.map((t) => t.url)).toEqual(["https://a.test/"]);
  });

  test("any other sender is refused", () => {
    const { invoke } = startService();
    const stranger = { send: jest.fn() };
    expect(() => invoke(CHANNELS.openTab, stranger, "https://a.test/")).toThrow(/Only the GAIA window/);
  });

  test("malformed arguments fail at the boundary", () => {
    const { window, invoke } = startService();
    expect(() => invoke(CHANNELS.navigate, window.webContents, "tab-1", 42)).toThrow(/url must be/);
    expect(() => invoke(CHANNELS.history, window.webContents, "tab-1", "sideways")).toThrow(/unknown history action/);
  });

  test("preload exposes exactly the service's channels", () => {
    const preload = fs.readFileSync(path.join(WEBUI, "preload.cjs"), "utf8");
    const block = preload.slice(preload.indexOf('exposeInMainWorld("gaiaBrowserPane"'));
    const used = new Set([...block.slice(0, block.indexOf("});")).matchAll(/"(pane:[a-z-]+)"/g)].map((m) => m[1]));
    expect([...used].sort()).toEqual(Object.values(CHANNELS).sort());
  });

  test("recent pages survive a restart and a corrupt file is ignored with an error", () => {
    const dir = fs.mkdtempSync(path.join(require("os").tmpdir(), "pane-recent-"));
    const file = path.join(dir, "recent.json");
    const store = fileRecentStore(file);
    expect(store.load()).toEqual([]);
    store.save([{ url: "https://a.test/", title: "A" }]);
    expect(fileRecentStore(file).load()).toEqual([{ url: "https://a.test/", title: "A" }]);
    fs.writeFileSync(file, "{not json");
    const error = jest.spyOn(console, "error").mockImplementation(() => {});
    expect(store.load()).toEqual([]);
    expect(error).toHaveBeenCalledWith(expect.stringContaining(file));
    error.mockRestore();
  });
});

describe("dev QA mode", () => {
  const exists = (p) => p === "/venv/bin/gaia";

  test("an unpackaged build with GAIA_BIN uses that backend", () => {
    expect(resolveDevQaMode({ isPackaged: false, env: { GAIA_BIN: "/venv/bin/gaia" }, exists })).toEqual({
      gaiaBin: "/venv/bin/gaia",
    });
  });

  test("a packaged build never enters it", () => {
    expect(resolveDevQaMode({ isPackaged: true, env: { GAIA_BIN: "/venv/bin/gaia" }, exists })).toBeNull();
  });

  test("without GAIA_BIN the normal install flow runs", () => {
    expect(resolveDevQaMode({ isPackaged: false, env: {}, exists })).toBeNull();
  });

  test("a GAIA_BIN that does not exist fails loudly", () => {
    expect(() => resolveDevQaMode({ isPackaged: false, env: { GAIA_BIN: "/missing/gaia" }, exists })).toThrow(
      /GAIA_BIN is set to "\/missing\/gaia", which does not exist/
    );
  });

  test("main.cjs skips the installer, protocol registration, pidfile and orphan cleanup in it", () => {
    const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");
    expect(main).toContain("const devQa = resolveDevQaMode({ isPackaged: app.isPackaged, env: process.env });");
    expect(main).toContain("if (!devQa) registerProtocolHandler();");
    expect(main).toContain("const bootstrapOk = devQa ? true : await prepareInstalledBackend();");
    expect(main).toContain("const gaiaCmd = devQa ? devQa.gaiaBin : backendInstaller.findGaiaBin();");
    expect(main).toContain("if (!devQa) await cleanupOrphanedBackend();");
    expect(main).toMatch(/if \(!devQa\) \{\n\s+try \{\n[^]*?backend\.pid/);
    const prep = main.slice(main.indexOf("async function prepareInstalledBackend("), main.indexOf("app.whenReady().then("));
    expect(prep).toContain("bootstrapBackend()");
    expect(prep).toContain("cleanupOrphanedBackend()");
    expect(prep).toContain("seedBundledAgents()");
  });
});

describe("no remote debugging port", () => {
  test.each(["darwin", "win32", "linux"])("no Chromium switch on %s opens one", (platform) => {
    for (const env of [{}, { GAIA_DISABLE_GPU: "1" }]) {
      const names = chromiumSwitches({ platform, env }).map(([name]) => name);
      expect(names.some((n) => /remote-debugging/.test(n))).toBe(false);
    }
  });

  test("no main-process source appends one", () => {
    const files = ["main.cjs", "preload.cjs"];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(path.join(WEBUI, dir), { withFileTypes: true })) {
        const rel = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(rel);
        else if (/\.c?js$/.test(entry.name)) files.push(rel);
      }
    };
    walk("services");
    const offenders = files.filter((f) => /remote-debugging/.test(fs.readFileSync(path.join(WEBUI, f), "utf8")));
    expect(offenders).toEqual([]);
  });
});
