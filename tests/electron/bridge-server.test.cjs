// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane's bridge: who may call it, what it parses, the discovery
 * file, and PaneHost's agent ops behind it (scope, blocked navigation, the
 * busy strip). Request bodies come from tests/fixtures/browser_pane/bridge,
 * the same files the Python tests check PaneDriver against.
 */

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow } = require("electron");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const PANE = path.join(WEBUI, "services", "browser-pane");
const FIXTURES = path.resolve(__dirname, "..", "fixtures", "browser_pane", "bridge");
const { BRIDGE_PROTOCOL, PARSERS, createHandlers, devServerOrigins, discoveryPath, parseRequest, startBridge } = require(path.join(PANE, "bridge-server.cjs"));
const { BridgeError, PaneHost, inScope } = require(path.join(PANE, "pane-host.cjs"));
const { isLocalPageHost } = require(path.join(PANE, "navigation-policy.cjs"));
const { Ring, TabCdp } = require(path.join(PANE, "cdp-session.cjs"));

const fixture = (name) => JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
const CONTRACT = fs.readFileSync(path.join(PANE, "contract.d.ts"), "utf8");
const quiet = { log: () => {}, error: () => {} };

function post(port, op, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const data = typeof body === "string" ? body : JSON.stringify(body);
    const req = http.request(
      { host: "127.0.0.1", port, path: `/v1/${op}`, method: "POST", headers: { "content-type": "application/json", ...headers } },
      (res) => {
        let text = "";
        res.on("data", (c) => { text += c; });
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: text ? JSON.parse(text) : null }));
      }
    );
    req.on("error", reject);
    req.end(data);
  });
}

const tempDirs = [];
afterAll(() => {
  for (const dir of tempDirs) fs.rmSync(dir, { recursive: true, force: true });
});

async function running(handlers = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-bridge-"));
  tempDirs.push(dir);
  const file = path.join(dir, "run", "browser-pane.json");
  const bridge = await startBridge({
    discoveryFile: file,
    handlers: { hello: async () => ({ protocol: BRIDGE_PROTOCOL, appVersion: "test", ops: Object.keys(PARSERS) }), ...handlers },
    logger: quiet,
  });
  const auth = { authorization: `Bearer ${bridge.discovery.token}` };
  return { bridge, file, dir, auth };
}

describe("discovery file", () => {
  test("is written at mode 0600 in a 0700 folder, with port, token, pid and protocol", async () => {
    const { bridge, file } = await running();
    const disc = JSON.parse(fs.readFileSync(file, "utf8"));
    expect(disc).toEqual({ port: bridge.port, token: bridge.discovery.token, pid: process.pid, protocol: 1 });
    expect(disc.token).toMatch(/^[0-9a-f]{64}$/);
    if (process.platform !== "win32") {
      expect(fs.statSync(file).mode & 0o777).toBe(0o600);
      expect(fs.statSync(path.dirname(file)).mode & 0o777).toBe(0o700);
    }
    await bridge.close();
  });

  test("is removed on quit", async () => {
    const { bridge, file } = await running();
    await bridge.close();
    expect(fs.existsSync(file)).toBe(false);
  });

  test("is left alone on quit when a newer app run has rewritten it", async () => {
    const { bridge, file } = await running();
    fs.writeFileSync(file, JSON.stringify({ port: 1, token: "someone-else", pid: 1, protocol: 1 }));
    await bridge.close();
    expect(JSON.parse(fs.readFileSync(file, "utf8")).token).toBe("someone-else");
  });

  test("a new run gets a new token", async () => {
    const first = await running();
    const second = await running();
    expect(first.bridge.discovery.token).not.toBe(second.bridge.discovery.token);
    await first.bridge.close();
    await second.bridge.close();
  });

  test("dev QA mode keeps it in the throwaway user data dir, never in ~/.gaia", () => {
    const gaiaDir = path.join(os.homedir(), ".gaia");
    expect(discoveryPath({ devQa: true, userDataDir: "/tmp/qa-ud", gaiaDir })).toBe(path.join("/tmp/qa-ud", "run", "browser-pane.json"));
    expect(discoveryPath({ devQa: false, userDataDir: "/tmp/qa-ud", gaiaDir })).toBe(path.join(gaiaDir, "run", "browser-pane.json"));
  });

  test("main hands the path to the backend it starts", () => {
    const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");
    expect(main).toMatch(/\[PANE_DISCOVERY_ENV\]: paneDiscoveryFile\(\)/);
    expect(main).toMatch(/paneBridge\.close\(\)/);
    expect(main).toMatch(/const appPorts = \(\) => \[backendPort, \.\.\.\(paneBridge \? \[paneBridge\.port\] : \[\]\), \.\.\.servicePorts\(\)\]/);
    expect(main).toMatch(/blockedPorts: appPorts,/);
  });
});

