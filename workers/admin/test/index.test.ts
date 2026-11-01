// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { memoryD1 } from "./d1";

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
    const res = await worker.fetch(new Request("http://localhost:8787/admin/developers"), { ...env, DB: memoryD1(), MAINTAINER_LOGINS: "ann, bob-2" });
    expect(await res.text()).toContain('var MINE=["ann","bob-2"],');
  });

  it("default to none, so no personal account is baked into the page", async () => {
    const html = await (await worker.fetch(new Request("http://localhost:8787/admin/developers"), { ...env, DB: memoryD1() })).text();
    expect(html).toContain("var MINE=[],");
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

describe("collect source selection", () => {
  const collect = (q: string) =>
    worker.fetch(new Request(`http://localhost:8787/admin/api/collect?days=1&${q}`, { method: "POST", headers: { "x-admin-action": "collect" } }), env);

  it("answers 400, not 502, for an unknown only= source, naming the known ones", async () => {
    const res = await collect("only=evals,nope");
    expect(res.status).toBe(400);
    const body = await res.text();
    expect(body).toContain("nope");
    expect(body).toContain("evals");
    expect(body).not.toContain("finished with errors");
  });

  it("rejects a source with the wrong case or spelling", async () => {
    expect((await collect("only=Evals")).status).toBe(400);
    expect((await collect("only=code-stats")).status).toBe(400);
  });
});

describe("parseMaintainerGroups", () => {
  it("reads one person per entry, + joining their accounts, and rejects anything that is not a login", async () => {
    const { parseMaintainerGroups, parseMaintainers } = await import("../src/page");
    expect(parseMaintainerGroups("alice+alice-work, bob")).toEqual([["alice", "alice-work"], ["bob"]]);
    expect(parseMaintainers("alice+alice-work,bob")).toEqual(["alice", "alice-work", "bob"]);
    expect(() => parseMaintainerGroups("alice+<script>")).toThrow(/not GitHub logins/);
  });
});

describe("core team set from the dashboard", () => {
  const save = (db: D1Database, body: unknown, headers: Record<string, string> = { "x-admin-action": "core-team" }) =>
    worker.fetch(
      new Request("http://localhost:8787/admin/api/core-team", { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) }),
      { ...env, DB: db, MAINTAINER_LOGINS: "deploy-person" },
    );
  const page = async (db: D1Database) =>
    (await worker.fetch(new Request("http://localhost:8787/admin/developers"), { ...env, DB: db, MAINTAINER_LOGINS: "deploy-person" })).text();

  it("uses the deploy value until a team is saved, then the saved one, recording who saved it", async () => {
    const db = memoryD1();
    expect(await page(db)).toContain('var MINE=["deploy-person"],');
    const res = await save(db, { maintainers: " ann + ann-work , bob " });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ maintainers: "ann+ann-work,bob" });
    const html = await page(db);
    expect(html).toContain('var MINE=["ann","ann-work","bob"],');
    expect(html).toContain('"source":"dashboard"');
    expect(html).toContain('"by":"dev@localhost"');
  });

  it("refuses a save without the action header or from another site", async () => {
    const db = memoryD1();
    expect((await save(db, { maintainers: "ann" }, {})).status).toBe(403);
    expect((await save(db, { maintainers: "ann" }, { "x-admin-action": "core-team", origin: "https://evil.example" })).status).toBe(403);
    expect((await save(db, { maintainers: "ann" }, { "x-admin-action": "collect" })).status).toBe(403);
    expect(await page(db)).toContain('var MINE=["deploy-person"],');
  });

  it("accepts a save from the page itself, which sends its own Origin", async () => {
    const db = memoryD1();
    expect((await save(db, { maintainers: "ann" }, { "x-admin-action": "core-team", origin: "http://localhost:8787" })).status).toBe(200);
    expect((await save(db, { maintainers: "ann" }, { "x-admin-action": "core-team", origin: "http://localhost" })).status).toBe(200);
  });

  it("rejects anything that is not a GitHub login and keeps the previous team", async () => {
    const db = memoryD1();
    await save(db, { maintainers: "ann" });
    const res = await save(db, { maintainers: 'ann,"];alert(1)//' });
    expect(res.status).toBe(400);
    expect(await res.text()).toMatch(/not GitHub logins/);
    expect((await save(db, { nope: 1 })).status).toBe(400);
    expect(await page(db)).toContain('var MINE=["ann"],');
  });

  it("is what the Growth page uses too", async () => {
    const db = memoryD1();
    await save(db, { maintainers: "ann+ann-work,bob" });
    const html = await (await worker.fetch(new Request("http://localhost:8787/admin/growth"), { ...env, DB: db, MAINTAINER_LOGINS: "deploy-person" })).text();
    expect(html).toContain('CORE="ann+ann-work,bob"');
  });

  it("refuses Origin: null and names the core team, not the secret, in a validation error", async () => {
    const db = memoryD1();
    expect((await save(db, { maintainers: "ann" }, { "x-admin-action": "core-team", origin: "null" })).status).toBe(403);
    expect(await (await save(db, { maintainers: "ann,<x>" })).text()).toMatch(/^The core team has entries that are not GitHub logins: <x>/);
  });

  it("lets an empty list clear the team, so nobody is counted as core", async () => {
    const db = memoryD1();
    expect((await save(db, { maintainers: "" })).status).toBe(200);
    expect(await page(db)).toContain("var MINE=[],");
  });
});

describe("collect with an empty source list", () => {
  it("answers 400 instead of collecting nothing and reporting ok", async () => {
    const res = await worker.fetch(new Request("http://localhost:8787/admin/api/collect?days=1&only=,", { method: "POST", headers: { "x-admin-action": "collect" } }), env);
    expect(res.status).toBe(400);
    expect(await res.text()).toMatch(/names no source/);
  });
});
