// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for asking before quitting with work in progress
 * (src/gaia/apps/webui/services/quit-guard.cjs), and the source-level
 * contract that main.cjs runs every quit through it.
 */

const fs = require("fs");
const path = require("path");
const {
  backendJson,
  describeWork,
  confirmQuit,
  installQuitGuard,
} = require("../../src/gaia/apps/webui/services/quit-guard.cjs");

const idle = { answers: [], indexing: [] };

describe("describeWork", () => {
  test("nothing running: nothing to say", () => {
    expect(describeWork(idle)).toBeNull();
  });

  test("one answer is named, and says it will be lost", () => {
    const w = describeWork({ answers: [{ session_id: "a", title: "Trip plan" }], indexing: [] });
    expect(w.message).toBe("GAIA is still writing an answer.");
    expect(w.detail).toBe("Quitting stops the answer in 'Trip plan', and that answer will be lost.");
  });

  test("many answers are listed with a count past two", () => {
    const answers = ["A", "B", "C", "D"].map((t, i) => ({ session_id: String(i), title: t }));
    const w = describeWork({ answers, indexing: [] });
    expect(w.message).toBe("GAIA is still writing 4 answers.");
    expect(w.detail).toBe("Quitting stops the answers in 'A', 'B' and 2 more, and those answers will be lost.");
  });

  test("indexing alone", () => {
    const w = describeWork({ answers: [], indexing: [{ document_id: "d", filename: "report.pdf" }] });
    expect(w.message).toBe("GAIA is still indexing documents.");
    expect(w.detail).toBe("Indexing 'report.pdf' stops too; add it again later.");
  });

  test("both, and untitled work still reads", () => {
    const w = describeWork({
      answers: [{ session_id: "a", title: null }],
      indexing: [{ document_id: "d", filename: null }, { document_id: "e", filename: "b.txt" }],
    });
    expect(w.message).toBe("GAIA is still working.");
    expect(w.detail).toContain("'a chat'");
    expect(w.detail).toContain("Indexing 'a document' and 'b.txt' stops too; add them again later.");
  });
});

describe("confirmQuit", () => {
  test("quits without asking when idle", async () => {
    const showMessageBox = jest.fn();
    await expect(confirmQuit({ getWork: async () => idle, showMessageBox })).resolves.toBe(true);
    expect(showMessageBox).not.toHaveBeenCalled();
  });

  test("asks during a running answer; Keep running is the default", async () => {
    const showMessageBox = jest.fn(async () => ({ response: 0 }));
    const go = await confirmQuit({
      getWork: async () => ({ answers: [{ session_id: "a", title: "Trip plan" }], indexing: [] }),
      showMessageBox,
    });
    expect(go).toBe(false);
    const opts = showMessageBox.mock.calls[0][0];
    expect(opts.buttons).toEqual(["Keep running", "Quit anyway"]);
    expect(opts.defaultId).toBe(0);
    expect(opts.cancelId).toBe(0);
    expect(opts.detail).toMatch(/will be lost/);
  });

  test("Quit anyway quits", async () => {
    const go = await confirmQuit({
      getWork: async () => ({ answers: [{ session_id: "a", title: "x" }], indexing: [] }),
      showMessageBox: async () => ({ response: 1 }),
    });
    expect(go).toBe(true);
  });

  test("a failed check asks rather than guessing, and says why", async () => {
    const showMessageBox = jest.fn(async () => ({ response: 0 }));
    const go = await confirmQuit({
      getWork: async () => {
        throw new Error("the GAIA service at http://127.0.0.1:1/x did not answer (ECONNREFUSED)");
      },
      showMessageBox,
    });
    expect(go).toBe(false);
    const opts = showMessageBox.mock.calls[0][0];
    expect(opts.message).toBe("GAIA couldn't check whether it is still working.");
    expect(opts.detail).toMatch(/ECONNREFUSED/);
  });
});

describe("backendJson", () => {
  test("asks the GAIA service on loopback", async () => {
    const fetchImpl = jest.fn(async () => ({ ok: true, status: 200, json: async () => idle }));
    await expect(backendJson(4321, "/api/system/work-in-progress", { fetchImpl })).resolves.toEqual(idle);
    expect(fetchImpl.mock.calls[0][0]).toBe("http://127.0.0.1:4321/api/system/work-in-progress");
  });

  test("sends the header the service requires on a POST", async () => {
    const fetchImpl = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    await backendJson(1, "/api/system/unload-model", { method: "POST", fetchImpl });
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ method: "POST", headers: { "X-Gaia-UI": "1" } });
  });

  test("an unreachable service names the URL", async () => {
    const fetchImpl = async () => {
      throw new Error("fetch failed");
    };
    await expect(backendJson(4321, "/x", { fetchImpl })).rejects.toThrow(
      "the GAIA service at http://127.0.0.1:4321/x did not answer (fetch failed)"
    );
  });

  test("an error reply carries the service's detail and status", async () => {
    const fetchImpl = async () => ({ ok: false, status: 409, json: async () => ({ detail: "busy" }) });
    await expect(backendJson(1, "/x", { fetchImpl })).rejects.toMatchObject({ message: "busy", status: 409 });
  });

  test("an unreadable reply says so", async () => {
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new Error("Unexpected token <");
      },
    });
    await expect(backendJson(1, "/x", { fetchImpl })).rejects.toThrow(/unreadable reply from \/x \(HTTP 200\)/);
  });
});

