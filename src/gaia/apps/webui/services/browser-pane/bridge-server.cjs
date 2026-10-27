// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane's bridge: how the GAIA agent, in the backend or in a
 * daemon sidecar, reaches the pane. A loopback HTTP server with a bearer
 * token, announced in a discovery file only the user can read.
 *
 * Thin by design. It refuses anything a web page or a rebound DNS name could
 * send, parses each body into a typed op, and hands it to PaneHost. Python's
 * half is gaia/browser/_pane_bridge.py; both test against
 * tests/fixtures/browser_pane/bridge/*.json.
 */

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { BridgeError } = require("./bridge-error.cjs");
const { KEYS } = require("./input.cjs");
const { envNameAllowed } = require("./dev-servers.cjs");

/** @typedef {import("./contract").BridgeDiscovery} BridgeDiscovery */
/** @typedef {import("./contract").BridgeOp} BridgeOp */

const BRIDGE_PROTOCOL = 1;
/** Env var naming the discovery file for the backend main starts. Python reads it first. */
const DISCOVERY_ENV = "GAIA_BROWSER_PANE_DISCOVERY";
const MAX_BODY_BYTES = 1024 * 1024;
const MAX_SCRIPT_CHARS = 512 * 1024;
const MAX_LOG_LIMIT = 500;
const MAX_SHOT_WIDTH = 4096;
const MAX_TYPED_CHARS = 10000;
const MAX_EXPRESSION_CHARS = 2000;
const VIEWPORT_MIN = 200;
const VIEWPORT_MAX = 4000;
const MAX_ARGV = 64;
const MAX_READY_MS = 120000;

const STATUS = {
  bad_request: 400,
  unauthorized: 401,
  forbidden: 403,
  unknown_op: 404,
  no_such_tab: 404,
  timeout: 504,
  internal: 500,
};

/**
 * Where the discovery file goes. Dev QA mode keeps it in the throwaway user
 * data dir, so a dev build never overwrites the installed app's file.
 *
 * @param {{ devQa: boolean, userDataDir: string, gaiaDir: string }} where
 */
function discoveryPath({ devQa, userDataDir, gaiaDir }) {
  return devQa ? path.join(userDataDir, "run", "browser-pane.json") : path.join(gaiaDir, "run", "browser-pane.json");
}

// ── Parsing ─────────────────────────────────────────────────────────────

const bad = (message) => new BridgeError("bad_request", message);

function str(body, key, { max = 4096 } = {}) {
  const v = body[key];
  if (typeof v !== "string" || v === "" || v.length > max) throw bad(`"${key}" must be a non-empty string of at most ${max} characters.`);
  return v;
}

const session = (body) => str(body, "sessionId", { max: 200 });

function tabIdOrActive(body) {
  const v = body.tabId;
  if (v === null || v === undefined) return null;
  if (typeof v !== "string" || v === "") throw bad('"tabId" must be a tab id or null for the active tab.');
  return v;
}

function bool(body, key) {
  if (typeof body[key] !== "boolean") throw bad(`"${key}" must be true or false.`);
  return body[key];
}

function int(body, key, min, max) {
  const v = body[key];
  if (!Number.isInteger(v) || v < min || v > max) throw bad(`"${key}" must be a whole number from ${min} to ${max}.`);
  return v;
}

function oneOf(body, key, values) {
  if (!values.includes(body[key])) throw bad(`"${key}" must be one of ${values.join(", ")}.`);
  return body[key];
}

/** Origins exactly as URL.origin spells them; anything looser could widen the scope. */
function origins(body) {
  const v = body.allowedOrigins;
  if (!Array.isArray(v)) throw bad('"allowedOrigins" must be a list of origins.');
  for (const o of v) {
    let parsed = null;
    try {
      parsed = typeof o === "string" ? new URL(o) : null;
    } catch {
      parsed = null;
    }
    if (!parsed || !/^https?:$/.test(parsed.protocol) || parsed.origin !== o) {
      throw bad(`"allowedOrigins" holds ${JSON.stringify(o)}, which is not an http(s) origin like https://example.com.`);
    }
  }
  return v;
}

