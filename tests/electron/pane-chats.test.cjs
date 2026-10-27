// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Browser pane tabs belong to the chat that opened them: each chat shows
 * its own tabs, the new-chat screen's tabs go to the chat its first message
 * makes, an agent never reaches another chat's tabs, other chats' pages stay
 * alive up to a cap, and deleting a chat closes its tabs for good.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { EventEmitter } = require("events");
const { BrowserWindow, WebContentsView, ipcMain } = require("electron");

const PANE = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "services", "browser-pane");
const { PaneHost, LIVE_VIEW_CAP } = require(path.join(PANE, "pane-host.cjs"));
const { createHandlers, parseRequest } = require(path.join(PANE, "bridge-server.cjs"));
const { CHANNELS, createBrowserPaneService } = require(path.join(PANE, "ipc.cjs"));

const LOCAL = "http://127.0.0.1:5000";
const POINT = { x: 10, y: 10, tag: "button", type: "submit", editable: false };

function makeHost() {
  const session = new EventEmitter();
  session.cookies = { get: jest.fn(() => Promise.resolve([])) };
  const published = [];
  const host = new PaneHost({
    window: new BrowserWindow(),
    session,
    publish: (s) => published.push(s),
    blockedPorts: () => [],
    recentStore: { load: () => [], save: () => {} },
    goneChats: new Set(),
    onToggleRequest: jest.fn(),
    onShowRequest: jest.fn(),
    openExternal: jest.fn(),
    createCdp: () => ({
      send: jest.fn(() => Promise.resolve({})),
      onNavigationStarted: jest.fn(),
      logs: jest.fn(() => ({ entries: [], lastSeq: 0, dropped: 0 })),
      screenshot: jest.fn(() => Promise.resolve(Buffer.from(""))),
    }),
  });
  return { host, published, handlers: createHandlers({ getHost: () => host, appVersion: "x" }) };
}

const wcOf = (host, id) => host.tabs.get(id).view.webContents;
const urls = (state) => state.tabs.map((t) => t.url);

beforeEach(() => {
  WebContentsView.instances.length = 0;
});

