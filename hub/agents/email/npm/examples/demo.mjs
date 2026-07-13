#!/usr/bin/env node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Integration smoke test + health check / example for @amd-gaia/agent-email.
 *
 * Spawns the email sidecar, runs a HEALTH CHECK that says plainly what (if
 * anything) is wrong — sidecar down, Lemonade not found, model not downloaded —
 * then drives EVERY standalone endpoint and prints a PASS/SKIP tally:
 *
 *   health · version · emailHealth · emailVersion · openapi · spec · draft · triage
 *
 * `send` is intentionally SKIPPED — it requires a connected Gmail/Outlook mailbox
 * (OAuth via the GAIA connectors), which this standalone example can't provide.
 * A connector-backed demo is a later addition (see the package README).
 *
 * What needs what:
 *   - health/version/emailHealth/emailVersion/openapi/spec/draft → work with NO
 *     Lemonade and NO mailbox. Running this proves your integration end-to-end.
 *   - triage → routes fine without Lemonade but returns 502 for a *live* result;
 *     start `lemonade-server serve` (and `gaia init` for the model) for a real one.
 *
 * Runs against EITHER:
 *   - the frozen binary  (set AGENT_EMAIL_BINARY=/path/to/email-agent[.exe]), or
 *   - the dev server.py  (default; set PYTHON=... to choose the interpreter).
 *
 * Usage:
 *   npm run build && node scripts/demo.mjs
 *   AGENT_EMAIL_BINARY=../python/packaging/dist/email-agent/email-agent.exe node scripts/demo.mjs
 *
 * This imports from dist/ — run `npm run build` first.
 */

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  EmailClient,
  HttpError,
  waitForHealth,
  checkVersion,
  shutdown,
} from "../dist/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(PKG_ROOT, "..", "..", "..", "..");
const SERVER_PY = path.join(
  REPO_ROOT,
  "hub",
  "agents",
  "python",
  "email",
  "packaging",
  "server.py",
);

const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT || 8131); // never 4001
const baseUrl = `http://${HOST}:${PORT}`;

function log(msg) {
  process.stdout.write(`[demo] ${msg}\n`);
}

// Translate a failed triage probe into an actionable cause + fix. Until the
// /v1/init readiness endpoint lands (#1795), the sidecar's own 502/timeout IS
// the signal — we surface its detail rather than guess Lemonade's URL/model here
// (the sidecar owns that config). Heuristic matching on the sidecar's message.
function diagnoseTriage(e) {
  const body = `${e?.bodyText ?? e?.message ?? ""}`.toLowerCase();
  // Specific causes (from the sidecar's own 502 detail) first; generic
  // client-side timeout last so a precise message always wins.
  if (body.includes("not reachable") || body.includes("refused") || body.includes("connection")) {
    return {
      cause: "Lemonade not found / not running",
      hint: "Start it: `lemonade-server serve` — install it with `gaia init`.",
    };
  }
  if (body.includes("model") || body.includes("not found") || body.includes("404") || body.includes("load") || body.includes("download")) {
    return {
      cause: "Model not downloaded / unavailable",
      hint: "Download + test the model: `gaia init`.",
    };
  }
  if (e?.status === 0 || body.includes("timed out") || body.includes("timeout")) {
    return {
      cause: "Lemonade not responding (timed out)",
      hint: "Is `lemonade-server serve` running and reachable on the expected port? (set LEMONADE_BASE_URL if non-default)",
    };
  }
  return {
    cause: "LLM triage failed",
    hint: `Sidecar said: ${e?.bodyText || e?.message || "unknown error"}`,
  };
}