/** An element: { frameId, selector }, with frameId null for the main frame. */
function element(value, key) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw bad(`"${key}" must be { frameId, selector }.`);
  const frameId = value.frameId;
  if (frameId !== null && typeof frameId !== "string") throw bad(`"${key}.frameId" must be a frame id or null.`);
  return { frameId, selector: str(value, "selector", { max: 200 }) };
}

function action(body) {
  const a = body.action;
  if (typeof a !== "object" || a === null) throw bad('"action" must be { kind, ... }.');
  const kind = oneOf(a, "kind", ["click", "fill", "press"]);
  if (kind === "click") return { kind };
  if (kind === "press") return { kind, key: oneOf(a, "key", Object.keys(KEYS)) };
  if (typeof a.text !== "string" || a.text.length > MAX_TYPED_CHARS) throw bad(`"action.text" must be text of at most ${MAX_TYPED_CHARS} characters.`);
  return { kind, text: a.text, pressEnter: bool(a, "pressEnter") };
}

function viewportSize(body) {
  const v = body.size;
  if (v === null) return null;
  if (typeof v !== "object" || Array.isArray(v)) throw bad('"size" must be null or { width, height, mobile }.');
  return { width: int(v, "width", VIEWPORT_MIN, VIEWPORT_MAX), height: int(v, "height", VIEWPORT_MIN, VIEWPORT_MAX), mobile: bool(v, "mobile") };
}

const serverKey = (body) => {
  const key = str(body, "key", { max: 16 });
  if (!/^[0-9a-f]{16}$/.test(key)) throw bad('"key" must be 16 hex digits.');
  return key;
};

/** A dev server as Python resolved it from launch.json: argv only, an absolute cwd, allowed env names. */
function devServerSpec(value) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw bad('"spec" must be a dev server spec.');
  const argv = value.argv;
  if (!Array.isArray(argv) || argv.length === 0 || argv.length > MAX_ARGV || !argv.every((a) => typeof a === "string" && a !== "" && a.length <= 4096 && !a.includes("\0"))) {
    throw bad(`"spec.argv" must be 1 to ${MAX_ARGV} non-empty strings.`);
  }
  const cwd = str(value, "cwd");
  if (!path.isAbsolute(cwd)) throw bad('"spec.cwd" must be an absolute folder.');
  const port = int(value, "port", 1024, 65535);
  const env = value.env;
  if (typeof env !== "object" || env === null || Array.isArray(env)) throw bad('"spec.env" must be an object.');
  for (const [k, v] of Object.entries(env)) {
    if (!envNameAllowed(k) || typeof v !== "string" || v.length > 4096) throw bad(`"spec.env" may not set ${JSON.stringify(k)}.`);
  }
  const url = str(value, "url");
  let parsed = null;
  try {
    parsed = new URL(url);
  } catch {
    parsed = null;
  }
  if (!parsed || parsed.protocol !== "http:" || parsed.hostname !== "localhost" || Number(parsed.port) !== port) {
    throw bad(`"spec.url" must be http://localhost:${port}/...`);
  }
  return {
    name: str(value, "name", { max: 64 }),
    argv: argv.slice(),
    cwd,
    port,
    env: { ...env },
    url,
    readyTimeoutMs: int(value, "readyTimeoutMs", 1000, MAX_READY_MS),
  };
}