describe("each chat shows its own tabs", () => {
  test("a fresh pane has no tabs; opening it makes a new tab page for the chat on screen", () => {
    const { host } = makeHost();
    expect(host.state()).toMatchObject({ chat: null, tabs: [], activeTabId: null });
    host.setView(null, true);
    expect(host.state().tabs).toEqual([expect.objectContaining({ url: null, title: "New tab" })]);
    expect(host.state().activeTabId).toBe(host.state().tabs[0].id);
    expect(WebContentsView.instances).toHaveLength(0);
  });

  test("switching chats swaps the tabs and brings back each chat's own active tab, without reloading", () => {
    const { host } = makeHost();
    host.setView("chat-a", true);
    const a1 = host.state().activeTabId;
    host.navigate(a1, `${LOCAL}/a1`);
    const a2 = host.openTab(`${LOCAL}/a2`);
    host.selectTab(a1);

    host.setView("chat-b", true);
    const b1 = host.state().activeTabId;
    host.navigate(b1, `${LOCAL}/b1`);
    expect(host.state()).toMatchObject({ chat: "chat-b", activeTabId: b1 });
    expect(urls(host.state())).toEqual([`${LOCAL}/b1`]);
    expect(wcOf(host, a1).loadURL).toHaveBeenCalledTimes(1);

    host.setView("chat-a", true);
    expect(host.state()).toMatchObject({ chat: "chat-a", activeTabId: a1 });
    expect(urls(host.state())).toEqual([`${LOCAL}/a1`, `${LOCAL}/a2`]);
    expect(wcOf(host, a1).loadURL).toHaveBeenCalledTimes(1);
    expect(wcOf(host, a2).close).not.toHaveBeenCalled();
  });

  test("another chat's pages stay hidden while their chat is not on screen", () => {
    const { host } = makeHost();
    host.setBounds({ x: 0, y: 0, width: 400, height: 300 });
    host.setView("chat-a", true);
    const a = host.state().activeTabId;
    host.navigate(a, `${LOCAL}/a`);
    host.setView("chat-b", true);
    host.navigate(host.state().activeTabId, `${LOCAL}/b`);
    expect(host.tabs.get(a).view.visible).toBe(false);
    expect(host.tabs.get(host.state().activeTabId).view.visible).toBe(true);
  });

  test("a chat that never opened the pane has no tabs, and showing it closed makes none", () => {
    const { host } = makeHost();
    host.setView("chat-a", true);
    host.setView("chat-new", false);
    expect(host.state()).toMatchObject({ chat: "chat-new", tabs: [], activeTabId: null, visible: false });
    expect(host.attachSession("chat-new")).toEqual({ status: "attached", tabId: null, paneVisible: false });
  });

  test("the user can't reach another chat's tab by id from the pane", () => {
    const { host } = makeHost();
    host.setView("chat-a", true);
    const a = host.state().activeTabId;
    host.setView("chat-b", true);
    expect(() => host.selectTab(a)).toThrow(/belongs to another chat/);
    expect(() => host.closeTab(a)).toThrow(/belongs to another chat/);
    expect(host.tabs.has(a)).toBe(true);
  });

  test("a popup lands in its opener's chat, even when that chat is not on screen", () => {
    const { host } = makeHost();
    host.setView("chat-a", true);
    const a = host.state().activeTabId;
    host.navigate(a, `${LOCAL}/a`);
    const opener = wcOf(host, a);

    opener.windowOpenHandler({ url: `${LOCAL}/popup-1` });
    expect(urls(host.state())).toEqual([`${LOCAL}/a`, `${LOCAL}/popup-1`]);

    host.setView("chat-b", true);
    opener.windowOpenHandler({ url: `${LOCAL}/popup-2` });
    expect(urls(host.state())).toEqual([null]);
    host.setView("chat-a", true);
    expect(urls(host.state())).toEqual([`${LOCAL}/a`, `${LOCAL}/popup-1`, `${LOCAL}/popup-2`]);
    expect(host.state().tabs.find((t) => t.id === host.state().activeTabId).url).toBe(`${LOCAL}/popup-2`);
  });
});

describe("the new-chat screen", () => {
  test("its tabs and active tab go to the chat its first message makes", () => {
    const { host } = makeHost();
    host.setView("chat-old", true);
    host.setView(null, true);
    const draft = host.state().activeTabId;
    host.navigate(draft, `${LOCAL}/draft`);
    const second = host.openTab(`${LOCAL}/draft-2`);
    host.selectTab(draft);

    host.claimDraft("chat-new");

    expect(host.state()).toMatchObject({ chat: "chat-new", activeTabId: draft });
    expect(urls(host.state())).toEqual([`${LOCAL}/draft`, `${LOCAL}/draft-2`]);
    expect(host.attachSession("chat-new")).toMatchObject({ status: "attached", tabId: draft });
    expect(host.listTabs("chat-new").tabs.map((t) => t.id)).toEqual([draft, second]);
    expect(wcOf(host, draft).loadURL).toHaveBeenCalledTimes(1);

    host.setView("chat-new", true);
    host.setView(null, false);
    expect(host.state().tabs).toEqual([]);
    host.setView("chat-old", true);
    expect(urls(host.state())).toEqual([null]);
  });

  test("claiming twice moves nothing more, and a later new-chat screen starts empty", () => {
    const { host } = makeHost();
    host.setView(null, true);
    const draft = host.state().activeTabId;
    host.claimDraft("chat-new");
    host.claimDraft("chat-new");
    expect(host.state()).toMatchObject({ chat: "chat-new", activeTabId: draft });
    expect(host.state().tabs).toHaveLength(1);
    host.setView(null, true);
    expect(host.state().tabs).toHaveLength(1);
    expect(host.state().activeTabId).not.toBe(draft);
  });

  test("the user leaving the screen before the chat exists still hands its tabs to that chat", () => {
    const { host } = makeHost();
    host.setView(null, true);
    const draft = host.state().activeTabId;
    host.setView("chat-other", false);
    host.claimDraft("chat-new");
    expect(host.state().chat).toBe("chat-other");
    host.setView("chat-new", true);
    expect(host.state()).toMatchObject({ chat: "chat-new", activeTabId: draft });
  });
});

