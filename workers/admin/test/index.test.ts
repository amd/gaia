// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import worker from "../src/index";

const env = {
  DB: {} as D1Database,
  GITHUB_REPO: "amd/gaia",
  NPM_PACKAGE: "x",
  PYPI_PACKAGE: "y",
  ACCESS_TEAM_DOMAIN: "",
  ACCESS_AUD: "",
  ADMIN_DEV_NO_AUTH: "1",
};
const post = (headers: Record<string, string>) =>
  worker.fetch(new Request("http://localhost:8787/admin/api/collect?days=1", { method: "POST", headers }), env);

describe("POST /admin/api/collect", () => {
  it("refuses a request without the explicit action header (a cross-site form cannot set it)", async () => {
    expect((await post({})).status).toBe(403);
  });

  it("refuses a request from another site even with the header", async () => {
    expect((await post({ "x-admin-action": "collect", origin: "https://evil.example" })).status).toBe(403);
  });

  it("rejects a bad days value before collecting anything", async () => {
    const res = await worker.fetch(
      new Request("http://localhost:8787/admin/api/collect?days=999", { method: "POST", headers: { "x-admin-action": "collect" } }),
      env,
    );
    expect(res.status).toBe(400);
  });
});

describe("routing", () => {
  it("answers 404 outside /admin", async () => {
    expect((await worker.fetch(new Request("http://localhost:8787/other"), env)).status).toBe(404);
  });
});