/** One parser per op. A test asserts this table and the handler table have the same keys. */
const PARSERS = {
  hello: () => ({}),
  attach: (b) => ({ sessionId: str(b, "sessionId", { max: 200 }) }),
  tabs: (b) => {
    const sessionId = session(b);
    const op = oneOf(b, "op", ["list", "open", "select"]);
    if (op === "open") return { sessionId, op, url: str(b, "url"), allowedOrigins: origins(b) };
    if (op === "select") return { sessionId, op, tabId: str(b, "tabId", { max: 100 }) };
    return { sessionId, op };
  },
  navigate: (b) => ({ sessionId: session(b), tabId: tabIdOrActive(b), to: str(b, "to"), allowedOrigins: origins(b) }),
  evaluate: (b) => {
    if (!("args" in b)) throw bad('"args" is required (use null for none).');
    return {
      sessionId: session(b),
      tabId: tabIdOrActive(b),
      script: str(b, "script", { max: MAX_SCRIPT_CHARS }),
      args: b.args,
      frames: oneOf(b, "frames", ["main", "all"]),
      allowedOrigins: origins(b),
    };
  },
  origin: (b) => ({ sessionId: session(b), tabId: tabIdOrActive(b) }),
  screenshot: (b) => {
    const clip = b.clip === null || b.clip === undefined ? null : element(b.clip, "clip");
    return { sessionId: session(b), tabId: tabIdOrActive(b), clip, maxWidth: int(b, "maxWidth", 100, MAX_SHOT_WIDTH), allowedOrigins: origins(b) };
  },
  logs: (b) => {
    const pattern = b.pattern;
    if (pattern !== null && pattern !== undefined && (typeof pattern !== "string" || pattern.length > 200)) {
      throw bad('"pattern" must be null or text of at most 200 characters.');
    }
    return {
      sessionId: session(b),
      tabId: tabIdOrActive(b),
      kind: oneOf(b, "kind", ["console", "network"]),
      afterSeq: int(b, "afterSeq", 0, Number.MAX_SAFE_INTEGER),
      errorsOnly: bool(b, "errorsOnly"),
      pattern: pattern || null,
      limit: int(b, "limit", 1, MAX_LOG_LIMIT),
      allowedOrigins: origins(b),
    };
  },
  act: (b) => {
    const act = action(b);
    const target = b.target === null || b.target === undefined ? null : element(b.target, "target");
    if (target === null && act.kind !== "press") throw bad(`A ${act.kind} needs a "target" element.`);
    return { sessionId: session(b), tabId: tabIdOrActive(b), action: act, target, allowedOrigins: origins(b) };
  },
  devServers: (b) => {
    const sessionId = session(b);
    const op = oneOf(b, "op", ["ensure", "stop", "list", "logs"]);
    if (op === "list") return { sessionId, op };
    if (op === "stop") return { sessionId, op, key: serverKey(b) };
    if (op === "ensure") {
      const searchPath = b.searchPath;
      if (searchPath !== null && (typeof searchPath !== "string" || searchPath.length > 32768)) {
        throw bad('"searchPath" must be null or a PATH string of at most 32768 characters.');
      }
      return { sessionId, op, key: serverKey(b), spec: devServerSpec(b.spec), searchPath: searchPath || null };
    }
    const pattern = b.pattern;
    if (pattern !== null && (typeof pattern !== "string" || pattern.length > 200)) throw bad('"pattern" must be null or text of at most 200 characters.');
    return { sessionId, op, key: serverKey(b), errorsOnly: bool(b, "errorsOnly"), pattern: pattern || null, limit: int(b, "limit", 1, MAX_LOG_LIMIT) };
  },
  dialog: (b) => {
    const sessionId = session(b);
    const op = oneOf(b, "op", ["get", "answer", "release"]);
    if (op === "release") return { sessionId, op };
    if (op === "get") return { sessionId, op, tabId: tabIdOrActive(b) };
    const text = b.text;
    if (text !== null && (typeof text !== "string" || text.length > MAX_TYPED_CHARS)) throw bad(`"text" must be null or text of at most ${MAX_TYPED_CHARS} characters.`);
    return { sessionId, op, tabId: tabIdOrActive(b), accept: bool(b, "accept"), text, allowedOrigins: origins(b) };
  },
  script: (b) => ({ sessionId: session(b), tabId: tabIdOrActive(b), expression: str(b, "expression", { max: MAX_EXPRESSION_CHARS }) }),
  viewport: (b) => ({
    sessionId: session(b),
    tabId: tabIdOrActive(b),
    size: viewportSize(b),
    colorScheme: b.colorScheme === null ? null : oneOf(b, "colorScheme", ["light", "dark"]),
  }),
};

