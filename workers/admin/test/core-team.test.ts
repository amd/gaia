// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Attacks on the core-team save: who may save, from where, and what a saved value can do to the page. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { memoryD1 } from "./d1";

const auth = vi.hoisted(() => ({ who: "maintainer@amd.com" as string | Error }));
vi.mock("../src/auth", async (orig) => {
  const real = await orig<typeof import("../src/auth")>();
  return {
    ...real,
    authenticate: async () => {
      if (auth.who instanceof Error) throw auth.who;
      return auth.who;
    },
  };
});

const { default: worker } = await import("../src/index");
const { AuthError } = await import("../src/auth");

const SITE = "https://amd-gaia.ai";
const env = (db: D1Database) => ({ DB: db, GITHUB_REPO: "amd/gaia", NPM_PACKAGE: "x", PYPI_PACKAGE: "y", ACCESS_TEAM_DOMAIN: "t", ACCESS_AUD: "a", MAINTAINER_LOGINS: "deploy-person" });
const save = (db: D1Database, body: string, headers: Record<string, string> = {}) =>
  worker.fetch(
    new Request(`${SITE}/admin/api/core-team`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-action": "core-team", origin: SITE, ...headers },
      body,
    }),
    env(db),
  );
const team = (maintainers: unknown) => JSON.stringify({ maintainers });
const page = async (db: D1Database) => (await worker.fetch(new Request(`${SITE}/admin/developers`), env(db))).text();
const unchanged = async (db: D1Database) => expect(await page(db)).toContain('var MINE=["deploy-person"],');

beforeEach(() => {
  auth.who = "maintainer@amd.com";
  vi.spyOn(console, "log").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("who may save the core team", () => {
  it("refuses a caller Access did not sign in, and stores nothing", async () => {
    const db = memoryD1();
    auth.who = new AuthError("Missing Cloudflare Access assertion.", 401);
    expect((await save(db, team("attacker"))).status).toBe(401);
    auth.who = "maintainer@amd.com";
    await unchanged(db);
  });

  it("refuses a service token, which names no person to record", async () => {
    const db = memoryD1();
    auth.who = "unknown";
    const res = await save(db, team("attacker"));
    expect(res.status).toBe(403);
    expect(await res.text()).toMatch(/service token/);
    auth.who = "maintainer@amd.com";
    await unchanged(db);
  });

  it("records the signed-in person and logs the change", async () => {
    const db = memoryD1();
    expect((await save(db, team("ann"))).status).toBe(200);
    expect(await page(db)).toContain('"by":"maintainer@amd.com"');
    expect(console.log).toHaveBeenCalledWith(expect.stringContaining('"by":"maintainer@amd.com"'));
  });
});

describe("cross-site saves", () => {
  it.each([
    ["another site", "https://evil.example"],
    ["a look-alike subdomain", "https://amd-gaia.ai.evil.example"],
    ["plain http on the real host", "http://amd-gaia.ai"],
    ["another port on the real host", "https://amd-gaia.ai:8443"],
    ["Origin: null", "null"],
  ])("refuses %s", async (_name, origin) => {
    const db = memoryD1();
    expect((await save(db, team("attacker"), { origin })).status).toBe(403);
    await unchanged(db);
  });

  it("refuses a simple form post, which cannot set the action header", async () => {
    const db = memoryD1();
    const res = await worker.fetch(
      new Request(`${SITE}/admin/api/core-team`, {
        method: "POST",
        headers: { "content-type": "text/plain", origin: SITE },
        body: team("attacker"),
      }),
      env(db),
    );
    expect(res.status).toBe(403);
    await unchanged(db);
  });
});

describe("what a save may contain", () => {
  it.each([
    ["markup", "ann,<img src=x onerror=alert(1)>"],
    ["a script close", "ann</script><script>alert(1)//"],
    ["a JSON breakout", 'ann","x":"'],
    ["a leading hyphen", "-ann"],
    ["a 40-character login", "a".repeat(40)],
    ["a look-alike Unicode letter", "аnn"],
    ["a space inside a login", "ann bob"],
    ["a $ replacement pattern", "$&"],
  ])("rejects %s and keeps the previous team", async (_name, value) => {
    const db = memoryD1();
    const res = await save(db, team(value));
    expect(res.status).toBe(400);
    expect(res.headers.get("content-type")).toMatch(/^text\/plain/);
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    await unchanged(db);
  });

  it.each([
    ["the same account twice", "ann,ann"],
    ["one account in two people", "ann+ann-work,bob+ann-work"],
    ["the same account in another case", "Ann,ann"],
  ])("rejects %s, naming it", async (_name, value) => {
    const db = memoryD1();
    const res = await save(db, team(value));
    expect(res.status).toBe(400);
    expect(await res.text()).toMatch(/more than once: ann/);
    await unchanged(db);
  });

  it.each([
    ["a number", team(42)],
    ["a list", team(["ann"])],
    ["null", team(null)],
    ["a JSON null body", "null"],
    ["a JSON string body", '"ann"'],
    ["no maintainers key", JSON.stringify({ nope: 1 })],
    ["broken JSON", "{"],
  ])("rejects %s as the body", async (_name, body) => {
    const db = memoryD1();
    expect((await save(db, body)).status).toBe(400);
    await unchanged(db);
  });

  it("refuses an oversized body, whether or not it declares its length", async () => {
    const db = memoryD1();
    const huge = team(Array.from({ length: 2000 }, (_, i) => `u${i}`).join(","));
    expect((await save(db, huge)).status).toBe(413);
    expect((await save(db, team("ann"), { "content-length": "999999" })).status).toBe(413);
    await unchanged(db);
  });

  it("keeps a signed-in name with markup inert in the page", async () => {
    const db = memoryD1();
    auth.who = 'x</script><script>alert(1)</script>"@evil.example';
    expect((await save(db, team("ann"))).status).toBe(200);
    const html = await page(db);
    const script = html.split("<script>").pop()!.split("</script>")[0]!;
    expect(script).toContain("var MINE=[\"ann\"],");
    expect(script).toContain("\\u003c/script>\\u003cscript>alert(1)");
    expect(() => new Function(script)).not.toThrow();
  });
});