describe("an agent only reaches its own chat's tabs", () => {
  async function twoChats() {
    const made = makeHost();
    const { host } = made;
    host.setView("chat-a", false);
    const { tabs } = await host.agentOpen("chat-a", `${LOCAL}/secret`, []);
    const theirs = tabs.find((t) => t.active).id;
    host.setView("chat-b", true);
    const wc = wcOf(host, theirs);
    wc.mainFrame.executeJavaScript.mockClear();
    return { ...made, theirs, wc };
  }

  const scope = { allowedOrigins: [] };
  const opsOn = (tabId) => [
    ["evaluate", { tabId, script: "() => document.title", args: null, frames: "main", ...scope }],
    ["screenshot", { tabId, clip: null, maxWidth: 800, ...scope }],
    ["logs", { tabId, kind: "console", afterSeq: 0, errorsOnly: false, pattern: null, limit: 10, ...scope }],
    ["act", { tabId, action: { kind: "click" }, target: { frameId: null, selector: "button" }, ...scope }],
    ["navigate", { tabId, to: `${LOCAL}/elsewhere`, ...scope }],
    ["origin", { tabId }],
    ["script", { tabId, expression: "document.title" }],
    ["viewport", { tabId, size: null, colorScheme: "dark" }],
    ["dialog", { op: "get", tabId }],
    ["dialog", { op: "answer", tabId, accept: false, text: null, ...scope }],
  ];
  const call = (handlers, op, body) => {
    const { req } = parseRequest(op, { sessionId: "chat-b", ...body });
    return handlers[op](req);
  };

  test("its tab list and attach show only its own chat's tabs", async () => {
    const { host, handlers } = await twoChats();
    const mine = host.state().activeTabId;
    expect(host.attachSession("chat-b")).toMatchObject({ status: "attached", tabId: mine });
    await expect(call(handlers, "tabs", { op: "list" })).resolves.toEqual({
      tabs: [expect.objectContaining({ id: mine })],
      activeTabId: mine,
    });
  });

  test.each(opsOn("THEIRS").map(([op, body]) => [body.op ? `${op} ${body.op}` : op, op, body]))(
    "%s on another chat's tab by id is no_such_tab, and the page is never touched",
    async (_name, op, body) => {
      const { handlers, theirs, wc } = await twoChats();
      await expect(call(handlers, op, { ...body, tabId: theirs })).rejects.toMatchObject({ code: "no_such_tab" });
      expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
      expect(wc.loadURL).toHaveBeenCalledTimes(1);
    }
  );

  test("switching to another chat's tab is no_such_tab, and the user's view stays put", async () => {
    const { host, handlers, theirs } = await twoChats();
    const mine = host.state().activeTabId;
    await expect(call(handlers, "tabs", { op: "select", tabId: theirs })).rejects.toMatchObject({ code: "no_such_tab" });
    expect(host.state().activeTabId).toBe(mine);
    expect(host.activeByChat.get("chat-a")).toBe(theirs);
  });

  test("with no tab of its own, the active tab is no tab at all, never the other chat's", async () => {
    const { host, handlers, wc } = await twoChats();
    host.setView("chat-c", false);
    const asC = (op, body) => handlers[op](parseRequest(op, { sessionId: "chat-c", ...body }).req);
    await expect(asC("tabs", { op: "list" })).resolves.toEqual({ tabs: [], activeTabId: null });
    for (const [op, body] of opsOn(null)) {
      await expect(asC(op, body)).rejects.toMatchObject({ code: "no_page" });
    }
    expect(wc.mainFrame.executeJavaScript).not.toHaveBeenCalled();
  });

  test("its own tabs still work: it opens, switches and reads in its own chat", async () => {
    const { host, handlers } = await twoChats();
    const mine = host.state().activeTabId;
    const opened = await call(handlers, "tabs", { op: "open", url: `${LOCAL}/b-own`, ...scope });
    const own = opened.tabs.find((t) => t.active).id;
    expect(opened.tabs.map((t) => t.id)).toEqual([mine, own]);
    await expect(call(handlers, "tabs", { op: "select", tabId: mine })).resolves.toMatchObject({ activeTabId: mine });
    wcOf(host, own).mainFrame.executeJavaScript.mockResolvedValue("Own page");
    await expect(call(handlers, "evaluate", { ...opsOn(own)[0][1] })).resolves.toMatchObject({ tabId: own });
  });

  test("a popup from its own click is adopted only if it opened in its own chat", async () => {
    const { host } = await twoChats();
    host.navigate(host.state().activeTabId, `${LOCAL}/b`);
    const mine = host.state().activeTabId;
    const wc = wcOf(host, mine);
    let opened = false;
    wc.mainFrame.executeJavaScript.mockImplementation((code) => {
      const probe = code.includes("scrollIntoView");
      if (probe && !opened) {
        opened = true;
        wc.windowOpenHandler({ url: `${LOCAL}/b-popup` });
      }
      return Promise.resolve(probe ? POINT : true);
    });
    const reply = await host.agentAct("chat-b", null, { kind: "click" }, { frameId: null, selector: "button" }, []);
    expect(reply.adoptedTabId).toBe(host.state().activeTabId);
    expect(host.listTabs("chat-b").tabs.map((t) => t.url)).toEqual([`${LOCAL}/b`, `${LOCAL}/b-popup`]);
    expect(host.listTabs("chat-a").tabs.map((t) => t.url)).toEqual([`${LOCAL}/secret`]);
  });
});

