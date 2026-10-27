// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * DevServerSupervisor against real processes: the fixture dev server in
 * tests/fixtures/browser_pane/devserver, run with this Node. Checks what the
 * user and the agent observe: who answers on the port, which processes are
 * alive, and what the log and the ledger hold.
 */

const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const PANE = path.join(WEBUI, "services", "browser-pane");
const APP = path.resolve(__dirname, "..", "fixtures", "browser_pane", "devserver");
const BRIDGE_FIXTURES = path.resolve(__dirname, "..", "fixtures", "browser_pane", "bridge");
const { CappedLog, DevServerSupervisor, ENV_NAMES, ENV_PREFIXES, childEnv, portAnswers } = require(path.join(PANE, "dev-servers.cjs"));
const { parseRequest, createHandlers } = require(path.join(PANE, "bridge-server.cjs"));
const { BridgeError } = require(path.join(PANE, "bridge-error.cjs"));
const { listProcesses } = require(path.join(WEBUI, "services", "process-tree.cjs"));

const posix = process.platform === "win32" ? describe.skip : describe;
const quiet = { log: () => {}, error: () => {} };
const KEY = "0123456789abcdef";
const fixture = (name) => JSON.parse(fs.readFileSync(path.join(BRIDGE_FIXTURES, name), "utf8"));

function freePort() {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

async function waitFor(check, ms = 5000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

function spec(port, extra = []) {
  return {
    name: "web",
    argv: [process.execPath, "server.js", "--port", String(port), ...extra],
    cwd: APP,
    port,
    env: {},
    url: `http://localhost:${port}/`,
    readyTimeoutMs: 10000,
  };
}

const supervisors = [];
const dirs = [];
function supervisor(deps = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-devservers-"));
  dirs.push(dir);
  const onChange = jest.fn();
  const sup = new DevServerSupervisor({ dir, blockedPorts: () => [], onChange, logger: quiet, ...deps });
  supervisors.push(sup);
  return { sup, dir, onChange };
}

afterEach(async () => {
  await Promise.all(supervisors.splice(0).map((s) => s.stopAll().catch(() => {})));
});
afterAll(() => {
  for (const dir of dirs) fs.rmSync(dir, { recursive: true, force: true });
});

posix("ensure", () => {
  test("starts the server, waits for its port, and reports it ready", async () => {
    const { sup, onChange } = supervisor();
    await sup.start();
    const port = await freePort();
    const res = await sup.ensure(KEY, spec(port), "chat-1");
    expect(res.reused).toBe(false);
    expect(res.server).toMatchObject({ key: KEY, name: "web", status: "ready", port, url: `http://localhost:${port}/` });
    expect(await portAnswers(port)).toBe(true);
    expect(onChange).toHaveBeenCalled();
    expect(sup.list().map((s) => s.status)).toEqual(["ready"]);
  });

  test("the same spec again returns the running server and starts nothing", async () => {
    const spawn = jest.fn(childProcess.spawn);
    const { sup } = supervisor({ spawn });
    await sup.start();
    const port = await freePort();
    const first = await sup.ensure(KEY, spec(port), "chat-1");
    const [a, b] = await Promise.all([sup.ensure(KEY, spec(port), "chat-1"), sup.ensure(KEY, spec(port), "chat-1")]);
    expect(spawn).toHaveBeenCalledTimes(1);
    expect([a.reused, b.reused]).toEqual([true, true]);
    expect(a.server.pid).toBe(first.server.pid);
  });

  test("a changed spec stops the old server and starts the new one", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    const first = await sup.ensure(KEY, spec(port), "chat-1");
    const second = await sup.ensure(KEY, { ...spec(port), env: { NODE_ENV: "production" } }, "chat-1");
    expect(second.reused).toBe(false);
    expect(second.server.pid).not.toBe(first.server.pid);
    expect(alive(first.server.pid)).toBe(false);
    expect(await portAnswers(port)).toBe(true);
  });

  test("a server that exits first fails with its last log lines", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    const err = await sup.ensure(KEY, spec(port, ["--crash"]), "chat-1").catch((e) => e);
    expect(err).toBeInstanceOf(BridgeError);
    expect(err.code).toBe("dev_server_failed");
    expect(err.message).toMatch(/exited before it answered on port \d+ \(exit code 1\)/);
    expect(err.message).toContain("Error: Cannot find module './missing-config'");
    expect(err.data.lines.length).toBeLessThanOrEqual(30);
    expect(sup.list()[0].status).toBe("failed");
  });

  test("a program that is not installed fails and says so", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    const err = await sup.ensure(KEY, { ...spec(port), argv: ["gaia-no-such-program-xyz"] }, "chat-1").catch((e) => e);
    expect(err.code).toBe("dev_server_failed");
    expect(err.message).toMatch(/Could not start gaia-no-such-program-xyz: .*ENOENT/);
  });

  test("a port someone else holds is refused before anything starts", async () => {
    const spawn = jest.fn(childProcess.spawn);
    const { sup } = supervisor({ spawn });
    await sup.start();
    const port = await freePort();
    const stranger = net.createServer().listen(port, "127.0.0.1");
    await new Promise((r) => stranger.once("listening", r));
    const err = await sup.ensure(KEY, spec(port), "chat-1").catch((e) => e);
    stranger.close();
    expect(err.code).toBe("port_in_use");
    expect(err.message).toMatch(/another program is already listening/);
    expect(spawn).not.toHaveBeenCalled();
  });

  test("GAIA's own ports are refused", async () => {
    const port = await freePort();
    const { sup } = supervisor({ blockedPorts: () => [port] });
    await sup.start();
    await expect(sup.ensure(KEY, spec(port), "chat-1")).rejects.toMatchObject({ code: "port_in_use" });
  });

  test("the server gets only the allowed environment", () => {
    const env = childEnv({ env: { NODE_ENV: "development" } }, { PATH: "/bin", HOME: "/h", ANTHROPIC_API_KEY: "sk-x", GAIA_BROWSER_PANE_DISCOVERY: "/f" });
    expect(env).toEqual({ PATH: "/bin", HOME: "/h", NODE_ENV: "development" });
  });

  test("the server finds tools on the backend's login PATH, not main's", () => {
    const env = childEnv({ env: {} }, { PATH: "/usr/bin:/bin", HOME: "/h" }, "/opt/homebrew/bin:/usr/bin:/bin");
    expect(env.PATH).toBe("/opt/homebrew/bin:/usr/bin:/bin");
  });

  test("ensure before start is a bug, not a silent wait", async () => {
    const { sup } = supervisor();
    await expect(sup.ensure(KEY, spec(1234), "chat-1")).rejects.toMatchObject({ code: "internal" });
  });
});

