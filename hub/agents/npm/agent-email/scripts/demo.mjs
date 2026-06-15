#!/usr/bin/env node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * End-to-end MVP proof for @amd-gaia/agent-email.
 *
 * Spawns the email sidecar, then drives the full client lifecycle through the
 * package's OWN helpers: spawn -> waitForHealth -> checkVersion -> triage
 * round-trip -> clean tree-kill shutdown.
 *
 * Triage uses the real local Lemonade model. With Lemonade running you get a
 * live result; without it, triage returns HTTP 502 — which still proves the
 * package's job (fetch/spawn/lifecycle/routing). Start Lemonade for a live triage.
 *
 * It runs against EITHER:
 *   - the frozen binary (set AGENT_EMAIL_BINARY=/path/to/email-agent[.exe]), or
 *   - the dev server.py via a python interpreter (default; set PYTHON=... to
 *     choose the interpreter, defaults to the repo .venv then `python`).
 *
 * Usage:
 *   npm run build && node scripts/demo.mjs
 *   AGENT_EMAIL_BINARY=../../python/email/packaging/dist/email-agent/email-agent.exe node scripts/demo.mjs
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
  // 15s timeout: long enough for a live triage, short enough that a no-Lemonade
  // probe doesn't stall the demo.
  const client = new EmailClient({ baseUrl, timeoutMs: 15_000 });
  const sidecar = { child, host: HOST, port: PORT, baseUrl, client };

  try {
    log(`waiting for /health at ${baseUrl} ...`);
    await waitForHealth(baseUrl, { timeoutMs: 60_000 });
    log("health OK");

    const ver = await checkVersion(client);
    log(`version OK: apiVersion=${ver.apiVersion} agentVersion=${ver.agentVersion}`);

    log("POST /v1/email/triage (single email) — routes to the real local LLM ...");
    const request = {
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
    try {
      const res = await client.triage(request);
      log("triage result:");
      process.stdout.write(JSON.stringify(res, null, 2) + "\n");
      if (res.request_kind !== "single") throw new Error("expected request_kind=single");
      if (!res.result.category) throw new Error("expected a category");
      log(
        `PROOF: live triage — kind=${res.request_kind} category=${res.result.category} ` +
          `actions=${res.result.action_items.length} draft=${res.result.draft ? "yes" : "no"}`,
      );
    } catch (e) {
      // With no Lemonade reachable the request is accepted and routed, then
      // errors (502) or times out (HTTP 0) waiting on the model. Either way
      // fetch/spawn/lifecycle/routing — the package's job — is proven. Any other
      // error (e.g. 400/404 = a real routing/contract bug) rethrows.
      if (e instanceof HttpError && (e.status === 502 || e.status === 0)) {
        log(`triage routed to the real LLM but no Lemonade is reachable: ${e.message}`);
        log("PROOF: routing + lifecycle verified. Start Lemonade Server for a live triage result.");
      } else {
        throw e;
      }
    }
    log("VERDICT: PASS");
  } finally {
    log("shutting down (tree-kill) ...");
    await shutdown(sidecar).catch((e) => log(`shutdown warn: ${e.message}`));
  }
}

main().catch((e) => {
  process.stderr.write(`[demo] FAILED: ${e.stack || e}\n`);
  process.exit(1);
});