/**
 * @param {string} op
 * @param {unknown} body
 * @returns {{ op: BridgeOp, req: any }}
 */
function parseRequest(op, body) {
  if (!Object.prototype.hasOwnProperty.call(PARSERS, op)) {
    throw new BridgeError("unknown_op", `Unknown bridge op "${op}". Known: ${Object.keys(PARSERS).join(", ")}.`);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw bad("The request body must be a JSON object.");
  return { op, req: PARSERS[op](body) };
}

// ── Handlers ────────────────────────────────────────────────────────────

/**
 * @param {{
 *   getHost: () => import("./pane-host.cjs").PaneHost | null,
 *   devServers: import("./dev-servers.cjs").DevServerSupervisor,
 *   appVersion: string,
 * }} deps
 */
function createHandlers({ getHost, devServers, appVersion }) {
  // Every op after attach is checked again: the user may have switched chats since.
  const host = (sessionId) => {
    const h = getHost();
    if (!h) throw new BridgeError("no_page", "GAIA desktop's window is closed, so its browser pane is too. Ask the user to reopen it.");
    h.requireSession(sessionId);
    return h;
  };
  return {
    hello: async () => ({ protocol: BRIDGE_PROTOCOL, appVersion, ops: Object.keys(PARSERS) }),
    attach: async ({ sessionId }) => {
      const h = getHost();
      return h ? h.attachSession(sessionId) : { status: "not_in_desktop" };
    },
    tabs: async (req) => {
      const h = host(req.sessionId);
      if (req.op === "open") return h.agentOpen(req.sessionId, req.url, req.allowedOrigins);
      if (req.op === "select") {
        h.agentTab(req.tabId);
        h.selectTab(req.tabId);
      }
      return h.listTabs();
    },
    navigate: async (req) => host(req.sessionId).agentNavigate(req.tabId, req.to, req.allowedOrigins),
    evaluate: async (req) => host(req.sessionId).agentEvaluate(req.tabId, req.script, req.args, req.frames, req.allowedOrigins),
    origin: async (req) => host(req.sessionId).agentOrigin(req.tabId),
    screenshot: async (req) => host(req.sessionId).agentScreenshot(req.tabId, req.clip, req.maxWidth, req.allowedOrigins),
    logs: async (req) =>
      host(req.sessionId).agentLogs(
        req.tabId,
        { kind: req.kind, afterSeq: req.afterSeq, errorsOnly: req.errorsOnly, pattern: req.pattern, limit: req.limit },
        req.allowedOrigins
      ),
    act: async (req) => host(req.sessionId).agentAct(req.tabId, req.action, req.target, req.allowedOrigins),
    dialog: async (req) => {
      const h = host(req.sessionId);
      if (req.op === "release") return h.releaseDialogs(req.sessionId);
      if (req.op === "get") return h.agentDialog(req.tabId);
      return h.agentAnswerDialog(req.tabId, req.accept, req.text, req.allowedOrigins);
    },
    script: async (req) => host(req.sessionId).agentScript(req.tabId, req.expression),
    viewport: async (req) => host(req.sessionId).agentViewport(req.tabId, { size: req.size, colorScheme: req.colorScheme }),
    devServers: async (req) => {
      host(req.sessionId);
      if (req.op === "list") return { servers: devServers.list() };
      if (req.op === "stop") return { server: await devServers.stop(req.key) };
      if (req.op === "logs") return devServers.logs(req.key, { errorsOnly: req.errorsOnly, pattern: req.pattern, limit: req.limit });
      return devServers.ensure(req.key, req.spec, req.searchPath);
    },
  };
}

// ── Server ──────────────────────────────────────────────────────────────

function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}