describe("who may call", () => {
  let ctx;
  beforeAll(async () => { ctx = await running(); });
  afterAll(async () => { await ctx.bridge.close(); });

  test("the token holder gets an answer, with no CORS headers", async () => {
    const res = await post(ctx.bridge.port, "hello", {}, ctx.auth);
    expect(res.status).toBe(200);
    expect(res.body.protocol).toBe(1);
    expect(Object.keys(res.headers).filter((h) => h.startsWith("access-control"))).toEqual([]);
  });

  test("a web page is refused even with the token", async () => {
    const res = await post(ctx.bridge.port, "hello", {}, { ...ctx.auth, origin: "http://127.0.0.1:5173" });
    expect(res.status).toBe(403);
    expect(res.body.error.code).toBe("forbidden");
    expect(Object.keys(res.headers).filter((h) => h.startsWith("access-control"))).toEqual([]);
  });

  test("a rebound DNS name is refused by its Host", async () => {
    const res = await post(ctx.bridge.port, "hello", {}, { ...ctx.auth, host: `evil.example:${ctx.bridge.port}` });
    expect(res.status).toBe(403);
  });

  test.each([
    ["no token", {}],
    ["a wrong token", { authorization: `Bearer ${"0".repeat(64)}` }],
    ["a token of another length", { authorization: "Bearer abc" }],
  ])("%s is refused", async (_name, headers) => {
    const res = await post(ctx.bridge.port, "hello", {}, headers);
    expect(res.status).toBe(401);
    expect(res.body.error.code).toBe("unauthorized");
  });

  test("an unknown op, a GET, or a body that is not JSON is refused before any handler", async () => {
    expect((await post(ctx.bridge.port, "click", {}, ctx.auth)).body.error.code).toBe("unknown_op");
    expect((await post(ctx.bridge.port, "hello", "{nope", ctx.auth)).body.error.code).toBe("bad_request");
    const get = await new Promise((resolve) => {
      http.get({ host: "127.0.0.1", port: ctx.bridge.port, path: "/v1/hello", headers: ctx.auth }, (res) => resolve(res.statusCode));
    });
    expect(get).toBe(404);
  });
});

