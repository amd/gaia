// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * /query integration test (#2097): startSidecar spawns the REAL Python sidecar
 * (dev mode — packaging/server.py with the scripted fake-agent seam from
 * test/fixtures/query_test_server.py), then drives the typed client end-to-end
 * over real HTTP: the 2.4 version handshake, the bearer gate, the canonical
 * status -> tool_call -> tool_result -> final sequence, and mid-run cancel.
 *
 * Needs a Python interpreter that can import the email agent's deps (fastapi,
 * uvicorn, and the gaia/gaia_agent_email sources of THIS checkout). Resolution
 * order: $GAIA_EMAIL_TEST_PYTHON, then <repo>/.venv, then python3 on PATH.
 * Skips (visibly) when none can — and on Windows (the spawn shim is POSIX).
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { HttpError } from "../src/errors.js";
import { startSidecar, shutdown, type Sidecar } from "../src/lifecycle.js";
import type { QueryEvent } from "../src/types.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..", "..");
const FIXTURE = path.join(HERE, "fixtures", "query_test_server.py");
const PORT = 8200 + Math.floor(Math.random() * 500); // never 4001

function resolvePython(): string | null {
  const candidates = [
    process.env.GAIA_EMAIL_TEST_PYTHON,
    path.join(REPO_ROOT, ".venv", "bin", "python"),
    "python3",
  ].filter((c): c is string => Boolean(c));
  for (const py of candidates) {
    if (py.includes(path.sep) && !fs.existsSync(py)) continue;
    const probe = spawnSync(py, [FIXTURE, "--check"], { timeout: 60_000 });
    if (probe.status === 0) return py;
  }
  return null;
}

const python = process.platform === "win32" ? null : resolvePython();
if (!python) {
  // eslint-disable-next-line no-console
  console.warn(
    "[query-integration] SKIPPED: no Python interpreter can import the email " +
      "sidecar (set GAIA_EMAIL_TEST_PYTHON to a venv python with the repo installed).",
  );
}

async function collect(iter: AsyncIterable<QueryEvent>): Promise<QueryEvent[]> {
  const out: QueryEvent[] = [];
  for await (const ev of iter) out.push(ev);
  return out;
}

describe.skipIf(!python)("query() against the real sidecar (dev mode)", () => {
  let sidecar: Sidecar;
  let wrapperDir: string;

  beforeAll(async () => {
    // startSidecar spawns `binaryPath --host H --port P`; a python interpreter
    // can't take those before its script, so shim it with an exec wrapper.
    wrapperDir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-email-query-"));
    const wrapper = path.join(wrapperDir, "email-agent-dev");
    fs.writeFileSync(wrapper, `#!/bin/sh\nexec "${python}" "${FIXTURE}" "$@"\n`, {
      mode: 0o755,
    });
    // verifyVersion defaults ON: this IS the 2.4-handshake acceptance — the
    // client (SCHEMA_VERSION 2.4) must accept the sidecar's reported apiVersion.
    sidecar = await startSidecar({
      binaryPath: wrapper,
      port: PORT,
      healthTimeoutMs: 90_000,
    });
  }, 120_000);

  afterAll(async () => {
    if (sidecar) await shutdown(sidecar);
    if (wrapperDir) fs.rmSync(wrapperDir, { recursive: true, force: true });
  });

  it("reports apiVersion 2.4 (the handshake already accepted it at startup)", async () => {
    const v = await sidecar.client.version();
    expect(v.apiVersion).toBe("2.4");
  });

  it("streams the canonical status -> tool_call -> tool_result -> final sequence", async () => {
    const runId = crypto.randomUUID();
    const events = await collect(
      sidecar.client.query({
        query: "Triage my inbox.",
        run_id: runId,
        context: [{ role: "user", content: "earlier turn" }],
      }),
    );

    expect(events.map((e) => e.type)).toEqual([
      "status",
      "tool_call",
      "tool_result",
      "final",
    ]);
    const call = events[1];
    if (call?.type !== "tool_call") throw new Error("expected tool_call");
    expect(call.tool).toBe("triage_inbox");
    expect(call.args).toEqual({ max_messages: 10 });
    const result = events[2];
    if (result?.type !== "tool_result") throw new Error("expected tool_result");
    expect(result.tool).toBe("triage_inbox");
    const final = events[3];
    if (final?.type !== "final") throw new Error("expected final");
    expect(final.answer).toBe("Triaged 5 emails.");
  }, 60_000);

  it("cancelQuery stops an in-flight run between steps", async () => {
    const runId = crypto.randomUUID();
    const events: QueryEvent[] = [];
    let cancelled = false;

    for await (const ev of sidecar.client.query({
      query: "Do a long task and wait between steps.",
      run_id: runId,
      context: [],
    })) {
      events.push(ev);
      if (!cancelled && ev.type === "tool_result") {
        // First step done, agent parked on the cancel event — cancel mid-run.
        const res = await sidecar.client.cancelQuery(runId);
        expect(res.cancelled).toBe(true);
        expect(res.run_id).toBe(runId);
        cancelled = true;
      }
    }

    expect(cancelled).toBe(true);
    const final = events[events.length - 1];
    if (final?.type !== "final") throw new Error("expected a terminal final");
    expect(final.answer).toBe("Stopped between steps.");
    // Only the first step's tool ran — steps 2 and 3 never started.
    const tools = events.filter((e) => e.type === "tool_call");
    expect(tools).toHaveLength(1);
  }, 60_000);

  it("rejects a /query without the per-session bearer (401)", async () => {
    const res = await fetch(`${sidecar.baseUrl}/v1/email/query`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify({
        query: "Triage my inbox.",
        run_id: crypto.randomUUID(),
        context: [],
      }),
    });
    expect(res.status).toBe(401);
  }, 30_000);

  it("cancel of a run that is not in flight is a loud 404", async () => {
    const err = await sidecar.client
      .cancelQuery(crypto.randomUUID())
      .catch((e) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
  }, 30_000);
});