describe("live pages past the cap", () => {
  /** Show `chat` with `n` pages open in it. */
  function fill(host, chat, n) {
    host.setView(chat, true);
    host.navigate(host.state().activeTabId, `${LOCAL}/${chat}/0`);
    for (let i = 1; i < n; i += 1) host.openTab(`${LOCAL}/${chat}/${i}`);
  }
  const live = (host) => [...host.tabs.values()].filter((t) => t.view).length;
  const chatPages = (host, chat) => host.chatTabs(chat).filter((t) => t.view).length;

  test("the least recently shown chat's pages close first, keeping each tab's address and title", () => {
    const { host } = makeHost();
    fill(host, "chat-old", 4);
    fill(host, "chat-mid", 4);
    fill(host, "chat-new", 4);
    expect(live(host)).toBe(LIVE_VIEW_CAP);
    const oldViews = host.chatTabs("chat-old").map((t) => t.view);

    fill(host, "chat-now", 1);

    expect(chatPages(host, "chat-old")).toBe(0);
    expect(oldViews.every((v) => v.webContents.close.mock.calls.length === 1)).toBe(true);
    expect([chatPages(host, "chat-mid"), chatPages(host, "chat-new"), chatPages(host, "chat-now")]).toEqual([4, 4, 1]);
    expect(host.chatTabs("chat-old").map((t) => t.state.url)).toEqual([0, 1, 2, 3].map((i) => `${LOCAL}/chat-old/${i}`));
  });

  test("showing a discarded chat loads its pages again from their saved history", () => {
    const { host } = makeHost();
    fill(host, "chat-old", 4);
    const saved = host.chatTabs("chat-old")[2];
    saved.view.webContents.navigationHistory.getAllEntries.mockReturnValue([
      { url: `${LOCAL}/chat-old/earlier`, title: "Earlier", pageState: "c2Nyb2xs" },
      { url: `${LOCAL}/chat-old/2`, title: "Two", pageState: "Zm9ybQ==" },
    ]);
    saved.view.webContents.navigationHistory.getActiveIndex.mockReturnValue(1);
    fill(host, "chat-mid", 4);
    fill(host, "chat-new", 4);
    fill(host, "chat-now", 1);
    expect(saved.view).toBeNull();

    host.setView("chat-old", true);

    expect(chatPages(host, "chat-old")).toBe(4);
    expect(saved.view.webContents.navigationHistory.restore).toHaveBeenCalledWith({
      entries: [
        { url: `${LOCAL}/chat-old/earlier`, title: "Earlier", pageState: "c2Nyb2xs" },
        { url: `${LOCAL}/chat-old/2`, title: "Two", pageState: "Zm9ybQ==" },
      ],
      index: 1,
    });
    expect(saved.view.webContents.loadURL).not.toHaveBeenCalled();
    expect(live(host)).toBeLessThanOrEqual(LIVE_VIEW_CAP);
    expect(chatPages(host, "chat-mid")).toBe(0);
  });

  test("the chat on screen is never discarded, even past the cap on its own", () => {
    const { host } = makeHost();
    fill(host, "chat-big", LIVE_VIEW_CAP + 3);
    expect(chatPages(host, "chat-big")).toBe(LIVE_VIEW_CAP + 3);
  });

  test("a chat whose page waits on a box keeps its pages", () => {
    const { host } = makeHost();
    fill(host, "chat-old", 4);
    host.tabs.get(host.state().activeTabId).view.webContents.emit("-run-dialog", { dialogType: "alert", messageText: "hi" }, () => {});
    fill(host, "chat-mid", 4);
    fill(host, "chat-new", 4);
    fill(host, "chat-now", 1);
    expect(chatPages(host, "chat-old")).toBe(4);
    expect(chatPages(host, "chat-mid")).toBe(0);
  });
});

