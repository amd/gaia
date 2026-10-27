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

describe("maintainer accounts", () => {
  it("are read from MAINTAINER_LOGINS and offered as a Hide maintainers button", async () => {
    const res = await worker.fetch(new Request("http://localhost:8787/admin/developers"), { ...env, MAINTAINER_LOGINS: "ann, bob-2" });
    expect(await res.text()).toContain('var MINE=["ann","bob-2"];');
  });

  it("default to none, so no personal account is baked into the page", async () => {
    const html = await (await worker.fetch(new Request("http://localhost:8787/admin/developers"), env)).text();
    expect(html).toContain("var MINE=[];");
  });

  it("refuse anything that is not a GitHub login (nothing can be injected into the page)", async () => {
    const { parseMaintainers } = await import("../src/page");
    expect(() => parseMaintainers('ann,"];alert(1)//')).toThrow(/not GitHub logins/);
    expect(parseMaintainers("dependabot[bot]")).toEqual(["dependabot[bot]"]);
  });
});

describe("collect origin check", () => {
  it("answers 403, not 500, for Origin: null", async () => {
    expect((await post({ "x-admin-action": "collect", origin: "null" })).status).toBe(403);
  });
});
