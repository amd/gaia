// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The desktop app, the npm launcher and the dev server read the Agent UI API
// token from the file the backend writes, and send it only to that backend.

const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  authHeaders,
  createUiTokenReader,
  installBackendAuth,
  isBackendUrl,
  uiTokenPath,
} = require("../../src/gaia/apps/webui/services/ui-token.cjs");
const { backendJson } = require("../../src/gaia/apps/webui/services/quit-guard.cjs");
const { signInUrl } = require("../../src/gaia/apps/webui/bin/gaia-ui.cjs");

const HOME = path.join(path.sep, "home", "you");

describe("uiTokenPath", () => {
  const resolve = (env, platform = "linux") => uiTokenPath({ env, homedir: HOME, platform });

  test("defaults to ~/.gaia/agent-ui-token", () => {
    expect(resolve({})).toBe(path.join(HOME, ".gaia", "agent-ui-token"));
  });

  test("GAIA_HOME wins over GAIA_CONFIG_DIR, and blank values are skipped", () => {
    expect(resolve({ GAIA_HOME: "/srv/gaia", GAIA_CONFIG_DIR: "/etc/gaia" })).toBe(path.join("/srv/gaia", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "   ", GAIA_CONFIG_DIR: " /etc/gaia " })).toBe(path.join("/etc/gaia", "agent-ui-token"));
  });

  test("expands ~ and environment variables like the backend does", () => {
    expect(resolve({ GAIA_HOME: "~/alt" })).toBe(path.join(HOME, "alt", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "$BASE/g", BASE: "/data" })).toBe(path.join("/data/g", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "${BASE}/g", BASE: "/data" })).toBe(path.join("/data/g", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "$MISSING/g" })).toBe(path.join("$MISSING/g", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "%BASE%\\g", BASE: "D:\\data" }, "win32")).toBe(path.join("D:\\data\\g", "agent-ui-token"));
    expect(resolve({ GAIA_HOME: "%BASE%/g", BASE: "/data" }, "linux")).toBe(path.join("%BASE%/g", "agent-ui-token"));
  });

  test("GAIA_UI_TOKEN_FILE names the file outright", () => {
    expect(resolve({ GAIA_UI_TOKEN_FILE: " /run/token ", GAIA_HOME: "/srv/gaia" })).toBe("/run/token");
    expect(resolve({ GAIA_UI_TOKEN_FILE: "~/t" })).toBe(path.join(HOME, "t"));
  });
});

describe("createUiTokenReader", () => {
  let dir;
  let file;
  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-ui-token-"));
    file = path.join(dir, "agent-ui-token");
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  test("a missing file is no token, and is read again once the backend writes it", () => {
    const reader = createUiTokenReader({ resolvePath: () => file });
    expect(reader.get()).toBeNull();
    expect(authHeaders(reader)).toEqual({});
    fs.writeFileSync(file, "  secret-1\n");
    expect(reader.get()).toBe("secret-1");
    expect(authHeaders(reader)).toEqual({ Authorization: "Bearer secret-1" });
  });

  test("caches the token until clear(), so a rotated file is picked up after a 401", () => {
    fs.writeFileSync(file, "secret-1");
    const readFile = jest.fn(fs.readFileSync);
    const reader = createUiTokenReader({ resolvePath: () => file, readFile });
    expect(reader.get()).toBe("secret-1");
    fs.writeFileSync(file, "secret-2");
    expect(reader.get()).toBe("secret-1");
    expect(readFile).toHaveBeenCalledTimes(1);
    reader.clear();
    expect(reader.get()).toBe("secret-2");
  });

  test("an unreadable file is an error that names it, not a silent null", () => {
    const readFile = () => {
      throw Object.assign(new Error("EACCES: permission denied"), { code: "EACCES" });
    };
    const reader = createUiTokenReader({ resolvePath: () => file, readFile });
    expect(() => reader.get()).toThrow(`Cannot read the GAIA Agent UI token at ${file}: EACCES`);
  });
});

describe("isBackendUrl", () => {
  test("only the spawned backend's own origin, on either loopback name", () => {
    expect(isBackendUrl("http://127.0.0.1:5123/api/system/status", 5123)).toBe(true);
    expect(isBackendUrl("http://localhost:5123/api/files/uploads/a.png", 5123)).toBe(true);
    expect(isBackendUrl("http://127.0.0.1:5124/api/x", 5123)).toBe(false);
    expect(isBackendUrl("http://127.0.0.1/api/x", 5123)).toBe(false);
    expect(isBackendUrl("https://127.0.0.1:5123/api/x", 5123)).toBe(false);
    expect(isBackendUrl("http://evil.example:5123/api/x", 5123)).toBe(false);
    expect(isBackendUrl("http://127.0.0.1.evil.example:5123/", 5123)).toBe(false);
    expect(isBackendUrl("file:///C:/app/dist/index.html", 5123)).toBe(false);
    expect(isBackendUrl("not a url", 5123)).toBe(false);
  });

  test("nothing matches before a backend is spawned", () => {
    expect(isBackendUrl("http://127.0.0.1:4200/api/x", null)).toBe(false);
  });
});