function sendError(res, err) {
  send(res, STATUS[err.code] || 409, { error: { code: err.code, message: err.message, data: err.data || {} } });
}

function tokenMatches(header, token) {
  const expected = Buffer.from(`Bearer ${token}`);
  const given = Buffer.from(typeof header === "string" ? header : "");
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(bad(`The request body is over ${MAX_BODY_BYTES} bytes.`));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

/** Write the file atomically at mode 0600, in a folder only the user can open. */
function writeDiscovery(file, discovery) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(discovery), { mode: 0o600 });
  fs.chmodSync(tmp, 0o600);
  fs.renameSync(tmp, file);
}

/** Remove the file, unless another app run has written its own since. */
function removeDiscovery(file, token) {
  let current;
  try {
    current = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code !== "ENOENT") console.error(`[browser-pane] Could not read ${file} to remove it: ${err.message}`);
    return;
  }
  if (current.token === token) fs.rmSync(file, { force: true });
}

/**
 * Start the bridge and announce it.
 *
 * @param {{
 *   discoveryFile: string,
 *   handlers: ReturnType<typeof createHandlers>,
 *   logger?: { log: Function, error: Function },
 * }} opts
 * @returns {Promise<{ port: number, discovery: BridgeDiscovery, close: () => Promise<void> }>}
 */
async function startBridge({ discoveryFile, handlers, logger = console }) {
  const token = crypto.randomBytes(32).toString("hex");
  let port = 0;

  const server = http.createServer(async (req, res) => {
    try {
      // A browser always sends Origin on a cross-site POST: no page, pane tabs included, gets in.
      if (req.headers.origin !== undefined) throw new BridgeError("forbidden", "Requests from web pages are refused.");
      // A name rebound to 127.0.0.1 still carries its own Host.
      const hostHeader = String(req.headers.host || "").toLowerCase();
      if (hostHeader !== `127.0.0.1:${port}` && hostHeader !== `localhost:${port}`) {
        throw new BridgeError("forbidden", `Host ${JSON.stringify(req.headers.host || "")} is not this bridge.`);
      }
      if (!tokenMatches(req.headers.authorization, token)) throw new BridgeError("unauthorized", "Missing or wrong bridge token.");
      const match = /^\/v1\/([A-Za-z]+)$/.exec(req.url || "");
      if (req.method !== "POST" || !match) throw new BridgeError("unknown_op", "Use POST /v1/<op>.");
      let body;
      try {
        body = JSON.parse(await readBody(req));
      } catch (err) {
        throw err instanceof BridgeError ? err : bad(`The request body is not JSON: ${err.message}`);
      }
      const { op, req: parsed } = parseRequest(match[1], body);
      send(res, 200, await handlers[op](parsed));
    } catch (err) {
      if (err instanceof BridgeError) {
        sendError(res, err);
        return;
      }
      logger.error(`[browser-pane] Bridge ${req.url} failed: ${(err && err.stack) || err}`);
      sendError(res, new BridgeError("internal", `The browser pane failed: ${err && err.message}`));
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  port = server.address().port;
  /** @type {BridgeDiscovery} */
  const discovery = { port, token, pid: process.pid, protocol: BRIDGE_PROTOCOL };
  // Written only once listening, so a reader never finds a port nobody answers.
  writeDiscovery(discoveryFile, discovery);
  logger.log(`[browser-pane] Bridge listening on 127.0.0.1:${port}; discovery file ${discoveryFile}`);

  let closed = false;
  return {
    port,
    discovery,
    close: async () => {
      if (closed) return;
      closed = true;
      removeDiscovery(discoveryFile, token);
      const stopped = new Promise((resolve) => server.close(() => resolve()));
      server.closeAllConnections();
      await stopped;
    },
  };
}

module.exports = {
  BRIDGE_PROTOCOL,
  DISCOVERY_ENV,
  PARSERS,
  createHandlers,
  discoveryPath,
  parseRequest,
  startBridge,
};