posix("stop, quit and orphans", () => {
  test("stop ends the server and frees its port; stopping again is harmless", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    const { server } = await sup.ensure(KEY, spec(port), "chat-1");
    const stopped = await sup.stop(KEY);
    expect(stopped).toMatchObject({ status: "stopped", pid: null });
    expect(alive(server.pid)).toBe(false);
    expect(await portAnswers(port)).toBe(false);
    await expect(sup.stop(KEY)).resolves.toMatchObject({ status: "stopped" });
    await expect(sup.stop("ffffffffffffffff")).rejects.toMatchObject({ code: "no_such_server" });
  });

  test("quit stops every server and clears the ledger", async () => {
    const { sup, dir } = supervisor();
    await sup.start();
    const [p1, p2] = [await freePort(), await freePort()];
    const a = await sup.ensure(KEY, spec(p1), "chat-1");
    const b = await sup.ensure("fedcba9876543210", { ...spec(p2), name: "api" }, "chat-1");
    expect(JSON.parse(fs.readFileSync(path.join(dir, "ledger.json"), "utf8")).servers.map((s) => s.name).sort()).toEqual(["api", "web"]);
    await sup.stopAll();
    expect([alive(a.server.pid), alive(b.server.pid)]).toEqual([false, false]);
    expect(fs.existsSync(path.join(dir, "ledger.json"))).toBe(false);
  });

  test("the next launch stops what a crash left, by recorded pid and matching command", async () => {
    const { sup, dir } = supervisor();
    await sup.start();
    const port = await freePort();
    const { server } = await sup.ensure(KEY, spec(port), "chat-1");
    const ledger = JSON.parse(fs.readFileSync(path.join(dir, "ledger.json"), "utf8"));
    expect(ledger.servers[0].processes[0]).toMatchObject({ pid: server.pid, command: expect.stringContaining("server.js") });
    // The app died here: a new supervisor reads the same ledger, nothing else.
    supervisors.splice(supervisors.indexOf(sup), 1);
    const next = new DevServerSupervisor({ dir, blockedPorts: () => [], onChange: () => {}, logger: quiet });
    const { reaped } = await next.reapOrphans();
    expect(reaped).toEqual([server.pid]);
    expect(await waitFor(() => !alive(server.pid))).toBe(true);
    expect(fs.existsSync(path.join(dir, "ledger.json"))).toBe(false);
  });

  test("a recorded pid now running something else is never signalled", async () => {
    const stranger = childProcess.spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-devservers-"));
    dirs.push(dir);
    fs.writeFileSync(
      path.join(dir, "ledger.json"),
      JSON.stringify({ appPid: 1, servers: [{ key: KEY, name: "web", processes: [{ pid: stranger.pid, command: "node server.js --port 4173" }] }] })
    );
    const sup = new DevServerSupervisor({ dir, blockedPorts: () => [], onChange: () => {}, logger: quiet });
    try {
      expect((await sup.reapOrphans()).reaped).toEqual([]);
      expect(alive(stranger.pid)).toBe(true);
    } finally {
      stranger.kill("SIGKILL");
    }
  });

  test("a ledger the table can't be read for stays for the next launch", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-devservers-"));
    dirs.push(dir);
    const file = path.join(dir, "ledger.json");
    fs.writeFileSync(file, JSON.stringify({ servers: [{ processes: [{ pid: 99999, command: "x" }] }] }));
    const error = jest.fn();
    const sup = new DevServerSupervisor({ dir, blockedPorts: () => [], onChange: () => {}, logger: { log: () => {}, error }, listProcesses: () => Promise.reject(new Error("ps failed")) });
    await sup.start();
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/ps failed\. Its ledger stays/));
    expect(fs.existsSync(file)).toBe(true);
  });

  test("the ledger is read from the real process table", async () => {
    const table = await listProcesses();
    expect(table.some((r) => r.pid === process.pid)).toBe(true);
  });
});

