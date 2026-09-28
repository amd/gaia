// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /reindex — auth failure paths (#4253).
 *
 * skill-publish.test.ts already covers the success path (a rebuild that
 * survives a deleted index.json). This file covers the two ways a caller
 * without the secret can hit the route: a missing/wrong token, and the
 * secret never having been provisioned at all.
 */

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import { makeEnv } from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

function reindexRequest(token?: string) {
  const headers: Record<string, string> = {};
  if (token !== undefined) {
    headers.authorization = `Bearer ${token}`;
  }
  return new Request("https://hub.amd-gaia.ai/reindex", {
    method: "POST",
    headers,
  });
}

describe("POST /reindex — authentication", () => {
  it("rejects a request with no Authorization header (401)", async () => {
    const env = makeEnv();
    (env as { REINDEX_TOKEN?: string }).REINDEX_TOKEN = "tok_reindex";

    const res = await worker.fetch(reindexRequest(), env as never);

    expect(res.status).toBe(401);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("unauthorized");
  });

  it("rejects an unknown token (401)", async () => {
    const env = makeEnv();
    (env as { REINDEX_TOKEN?: string }).REINDEX_TOKEN = "tok_reindex";

    const res = await worker.fetch(reindexRequest("tok_not_real"), env as never);

    expect(res.status).toBe(401);
  });

  it("returns 500 when REINDEX_TOKEN is unset (fail loudly, not allow-all)", async () => {
    const env = makeEnv() as Env & { REINDEX_TOKEN?: string };
    delete env.REINDEX_TOKEN;

    const res = await worker.fetch(reindexRequest("anything"), env as never);

    expect(res.status).toBe(500);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("config_error");
  });
});