describe("deleting a chat", () => {
  test("closes its tabs, and its id is refused from then on", async () => {
    const { host, handlers } = makeHost();
    host.setView("chat-a", true);
    const a = host.state().activeTabId;
    host.navigate(a, `${LOCAL}/a`);
    const view = host.tabs.get(a).view;

    host.forgetChat("chat-a");

    expect(view.webContents.close).toHaveBeenCalled();
    expect(host.tabs.has(a)).toBe(false);
    expect(host.state().tabs).toEqual([]);
    expect(host.attachSession("chat-a")).toEqual({ status: "not_in_desktop" });
    await expect(handlers.tabs({ sessionId: "chat-a", op: "list" })).rejects.toMatchObject({ code: "not_in_desktop" });
    expect(() => host.setView("chat-a", true)).toThrow(/was deleted/);
    expect(() => host.claimDraft("chat-a")).toThrow(/was deleted/);
    host.setView(null, false);
    expect(host.state().chat).toBe(null);
  });

  test("goes through one IPC call that also stops the chat's dev servers, and a new window still refuses it", () => {
    const devServers = { list: jest.fn(() => []), forgetChat: jest.fn(() => Promise.resolve()) };
    const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "pane-chats-"));
    const session = Object.assign(new EventEmitter(), {
      setPermissionRequestHandler: jest.fn(),
      setPermissionCheckHandler: jest.fn(),
      setDevicePermissionHandler: jest.fn(),
      webRequest: { onBeforeRequest: jest.fn() },
    });
    const service = createBrowserPaneService({
      ipcMain,
      session,
      shell: { openExternal: jest.fn() },
      userDataDir,
      downloadsDir: () => "/dl",
      blockedPorts: () => [],
      devServers,
    });
    const window = new BrowserWindow();
    const host = service.attach(window);
    const send = (channel, ...args) => ipcMain.emit(channel, { sender: window.webContents }, ...args);
    send(CHANNELS.setView, "chat-a", true);
    const a = host.state().activeTabId;
    expect(host.state().chat).toBe("chat-a");

    send(CHANNELS.forgetChat, "chat-a");

    expect(host.tabs.has(a)).toBe(false);
    expect(devServers.forgetChat).toHaveBeenCalledWith("chat-a");
    const next = service.attach(new BrowserWindow());
    expect(() => next.setView("chat-a", false)).toThrow(/was deleted/);
    fs.rmSync(userDataDir, { recursive: true, force: true });
  });
});