describe("installBackendAuth", () => {
  function fakeSession() {
    const handlers = {};
    return {
      handlers,
      webRequest: {
        onBeforeSendHeaders: (fn) => { handlers.before = fn; },
        onCompleted: (fn) => { handlers.completed = fn; },
      },
    };
  }
  const reader = (token) => ({ get: jest.fn(() => token), clear: jest.fn() });
  const send = (session, url, requestHeaders = { Accept: "*/*" }) =>
    new Promise((done) => session.handlers.before({ url, requestHeaders }, done));

  test("adds the bearer header to the backend's requests and to nothing else", async () => {
    const session = fakeSession();
    installBackendAuth(session, { getPort: () => 5123, reader: reader("s3cret") });
    await expect(send(session, "http://127.0.0.1:5123/api/sessions/events")).resolves.toEqual({
      requestHeaders: { Accept: "*/*", Authorization: "Bearer s3cret" },
    });
    await expect(send(session, "http://localhost:5123/api/files/uploads/x.png")).resolves.toMatchObject({
      requestHeaders: { Authorization: "Bearer s3cret" },
    });
    for (const url of ["http://127.0.0.1:13305/api/v1/models", "https://github.com/amd/gaia", "file:///app/index.html"]) {
      await expect(send(session, url)).resolves.toEqual({ requestHeaders: { Accept: "*/*" } });
    }
  });

  test("follows a restarted backend's new port", async () => {
    const session = fakeSession();
    let port = 5123;
    installBackendAuth(session, { getPort: () => port, reader: reader("s3cret") });
    port = 6001;
    expect((await send(session, "http://127.0.0.1:5123/api/x")).requestHeaders.Authorization).toBeUndefined();
    expect((await send(session, "http://127.0.0.1:6001/api/x")).requestHeaders.Authorization).toBe("Bearer s3cret");
  });

  test("with no token file yet the request goes out unchanged", async () => {
    const session = fakeSession();
    installBackendAuth(session, { getPort: () => 5123, reader: reader(null) });
    await expect(send(session, "http://127.0.0.1:5123/api/x")).resolves.toEqual({ requestHeaders: { Accept: "*/*" } });
  });

  test("a read error is logged and the request still goes out", async () => {
    const session = fakeSession();
    const log = { error: jest.fn() };
    const broken = { get: () => { throw new Error("Cannot read the GAIA Agent UI token at /x: EACCES"); }, clear: jest.fn() };
    installBackendAuth(session, { getPort: () => 5123, reader: broken, log });
    await expect(send(session, "http://127.0.0.1:5123/api/x")).resolves.toEqual({ requestHeaders: { Accept: "*/*" } });
    expect(log.error).toHaveBeenCalledWith(expect.stringContaining("EACCES"));
  });

  test("a 401 from the backend clears the cached token; one from elsewhere doesn't", () => {
    const session = fakeSession();
    const r = reader("s3cret");
    installBackendAuth(session, { getPort: () => 5123, reader: r });
    session.handlers.completed({ url: "http://127.0.0.1:9999/api/x", statusCode: 401 });
    session.handlers.completed({ url: "http://127.0.0.1:5123/api/x", statusCode: 200 });
    expect(r.clear).not.toHaveBeenCalled();
    session.handlers.completed({ url: "http://127.0.0.1:5123/api/x", statusCode: 401 });
    expect(r.clear).toHaveBeenCalledTimes(1);
  });
});

describe("backendJson sends the token", () => {
  test("Authorization rides along with X-Gaia-UI", async () => {
    const fetchImpl = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const tokenReader = { get: () => "s3cret", clear: jest.fn() };
    await backendJson(4321, "/api/system/work-in-progress", { fetchImpl, tokenReader });
    expect(fetchImpl.mock.calls[0][1].headers).toEqual({ "X-Gaia-UI": "1", Authorization: "Bearer s3cret" });
  });

  test("a 401 clears the cached token so the next call re-reads the file", async () => {
    const fetchImpl = async () => ({ ok: false, status: 401, json: async () => ({ detail: "sign in", code: "ui_token_required" }) });
    const tokenReader = { get: () => "old", clear: jest.fn() };
    await expect(backendJson(1, "/api/x", { fetchImpl, tokenReader })).rejects.toMatchObject({ status: 401 });
    expect(tokenReader.clear).toHaveBeenCalledTimes(1);
  });
});

describe("gaia-ui signInUrl", () => {
  const reader = (token) => ({ get: () => token, path: () => "/home/you/.gaia/agent-ui-token" });

  test("asks the backend for a launch code with the token and returns the link", async () => {
    const fetchImpl = jest.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ path: "/?launch=abc", expires_in: 600 }) }));
    await expect(signInUrl(4200, { fetchImpl, reader: reader("s3cret") })).resolves.toBe("http://localhost:4200/?launch=abc");
    expect(fetchImpl).toHaveBeenCalledWith("http://localhost:4200/api/auth/launch-code", {
      method: "POST",
      headers: { Authorization: "Bearer s3cret", "X-Gaia-UI": "1" },
    });
  });

  test("no token file is a loud error naming the file", async () => {
    await expect(signInUrl(4200, { fetchImpl: jest.fn(), reader: reader(null) })).rejects.toThrow(
      "/home/you/.gaia/agent-ui-token does not exist"
    );
  });

  test("a refused code says which token was used and why it may not match", async () => {
    const fetchImpl = async () => ({ ok: false, status: 401, text: async () => '{"code":"ui_token_required"}' });
    await expect(signInUrl(4200, { fetchImpl, reader: reader("x") })).rejects.toThrow(/HTTP 401.*another GAIA/);
  });

  test("a reply without a path is an error, never a link to /", async () => {
    const fetchImpl = async () => ({ ok: true, status: 200, text: async () => "{}" });
    await expect(signInUrl(4200, { fetchImpl, reader: reader("x") })).rejects.toThrow("no link path");
  });
});