function pickPython() {
  if (process.env.PYTHON) return [process.env.PYTHON];
  const isWin = process.platform === "win32";
  const venv = isWin
    ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(REPO_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return [venv];
  return [isWin ? "python" : "python3"];
}

function startProcess() {
  const binary = process.env.AGENT_EMAIL_BINARY;
  const detached = process.platform !== "win32";
  if (binary) {
    if (!fs.existsSync(binary)) {
      throw new Error(`AGENT_EMAIL_BINARY does not exist: ${binary}`);
    }
    log(`launching frozen binary: ${binary}`);
    return spawn(binary, ["--host", HOST, "--port", String(PORT)], {
      detached,
      stdio: ["ignore", "pipe", "pipe"],
    });
  }
  const [py, ...pre] = pickPython();
  log(`launching dev server.py via ${py}`);
  if (!fs.existsSync(SERVER_PY)) {
    throw new Error(`server.py not found at ${SERVER_PY}`);
  }
  return spawn(py, [...pre, SERVER_PY, "--host", HOST, "--port", String(PORT)], {
    detached,
    stdio: ["ignore", "pipe", "pipe"],
    cwd: REPO_ROOT,
  });
}

async function main() {
  const child = startProcess();
  child.stdout?.on("data", (d) => {
    if (process.env.DEBUG) process.stderr.write(`[sidecar] ${d}`);
  });
  child.stderr?.on("data", (d) => {
    if (process.env.DEBUG) process.stderr.write(`[sidecar] ${d}`);
  });

  // Wrap into the package's Sidecar shape so we reuse shutdown()'s tree-kill.
  const client = new EmailClient({ baseUrl, timeoutMs: 15_000 });
  const sidecar = { child, host: HOST, port: PORT, baseUrl, client };

  const results = [];
  const pass = (name, detail) => {
    results.push({ name, status: "PASS", detail });
    log(`  ✓ ${name} — ${detail}`);
  };
  const skip = (name, detail) => {
    results.push({ name, status: "SKIP", detail });
    log(`  • ${name} — SKIP: ${detail}`);
  };

  try {
    log(`waiting for /health at ${baseUrl} ...`);
    await waitForHealth(baseUrl, { timeoutMs: 60_000 });

    // ── Health check ─────────────────────────────────────────────────────
    // Two questions: (1) is the sidecar up? (2) is the LLM stack — Lemonade +
    // the model — actually ready? `/health` only answers (1); we answer (2) by
    // probing triage and diagnosing the failure if it 502s/times out.
    log("Health check:");
    const h = await client.health();
    const v = await client.version();
    await checkVersion(client); // contract MAJOR guard
    log(`  ✓ sidecar          up — service=${h.service} apiVersion=${v.apiVersion} agentVersion=${v.agentVersion}`);

    const probe = {
      payload: {
        kind: "single",
        principal: { email: "me@example.com" },
        message: {
          message_id: "demo-1",
          from: { name: "Sarah Chen", email: "sarah@example.com" },
          to: [{ email: "me@example.com" }],
          subject: "Prod incident follow-up",
          body: "Please review the incident report and reply by Friday. Action required.",
        },
      },
    };
    let stack;
    try {
      const res = await client.triage(probe);
      stack = {
        ready: true,
        detail: `category=${res.result.category} actions=${res.result.action_items.length}`,
      };
      log(`  ✓ Lemonade+model   ready — live triage: ${stack.detail}`);
    } catch (e) {
      // 502 / HTTP-0 timeout = the API routed fine but the LLM backend isn't
      // ready. Diagnose it. Any other status (400/404) is a real bug → throw.
      if (e instanceof HttpError && (e.status === 502 || e.status === 0)) {
        stack = { ready: false, ...diagnoseTriage(e) };
        log(`  ✗ ${stack.cause}`);
        log(`       → ${stack.hint}`);
      } else {
        throw e;
      }
    }

    // ── Endpoint coverage (standalone — no connector, no mailbox) ─────────
    log("Endpoint coverage (standalone):");
    pass("health()", `status=${h.status} service=${h.service}`);
    pass("version()", `apiVersion=${v.apiVersion} agentVersion=${v.agentVersion}`);
    const eh = await client.emailHealth();
    pass("emailHealth()", `status=${eh.status}`);
    const ev = await client.emailVersion();
    pass("emailVersion()", `apiVersion=${ev.apiVersion}`);
    const api = await client.openapi();
    pass("openapi()", `openapi=${api.openapi} paths=${Object.keys(api.paths || {}).length}`);
    const specHtml = await client.spec();
    pass("spec()", `${specHtml.length} bytes of HTML`);
    // draft is standalone — mints a token from the reply you pass in (no LLM,
    // no mailbox), so it passes even when the model stack is not ready.
    const d = await client.draft({
      to: [{ email: "sarah@example.com" }],
      subject: "Re: Prod incident follow-up",
      body: "Reviewed — I'll reply by Friday.",
    });
    pass("draft()", `confirmation_token=${d.confirmation_token.slice(0, 8)}…`);
    if (stack.ready) {
      pass("triage()", `live result (${stack.detail})`);
    } else {
      skip("triage()", `endpoint routed OK; ${stack.cause} — see Health check above`);
    }
    // prescan — CONNECTOR-GATED but READ-ONLY, so we actually try it. A 503
    // (no mailbox) or 400 (2+ mailboxes) means the endpoint routed fine but no
    // single mailbox is connected — skip with the reason rather than fail.
    try {
      const ps = await client.prescan({ max_messages: 10 });
      const t = ps.result.totals || {};
      pass(
        "prescan()",
        `urgent=${t.urgent ?? ps.result.urgent.length} ` +
          `actionable=${t.actionable ?? ps.result.actionable.length} ` +
          `archives=${t.suggested_archives ?? ps.result.suggested_archives.length}`,
      );
    } catch (e) {
      if (e instanceof HttpError && (e.status === 503 || e.status === 400)) {
        skip("prescan()", "endpoint routed OK; needs a single connected Gmail/Outlook mailbox");
      } else {
        throw e;
      }
    }
    // send — CONNECTOR-GATED. Not exercised: needs a connected Gmail/Outlook
    // mailbox (OAuth). A connector-backed demo is a later addition.
    skip("send()", "needs a connected Gmail/Outlook mailbox (OAuth) — connector flow not covered yet");

    log("");
    log("Summary:");
    for (const r of results) {
      const mark = r.status === "PASS" ? "✓" : "•";
      log(`  ${mark} ${r.name.padEnd(16)} ${r.status.padEnd(4)}  ${r.detail}`);
    }
    const passed = results.filter((r) => r.status === "PASS").length;
    log("");
    log(
      `STACK HEALTH: ${stack.ready ? "✓ READY — sidecar + Lemonade + model" : `✗ NOT READY — ${stack.cause}: ${stack.hint}`}`,
    );
    log(`ENDPOINTS: ${passed} standalone endpoint(s) PASS; send() is connector-gated (skipped).`);
  } finally {
    log("shutting down (tree-kill) ...");
    await shutdown(sidecar).catch((e) => log(`shutdown warn: ${e.message}`));
  }
}

main().catch((e) => {
  process.stderr.write(`[demo] FAILED: ${e.stack || e}\n`);
  process.exit(1);
});