describe("parsing", () => {
  const requests = fixture("requests.json");

  test.each(Object.keys(requests).filter((k) => !k.startsWith("_")))("the shared %s body parses", (key) => {
    const op = key.split("_")[0];
    expect(() => parseRequest(op, requests[key])).not.toThrow();
  });

  test.each([
    ["evaluate", { ...requests.evaluate, frames: "some" }, /"frames" must be one of main, all/],
    ["evaluate", { ...requests.evaluate, allowedOrigins: ["https://example.com/path"] }, /not an http\(s\) origin/],
    ["evaluate", { ...requests.evaluate, allowedOrigins: ["*"] }, /not an http\(s\) origin/],
    ["evaluate", { tabId: null, script: "x", frames: "main", allowedOrigins: [] }, /"args" is required/],
    ["logs", { ...requests.logs, limit: 0 }, /"limit"/],
    ["screenshot", { ...requests.screenshot, maxWidth: 99999 }, /"maxWidth"/],
    ["tabs", { sessionId: "c", op: "close", tabId: "tab-1" }, /"op" must be one of list, open, select/],
    ["navigate", { sessionId: "c", tabId: 3, to: "back", allowedOrigins: [] }, /"tabId"/],
    ["origin", { tabId: null }, /"sessionId"/],
  ])("a bad %s body names what is wrong", (op, body, message) => {
    expect(() => parseRequest(op, body)).toThrow(message);
  });

  test("every op has a parser, a handler, and a place in the contract", () => {
    const handlers = createHandlers({ getHost: () => null, devServers: { list: () => [] }, appVersion: "x" });
    const contractOps = [...CONTRACT.split("export interface BridgeOps {")[1].split("\n}\n")[0].matchAll(/^  (\w+): \{/gm)].map((m) => m[1]);
    expect(Object.keys(handlers).sort()).toEqual(Object.keys(PARSERS).sort());
    expect(contractOps.sort()).toEqual(Object.keys(PARSERS).sort());
    expect(fixture("hello.json").ops.sort()).toEqual(Object.keys(PARSERS).sort());
  });

  test("every error code the code can send is in the contract and the shared list", () => {
    const sent = new Set();
    for (const file of ["bridge-server.cjs", "pane-host.cjs", "input.cjs", "dev-servers.cjs"]) {
      const src = fs.readFileSync(path.join(PANE, file), "utf8");
      for (const m of src.matchAll(/new BridgeError\("([a-z_]+)"/g)) sent.add(m[1]);
      for (const m of src.matchAll(/^const bad = .*BridgeError\("([a-z_]+)"/gm)) sent.add(m[1]);
    }
    const contract = [...CONTRACT.split("export type BridgeErrorCode =")[1].split(";")[0].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(contract.sort()).toEqual(fixture("error_codes.json").codes);
    for (const code of sent) expect(contract).toContain(code);
  });

  test("pages on this computer match the list Python uses", () => {
    for (const [origin, expected] of fixture("loopback.json").cases) {
      expect([origin, isLocalPageHost(new URL(origin).hostname)]).toEqual([origin, expected]);
    }
  });

  test("a dev server's origins match the ones Python trusts", () => {
    const { port, origins } = fixture("dev_server_origins.json");
    expect(devServerOrigins(port)).toEqual(origins);
  });

  test("attach says not_in_desktop while no window is open", async () => {
    const handlers = createHandlers({ getHost: () => null, devServers: { list: () => [] }, appVersion: "x" });
    await expect(handlers.attach({ sessionId: "chat-1" })).resolves.toEqual({ status: "not_in_desktop" });
    await expect(handlers.evaluate({ sessionId: "chat-1", tabId: null })).rejects.toMatchObject({ code: "no_page" });
  });
});

// ── PaneHost's agent ops ────────────────────────────────────────────────

function fakeSession() {
  const ses = new EventEmitter();
  ses.cookies = { get: jest.fn(() => Promise.resolve([{ name: "sid" }])) };
  return ses;
}

function fakeCdp(wcArg) {
  return { onNavigationStarted: jest.fn(), logs: jest.fn(), screenshot: jest.fn(), ready: Promise.resolve(), landingOrigin: jest.fn((_x, _y) => Promise.resolve(wcArg._landsIn || new URL(wcArg.getURL()).origin)), shownUnapprovedSites: jest.fn(() => Promise.resolve(wcArg._shownSites || [])), };
}

function makeHost() {
  const window = new BrowserWindow();
  const published = [];
  const deps = {
    window,
    session: fakeSession(),
    publish: (s) => published.push(s),
    blockedPorts: () => [4321],
    recentStore: { load: () => new Map(), save: () => {} },
    goneChats: new Set(),
    onToggleRequest: jest.fn(),
    onShowRequest: jest.fn(),
    openExternal: jest.fn(),
    createCdp: jest.fn(fakeCdp),
  };
  const host = new PaneHost(deps);
  host.setView("chat-1", false);
  host.openTab(null);
  return { host, deps, published };
}

/** Make the next loadURL in `wc` wait until the test resolves or rejects it. */
function holdLoad(wc) {
  const held = {};
  wc.loadURL.mockImplementationOnce((url) => {
    wc._url = url;
    return new Promise((resolve, reject) => Object.assign(held, { resolve, reject }));
  });
  return held;
}

describe("agent ops", () => {
  test("attach answers only for the chat the window shows", () => {
    const { host } = makeHost();
    expect(host.attachSession("chat-2")).toEqual({ status: "not_in_desktop" });
    expect(host.attachSession("chat-1")).toMatchObject({ status: "attached", tabId: host.activeTabId });
    host.setView("chat-2", false);
    expect(host.attachSession("chat-2")).toEqual({ status: "attached", tabId: null, paneVisible: false });
    expect(host.attachSession("chat-1")).toEqual({ status: "not_in_desktop" });
  });

  test("a chat the user switched away from can no longer read the pane", async () => {
    const { host } = makeHost();
    const handlers = createHandlers({ getHost: () => host, devServers: { list: () => [] }, appVersion: "x" });
    await expect(handlers.origin({ sessionId: "chat-1", tabId: null })).resolves.toMatchObject({ tabId: host.activeTabId });
    host.setView("chat-2", false);
    for (const op of ["origin", "evaluate", "screenshot", "logs", "navigate"]) {
      await expect(handlers[op]({ sessionId: "chat-1", tabId: null })).rejects.toMatchObject({ code: "not_in_desktop" });
    }
    await expect(handlers.tabs({ sessionId: "chat-1", op: "list" })).rejects.toMatchObject({ code: "not_in_desktop" });
  });

  test("the agent opens its own tab, shows the pane, and the strip says it is reading", async () => {
    const { host, deps, published } = makeHost();
    const tabs = await host.agentOpen("chat-1", "http://127.0.0.1:5000/", ["http://127.0.0.1:5000"]);
    const opened = tabs.tabs.find((t) => t.active);
    expect(opened).toMatchObject({ url: "http://127.0.0.1:5000/", openedBy: "chat-1" });
    expect(deps.onShowRequest).toHaveBeenCalled();
    expect(published.some((s) => s.tabs.find((t) => t.id === opened.id)?.agentBusy)).toBe(true);
    await new Promise((r) => setTimeout(r, 1600));
    expect(published[published.length - 1].tabs.find((t) => t.id === opened.id).agentBusy).toBe(false);
  });

  test("a site the gate did not approve is never opened", async () => {
    const { host } = makeHost();
    await expect(host.agentOpen("chat-1", "https://example.com/", [])).rejects.toMatchObject({
      code: "blocked_navigation",
      data: { origin: "https://example.com" },
    });
    await expect(host.agentOpen("chat-1", "https://example.com/", ["https://example.com"])).resolves.toBeTruthy();
  });

  test("a redirect off the approved sites during an op is stopped and reported", async () => {
    const { host } = makeHost();
    const id = host.activeTabId;
    host.navigate(id, "http://127.0.0.1:5000/");
    const wc = host.tabs.get(id).view.webContents;
    const held = holdLoad(wc);
    const op = host.agentNavigate("chat-1", id, "http://127.0.0.1:5000/redirect", ["http://127.0.0.1:5000"]);
    const redirect = { url: "https://evil.example/landing", preventDefault: jest.fn() };
    wc.emit("will-redirect", redirect);
    held.reject(new Error("ERR_ABORTED (-3) loading 'http://127.0.0.1:5000/redirect'"));
    await expect(op).rejects.toMatchObject({
      code: "blocked_navigation",
      data: { url: "https://evil.example/landing", origin: "https://evil.example" },
    });
    expect(redirect.preventDefault).toHaveBeenCalled();
  });

  test("outside an op the user browses anywhere", () => {
    const { host } = makeHost();
    host.navigate(host.activeTabId, "http://127.0.0.1:5000/");
    const wc = host.tabs.get(host.activeTabId).view.webContents;
    const nav = { url: "https://example.org/", preventDefault: jest.fn() };
    wc.emit("will-navigate", nav);
    expect(nav.preventDefault).not.toHaveBeenCalled();
  });

  test("a page off the approved sites is withheld, naming its origin", async () => {
    const { host } = makeHost();
    host.navigate(host.activeTabId, "https://example.org/");
    await expect(host.agentEvaluate("chat-1", null, "() => 1", null, "main", [])).rejects.toMatchObject({
      code: "off_scope",
      data: { origin: "https://example.org" },
    });
    const wc = host.tabs.get(host.activeTabId).view.webContents;
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });

  test("evaluate runs Python's script in every frame, main first", async () => {
    const { host } = makeHost();
    host.navigate(host.activeTabId, "http://localhost:5173/");
    const wc = host.tabs.get(host.activeTabId).view.webContents;
    const child = { url: "http://localhost:5173/frame", processId: 4, routingId: 7, executeJavaScript: jest.fn(() => Promise.reject(new Error("cross-origin"))) };
    wc.mainFrame.framesInSubtree = [child, wc.mainFrame];
    wc.mainFrame.executeJavaScript.mockResolvedValue({ title: "App" });
    const res = await host.agentEvaluate("chat-1", null, "(args) => ({ title: document.title })", { gen: 1 }, "all", ["http://localhost:5173"]);
    expect(wc.mainFrame.executeJavaScript).toHaveBeenCalledWith('((args) => ({ title: document.title }))({"gen":1})', false);
    expect(res.frames).toEqual([
      { frameId: null, url: "", ok: true, value: { title: "App" } },
      { frameId: "4:7", url: "http://localhost:5173/frame", ok: false, error: "cross-origin" },
    ]);
  });

  /** A tab on the chat's dev server at localhost:5173, holding `urls` as frames: [url] or [url, origin]. */
  function framedPage(specs) {
    const { host } = makeHost();
    host.navigate(host.activeTabId, "http://localhost:5173/");
    const wc = host.tabs.get(host.activeTabId).view.webContents;
    wc.mainFrame.executeJavaScript.mockResolvedValue({ title: "App" });
    const frames = specs.map(([url, origin], i) => ({ url, origin, processId: 9, routingId: i + 1, parent: wc.mainFrame, executeJavaScript: jest.fn(() => Promise.resolve({ read: url })) }));
    wc.mainFrame.framesInSubtree = [wc.mainFrame, ...frames];
    return { host, wc, frames };
  }

  test("a frame from a site that is not approved is not read; approving its site reads it", async () => {
    const { host, frames } = framedPage([
      ["https://widget.example/pay"],
      ["http://127.0.0.1:13305/"],
      ["http://127.0.0.1:13305/admin", "null"],
      ["http://[::ffff:7f00:1]:13305/"],
      ["http://0.0.0.0:8888/"],
      ["data:text/html,<p>hi</p>", "null"],
    ]);
    const [widget, lemonade, sandboxed, mapped, anyAddr, data] = frames;
    const res = await host.agentEvaluate("chat-1", null, "() => 1", null, "all", ["http://localhost:5173"]);
    for (const f of frames) expect(f.executeJavaScript).not.toHaveBeenCalled();
    expect(res.frames[0]).toMatchObject({ frameId: null, ok: true });
    expect(res.frames.slice(1).map((f) => [f.ok, f.unapproved])).toEqual([
      [false, "https://widget.example"],
      [false, "http://127.0.0.1:13305"],
      [false, null],
      [false, "http://[::ffff:7f00:1]:13305"],
      [false, "http://0.0.0.0:8888"],
      [false, null],
    ]);
    for (const f of res.frames.slice(1)) {
      expect(f).not.toHaveProperty("value");
      expect(f.error).toMatch(/not approved/);
    }

    // Approval is per frame origin: approving one site reads its frames and nothing else.
    const again = await host.agentEvaluate("chat-1", null, "() => 1", null, "all", ["http://localhost:5173", "https://widget.example"]);
    expect(widget.executeJavaScript).toHaveBeenCalled();
    expect(again.frames[1]).toMatchObject({ ok: true, value: { read: widget.url } });
    for (const f of [lemonade, sandboxed, mapped, anyAddr, data]) expect(f.executeJavaScript).not.toHaveBeenCalled();
    await host.agentEvaluate("chat-1", null, "() => 1", null, "all", ["http://localhost:5173", "http://127.0.0.1:13305"]);
    expect(lemonade.executeJavaScript).toHaveBeenCalled();
    // A sandboxed frame has an opaque origin: approving the site it came from does not cover it.
    for (const f of [sandboxed, mapped, anyAddr, data]) expect(f.executeJavaScript).not.toHaveBeenCalled();
  });

  test("the page's own frames and the chat's dev server frames are read with no further approval", async () => {
    const { host, frames } = framedPage([
      ["http://localhost:5173/inner"],
      ["about:srcdoc", "http://localhost:5173"],
      ["about:blank", "http://localhost:5173"],
      ["about:blank", "null"],
      ["http://127.0.0.1:5000/embed"],
    ]);
    const res = await host.agentEvaluate("chat-1", null, "() => 1", null, "all", ["http://localhost:5173", ...devServerOrigins(5000)]);
    const [inner, srcdoc, blank, opaque, devServer] = frames;
    for (const f of [inner, srcdoc, blank, devServer]) expect(f.executeJavaScript).toHaveBeenCalled();
    // A blank frame whose origin is opaque (sandboxed) has no site to approve, so it stays unread.
    expect(opaque.executeJavaScript).not.toHaveBeenCalled();
    expect(res.frames.filter((f) => !f.ok).map((f) => f.frameId)).toEqual([`9:${frames.indexOf(opaque) + 1}`]);
  });

  test("origin lists the page's frame origins, so a read can ask for them", async () => {
    const { host } = framedPage([
      ["https://widget.example/pay"],
      ["http://localhost:5173/inner"],
      ["https://widget.example/other"],
      ["about:srcdoc", "http://localhost:5173"],
      ["http://127.0.0.1:13305/", "null"],
      ["data:text/html,x", "null"],
    ]);
    await expect(host.agentOrigin("chat-1", null)).resolves.toMatchObject({
      origin: "http://localhost:5173",
      frameOrigins: ["https://widget.example"],
    });
  });

  /** A tab on localhost:5173 whose history holds `before` and `after` around it; each step lands on `landsOn` (default: the entry). */
  function withHistory({ before = null, after = null, landsOn = null } = {}) {
    const { host } = makeHost();
    host.navigate(host.activeTabId, "http://localhost:5173/");
    const wc = host.tabs.get(host.activeTabId).view.webContents;
    const entries = [before, "http://localhost:5173/", after].filter(Boolean).map((url) => ({ url, title: url }));
    const here = before ? 1 : 0;
    const nav = wc.navigationHistory;
    nav.getAllEntries.mockImplementation(() => entries);
    nav.getActiveIndex.mockImplementation(() => here);
    nav.canGoBack.mockImplementation(() => Boolean(before));
    nav.canGoForward.mockImplementation(() => Boolean(after));
    const land = (url) => () => {
      wc._url = landsOn || url;
      wc.getTitle = jest.fn(() => `Title of ${wc._url}`);
      setImmediate(() => wc.emit("did-stop-loading"));
    };
    nav.goBack.mockImplementation(land(before));
    nav.goForward.mockImplementation(land(after));
    wc.reload.mockImplementation(land("http://localhost:5173/"));
    return { host, wc, nav };
  }

  test.each([
    ["back", { before: "http://127.0.0.1:13305/" }],
    ["forward", { after: "https://mail.example/inbox" }],
  ])("going %s to a page on a site that is not approved is refused before the tab moves", async (to, history) => {
    const { host, nav } = withHistory(history);
    const err = await host.agentNavigate("chat-1", null, to, ["http://localhost:5173"]).catch((e) => e);
    expect(err).toMatchObject({ code: "off_scope" });
    expect(JSON.stringify(err.data)).not.toMatch(/13305\/|inbox/);
    expect(nav.goBack).not.toHaveBeenCalled();
    expect(nav.goForward).not.toHaveBeenCalled();
  });

  test("going back to an approved page moves and says where it landed", async () => {
    const { host, nav } = withHistory({ before: "https://docs.example/a" });
    const res = await host.agentNavigate("chat-1", null, "back", ["http://localhost:5173", "https://docs.example"]);
    expect(nav.goBack).toHaveBeenCalled();
    expect(res).toMatchObject({ url: "https://docs.example/a", title: "Title of https://docs.example/a" });
  });

  test("a step that lands somewhere not approved never returns that page's address or title", async () => {
    const { host } = withHistory({ landsOn: "https://other.example/secret-path" });
    const err = await host.agentNavigate("chat-1", null, "reload", ["http://localhost:5173"]).catch((e) => e);
    expect(err).toMatchObject({ code: "off_scope", data: { origin: "https://other.example" } });
    expect(err.message).not.toMatch(/secret-path|Title of/);
  });

  test("the agent can't open a name that resolves to one of GAIA's own ports", async () => {
    const { host } = makeHost();
    host.deps.lookup = jest.fn(async () => [{ address: "127.0.0.1" }]);
    const before = host.tabs.size;
    await expect(host.agentOpen("chat-1", "http://rebind.example:4321/", ["http://rebind.example:4321"])).rejects.toMatchObject({ code: "blocked_url" });
    expect(host.deps.lookup).toHaveBeenCalledWith("rebind.example");
    expect(host.tabs.size).toBe(before);
    host.deps.lookup = jest.fn(async () => [{ address: "93.184.216.34" }]);
    await expect(host.agentOpen("chat-1", "http://site.example:4321/", ["http://site.example:4321"])).resolves.toBeTruthy();
  });

  test("a new tab page has nothing to read", async () => {
    const { host } = makeHost();
    await expect(host.agentEvaluate("chat-1", null, "() => 1", null, "main", [])).rejects.toMatchObject({ code: "no_page" });
  });

  test("origin reports where the page is and whether the pane holds cookies there", async () => {
    const { host, deps } = makeHost();
    host.navigate(host.activeTabId, "https://example.com/a");
    await expect(host.agentOrigin("chat-1", null)).resolves.toMatchObject({ origin: "https://example.com", hasCookies: true });
    expect(deps.session.cookies.get).toHaveBeenCalledWith({ url: "https://example.com" });
  });

  test("scope: only by exact origin, localhost included", () => {
    expect(inScope("http://127.0.0.1:9/x", [])).toBe(false);
    expect(inScope("http://127.0.0.1:9/x", ["http://127.0.0.1:9"])).toBe(true);
    expect(inScope("http://localhost:9/x", ["http://127.0.0.1:9"])).toBe(false);
    expect(inScope("https://example.com/x", ["https://example.com"])).toBe(true);
    expect(inScope("https://example.com:8443/x", ["https://example.com"])).toBe(false);
    expect(inScope("file:///etc/passwd", ["https://example.com"])).toBe(false);
    expect(inScope("about:blank", [])).toBe(true);
  });

  test("BridgeError carries a code and data", () => {
    const err = new BridgeError("timeout", "slow", { x: 1 });
    expect(err).toMatchObject({ code: "timeout", message: "slow", data: { x: 1 } });
  });
});

describe("console and network rings", () => {
  test("a reader's cursor returns only what is new, and says what was dropped", () => {
    const ring = new Ring(3);
    for (const text of ["a", "b", "c", "d", "e"]) ring.push({ text });
    expect(ring.query({ afterSeq: 0, limit: 10, keep: () => true })).toEqual({
      entries: [{ seq: 3, text: "c" }, { seq: 4, text: "d" }, { seq: 5, text: "e" }],
      lastSeq: 5,
      dropped: 2,
    });
    expect(ring.query({ afterSeq: 4, limit: 10, keep: () => true }).entries).toEqual([{ seq: 5, text: "e" }]);
  });

  test("a tab records console errors, exceptions and failed requests from the moment it opens", async () => {
    const wc = new (require("electron").WebContentsView)().webContents;
    const cdp = new TabCdp(wc, quiet);
    await cdp.ready;
    expect(wc.debugger.attach).toHaveBeenCalledWith("1.3");
    expect(wc.debugger.sendCommand).toHaveBeenCalledWith("Network.enable");
    const emit = (method, params) => wc.debugger.emit("message", {}, method, params);
    emit("Runtime.consoleAPICalled", { type: "log", args: [{ type: "string", value: "hi" }] });
    emit("Runtime.exceptionThrown", { exceptionDetails: { text: "Uncaught", exception: { description: "TypeError: x is null" }, url: "http://localhost/a.js", lineNumber: 11 } });
    emit("Network.requestWillBeSent", { requestId: "1", request: { url: "http://localhost/api", method: "POST" }, type: "Fetch" });
    emit("Network.responseReceived", { requestId: "1", response: { status: 500 } });
    emit("Network.loadingFinished", { requestId: "1" });
    emit("Network.requestWillBeSent", { requestId: "2", request: { url: "http://localhost/ok", method: "GET" }, type: "Fetch" });
    emit("Network.responseReceived", { requestId: "2", response: { status: 200 } });
    emit("Network.loadingFinished", { requestId: "2" });

    const errors = cdp.logs({ kind: "console", afterSeq: 0, errorsOnly: true, pattern: null, limit: 50 });
    expect(errors.entries).toEqual([expect.objectContaining({ level: "error", text: "TypeError: x is null", source: "http://localhost/a.js:12" })]);
    const failed = cdp.logs({ kind: "network", afterSeq: 0, errorsOnly: true, pattern: null, limit: 50 });
    expect(failed.entries).toEqual([expect.objectContaining({ method: "POST", url: "http://localhost/api", status: 500 })]);
    expect(cdp.logs({ kind: "network", afterSeq: 0, errorsOnly: false, pattern: "OK", limit: 50 }).entries).toHaveLength(1);

    cdp.onNavigationStarted();
    expect(cdp.logs({ kind: "console", afterSeq: 0, errorsOnly: false, pattern: null, limit: 50 }).entries).toEqual([]);
  });
});

describe("the browser's own view of the page's frames", () => {
  const PAGE = "http://127.0.0.1:5000";
  const WIDGET = "https://widget.example";

  /**
   * A fake DevTools target: `frames` is the frame tree per session, `hits`
   * answers DOM.getNodeForLocation per session, `boxes` DOM.getBoxModel by node.
   */
  async function browserSees({ frames, hits = {}, boxes = {}, owners = {} }) {
    const wc = new (require("electron").WebContentsView)().webContents;
    const cdp = new TabCdp(wc, quiet);
    await cdp.ready;
    for (const sid of Object.keys(frames)) {
      if (sid !== "main") wc.debugger.emit("message", {}, "Target.attachedToTarget", { sessionId: sid, targetInfo: { type: "iframe" } });
    }
    const sent = [];
    wc.debugger.sendCommand.mockImplementation((method, params = {}, sid) => {
      const session = sid || "main";
      sent.push([method, session]);
      if (method === "Page.getFrameTree") return Promise.resolve({ frameTree: frames[session] });
      if (method === "DOM.getDocument") return Promise.resolve({ root: { nodeId: 1 } });
      if (method === "DOM.getNodeForLocation") return Promise.resolve(hits[session](params.x, params.y));
      if (method === "DOM.describeNode") return Promise.resolve({ node: { nodeName: params.backendNodeId >= 100 ? "IFRAME" : "DIV", frameId: params.frameIdOf } });
      if (method === "DOM.getBoxModel") {
        const box = boxes[params.backendNodeId];
        return box ? Promise.resolve({ model: box }) : Promise.reject(new Error("Could not compute box model."));
      }
      if (method === "DOM.getFrameOwner") return Promise.resolve({ backendNodeId: owners[params.frameId] });
      return Promise.resolve({});
    });
    return { cdp, sent };
  }

  const tree = (id, url, children = [], parentId) => ({ frame: { id, url, parentId }, childFrames: children });

  test("a point is followed into an out-of-process frame, by the browser's layout", async () => {
    const describeNode = (backendNodeId) => ({ 101: "W" })[backendNodeId];
    const { cdp } = await browserSees({
      frames: { main: tree("M", `${PAGE}/`), s1: tree("W", `${WIDGET}/pay`, [], "M") },
      hits: { main: () => ({ backendNodeId: 101, frameId: "M" }), s1: (x, y) => ({ backendNodeId: 5, frameId: "W", at: [x, y] }) },
      boxes: { 101: { content: [100, 200, 400, 200, 400, 300, 100, 300], width: 300, height: 100 } },
    });
    const send = cdp.wc.debugger.sendCommand.getMockImplementation();
    cdp.wc.debugger.sendCommand.mockImplementation((method, params = {}, sid) =>
      method === "DOM.describeNode" ? Promise.resolve({ node: { nodeName: "IFRAME", frameId: describeNode(params.backendNodeId) || (sid ? "W" : "M") } }) : send(method, params, sid)
    );
    await expect(cdp.landingOrigin(150, 250)).resolves.toBe(WIDGET);
    const located = cdp.wc.debugger.sendCommand.mock.calls.filter(([m]) => m === "DOM.getNodeForLocation");
    expect(located.at(-1)).toEqual(["DOM.getNodeForLocation", expect.objectContaining({ x: 50, y: 50 }), "s1"]);
  });

  test("a point in the page itself, or in a blank frame it wrote, is the page's site", async () => {
    const { cdp } = await browserSees({
      frames: { main: tree("M", `${PAGE}/`, [tree("B", "about:srcdoc", [], "M")]) },
      hits: { main: (x) => ({ backendNodeId: 5, frameId: x < 100 ? "M" : "B" }) },
    });
    await expect(cdp.landingOrigin(10, 10)).resolves.toBe(PAGE);
    await expect(cdp.landingOrigin(150, 10)).resolves.toBe(PAGE);
  });

  test("a point in a data: frame has no site", async () => {
    const { cdp } = await browserSees({
      frames: { main: tree("M", `${PAGE}/`, [tree("D", "data:text/html,x", [], "M")]) },
      hits: { main: () => ({ backendNodeId: 5, frameId: "D" }) },
    });
    await expect(cdp.landingOrigin(10, 10)).resolves.toBeNull();
  });

  test("only frames that show, from sites not approved, and the outermost of them, are named", async () => {
    const { cdp } = await browserSees({
      frames: {
        main: tree("M", `${PAGE}/`, [tree("H", "https://hidden.example/", [], "M"), tree("Z", "https://zero.example/", [], "M")]),
        s1: tree("W", `${WIDGET}/pay`, [tree("N", "https://nested.example/", [], "W")], "M"),
        s2: tree("A", "https://ads.example/", [], "M"),
      },
      owners: { H: 1, Z: 2, W: 3, A: 4, N: 5 },
      boxes: { 2: { width: 0, height: 0 }, 3: { width: 300, height: 100 }, 4: { width: 10, height: 10 }, 5: { width: 50, height: 50 } },
    });
    await expect(cdp.shownUnapprovedSites([PAGE])).resolves.toEqual([WIDGET, "https://ads.example"]);
    await expect(cdp.shownUnapprovedSites([PAGE, WIDGET])).resolves.toEqual(["https://nested.example", "https://ads.example"]);
    await expect(cdp.shownUnapprovedSites([PAGE, WIDGET, "https://nested.example", "https://ads.example"])).resolves.toEqual([]);
  });

  test("a frame session that can't be read fails the check instead of passing it", async () => {
    const { cdp } = await browserSees({ frames: { main: tree("M", `${PAGE}/`), s1: tree("W", `${WIDGET}/`, [], "M") } });
    const send = cdp.wc.debugger.sendCommand.getMockImplementation();
    cdp.wc.debugger.sendCommand.mockImplementation((method, params, sid) =>
      method === "Page.getFrameTree" && sid === "s1" ? Promise.reject(new Error("Target crashed")) : send(method, params, sid)
    );
    await expect(cdp.shownUnapprovedSites([PAGE])).rejects.toThrow(/Target crashed/);
  });

  test("events from frame sessions never land in the tab's own logs", async () => {
    const wc = new (require("electron").WebContentsView)().webContents;
    const cdp = new TabCdp(wc, quiet);
    await cdp.ready;
    expect(wc.debugger.sendCommand).toHaveBeenCalledWith("Target.setAutoAttach", expect.objectContaining({ autoAttach: true, flatten: true }));
    wc.debugger.emit("message", {}, "Runtime.consoleAPICalled", { type: "error", args: [{ type: "string", value: "from a frame" }] }, "s9");
    expect(cdp.logs({ kind: "console", afterSeq: 0, errorsOnly: false, pattern: null, limit: 50 }).entries).toEqual([]);
  });
});