describe("installQuitGuard", () => {
  const { EventEmitter } = require("events");

  function setup({ ask = true, confirm = async () => true } = {}) {
    const app = new EventEmitter();
    const updater = new EventEmitter();
    const deps = {
      app,
      updater,
      shouldAsk: jest.fn(() => ask),
      confirm: jest.fn(confirm),
      onQuitting: jest.fn(),
      onError: jest.fn(),
    };
    app.quit = jest.fn(() => app.emit("before-quit", quitEvent()));
    const guard = installQuitGuard(deps);
    return { app, updater, deps, guard };
  }
  const quitEvent = () => ({ preventDefault: jest.fn() });
  const flush = () => new Promise((r) => setImmediate(r));

  test("holds the quit while it checks, then quits for real when confirmed", async () => {
    const { app, deps, guard } = setup();
    const first = quitEvent();
    app.emit("before-quit", first);
    expect(first.preventDefault).toHaveBeenCalled();
    expect(deps.onQuitting).not.toHaveBeenCalled();
    await flush();
    expect(app.quit).toHaveBeenCalledTimes(1);
    expect(deps.onQuitting).toHaveBeenCalledTimes(1);
    expect(guard.confirmed).toBe(true);
  });

  test("Keep running cancels the quit and leaves the app up", async () => {
    const { app, deps, guard } = setup({ confirm: async () => false });
    app.emit("before-quit", quitEvent());
    await flush();
    expect(app.quit).not.toHaveBeenCalled();
    expect(deps.onQuitting).not.toHaveBeenCalled();
    expect(guard.confirmed).toBe(false);
  });

  test("a second quit during the check doesn't ask twice", async () => {
    let release;
    const { app, deps } = setup({ confirm: () => new Promise((r) => { release = r; }) });
    app.emit("before-quit", quitEvent());
    const second = quitEvent();
    app.emit("before-quit", second);
    expect(second.preventDefault).toHaveBeenCalled();
    expect(deps.confirm).toHaveBeenCalledTimes(1);
    release(false);
    await flush();
  });

  test("an update restart skips the question", () => {
    const { app, updater, deps } = setup();
    updater.emit("before-quit-for-update");
    const ev = quitEvent();
    app.emit("before-quit", ev);
    expect(ev.preventDefault).not.toHaveBeenCalled();
    expect(deps.confirm).not.toHaveBeenCalled();
    expect(deps.onQuitting).toHaveBeenCalled();
  });

  test("allow() lets an OS sign-out or shutdown through", () => {
    const { app, guard, deps } = setup();
    guard.allow();
    const ev = quitEvent();
    app.emit("before-quit", ev);
    expect(ev.preventDefault).not.toHaveBeenCalled();
    expect(deps.confirm).not.toHaveBeenCalled();
  });

  test("with nothing to ask about, the quit goes straight through", () => {
    const { app, deps } = setup({ ask: false });
    const ev = quitEvent();
    app.emit("before-quit", ev);
    expect(ev.preventDefault).not.toHaveBeenCalled();
    expect(deps.onQuitting).toHaveBeenCalled();
  });

  test("a failed check is reported and the app stays up", async () => {
    const { app, deps } = setup({ confirm: async () => { throw new Error("dialog failed"); } });
    app.emit("before-quit", quitEvent());
    await flush();
    expect(deps.onError).toHaveBeenCalledWith(expect.objectContaining({ message: "dialog failed" }));
    expect(app.quit).not.toHaveBeenCalled();
  });
});

describe("main.cjs wires the guard", () => {
  // main.cjs can't be required without launching Electron; these pin the wiring
  // whose behaviour installQuitGuard's tests above cover.
  const MAIN = fs.readFileSync(
    path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
    "utf8"
  );
  const block = (start, end) => {
    const i = MAIN.indexOf(start);
    expect(i).toBeGreaterThan(-1);
    return MAIN.slice(i, MAIN.indexOf(end, i));
  };

  test("asks only while the GAIA service runs, about its real work", () => {
    const body = block("const quitGuard = installQuitGuard({", "\n});");
    expect(body).toContain("updater: electronAutoUpdater");
    expect(body).toContain("shouldAsk: () => Boolean(trayManager && backendProcess)");
    expect(body).toContain("fetchWorkInProgress(backendPort)");
  });

  test("closing the window when it quits asks first and keeps the window", () => {
    const body = block('mainWindow.on("close", (event) => {', 'mainWindow.on("closed"');
    expect(body).toMatch(/!quitGuard\.confirmed\) \{\s*event\.preventDefault\(\);\s*app\.quit\(\);/);
    expect(body).toMatch(/trayManager\s*\.showCloseNotice\(\)/);
  });

  test("an OS sign-out or shutdown never waits on the question", () => {
    expect(MAIN).toContain('mainWindow.on("session-end", () => quitGuard.allow())');
    expect(MAIN).toContain('powerMonitor.on("shutdown", () => quitGuard.allow())');
  });

  test("the GAIA service exiting shows on the tray", () => {
    expect(MAIN).toContain("trayManager.setBackendAlive(false)");
  });
});