posix("the chats a server belongs to", () => {
  test("each server names the chat that started it, then the chats that asked for it since", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    await sup.ensure(KEY, spec(port), "chat-a");
    await sup.ensure(KEY, spec(port), "chat-b");
    await sup.ensure(KEY, spec(port), "chat-a");
    expect(sup.list()[0].chats).toEqual(["chat-a", "chat-b"]);
    expect(() => sup.ensure(KEY, spec(port), undefined)).toThrow(/must be started for a chat/);
  });

  test("deleting a chat stops and forgets the servers only it used, and keeps a server another chat still uses", async () => {
    const { sup, dir, onChange } = supervisor();
    await sup.start();
    const [p1, p2] = [await freePort(), await freePort()];
    const own = await sup.ensure(KEY, spec(p1), "chat-a");
    const shared = await sup.ensure("fedcba9876543210", { ...spec(p2), name: "api" }, "chat-a");
    await sup.ensure("fedcba9876543210", { ...spec(p2), name: "api" }, "chat-b");
    onChange.mockClear();

    await sup.forgetChat("chat-a");

    expect(alive(own.server.pid)).toBe(false);
    expect(await portAnswers(p1)).toBe(false);
    expect(alive(shared.server.pid)).toBe(true);
    expect(sup.list()).toEqual([expect.objectContaining({ name: "api", status: "ready", chats: ["chat-b"] })]);
    expect(JSON.parse(fs.readFileSync(path.join(dir, "ledger.json"), "utf8")).servers.map((s) => s.name)).toEqual(["api"]);
    expect(onChange).toHaveBeenCalled();
  });

  test("a start already under way when its chat is deleted is stopped once it lands", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    const starting = sup.ensure(KEY, spec(port), "chat-a");
    const forgotten = sup.forgetChat("chat-a");
    const { server } = await starting;
    await forgotten;
    expect(alive(server.pid)).toBe(false);
    expect(sup.list()).toEqual([]);
  });
});

posix("logs", () => {
  test("a filtered slice returns the build error and only matching lines", async () => {
    const { sup } = supervisor();
    await sup.start();
    const port = await freePort();
    await sup.ensure(KEY, spec(port), "chat-1");
    await fetch(`http://localhost:${port}/`);
    const errors = sup.logs(KEY, { errorsOnly: true, pattern: null, limit: 50 });
    expect(errors.lines).toEqual([]);
    const gets = sup.logs(KEY, { errorsOnly: false, pattern: "get /", limit: 50 });
    expect(gets.lines).toEqual(["GET /"]);
    expect(gets.total).toBeGreaterThan(gets.matched);
  });

  test("the log file stays under its cap and keeps the newest lines", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-devservers-"));
    dirs.push(dir);
    const log = new CappedLog(path.join(dir, "x.log"), 1000);
    for (let i = 0; i < 200; i += 1) log.write(`line ${i}\n`);
    log.close();
    const text = fs.readFileSync(path.join(dir, "x.log"), "utf8");
    expect(text.length).toBeLessThanOrEqual(1000);
    expect(text).toMatch(/^\[GAIA: older lines were dropped/);
    expect(text.trimEnd().endsWith("line 199")).toBe(true);
  });
});

describe("bridge op", () => {
  const requests = fixture("requests.json");

  test("env names match the list Python uses", () => {
    const shared = fixture("dev_server_env.json");
    expect([...ENV_NAMES].sort()).toEqual(shared.names);
    expect(ENV_PREFIXES).toEqual(shared.prefixes);
  });

  test.each([
    [{ ...requests.devServers_ensure, key: "../etc" }, /"key" must be 16 hex digits/],
    [{ ...requests.devServers_ensure, spec: { ...requests.devServers_ensure.spec, cwd: "relative/dir" } }, /absolute folder/],
    [{ ...requests.devServers_ensure, spec: { ...requests.devServers_ensure.spec, argv: "npm run dev" } }, /"spec.argv"/],
    [{ ...requests.devServers_ensure, spec: { ...requests.devServers_ensure.spec, env: { ANTHROPIC_API_KEY: "x" } } }, /may not set "ANTHROPIC_API_KEY"/],
    [{ ...requests.devServers_ensure, spec: { ...requests.devServers_ensure.spec, url: "http://evil.example:4173/" } }, /http:\/\/localhost:4173/],
    [{ ...requests.devServers_logs, limit: 0 }, /"limit"/],
  ])("a bad devServers body names what is wrong", (body, message) => {
    expect(() => parseRequest("devServers", body)).toThrow(message);
  });

  test("every op checks the chat first, then reaches the supervisor", async () => {
    const devServers = { list: jest.fn(() => []), ensure: jest.fn(), stop: jest.fn(), logs: jest.fn() };
    const host = { requireSession: jest.fn(() => { throw new BridgeError("not_in_desktop", "switched"); }) };
    const handlers = createHandlers({ getHost: () => host, devServers, appVersion: "x" });
    const { req } = parseRequest("devServers", requests.devServers_ensure);
    await expect(handlers.devServers(req)).rejects.toMatchObject({ code: "not_in_desktop" });
    expect(devServers.ensure).not.toHaveBeenCalled();
    host.requireSession = jest.fn();
    await handlers.devServers(req);
    expect(devServers.ensure).toHaveBeenCalledWith(
      KEY,
      requests.devServers_ensure.spec,
      requests.devServers_ensure.sessionId,
      requests.devServers_ensure.searchPath
    );
  });
});
