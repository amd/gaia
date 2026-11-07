// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Publish is all-or-nothing and catalog writes don't lose each other (#3538).
 *
 * A publish that fails partway must not leave a version the catalog can't see
 * but a retry reports as "already published", and two publishes that interleave
 * must both end up in manifest.json and index.json.
 */

import { describe, expect, it } from "vitest";

import { rebuildIndex } from "../src/catalog";
import worker from "../src/index";
import type { AgentManifest, CatalogIndex, SkillManifest } from "../src/types";
import {
  makeEnv,
  publishRequest,
  sampleManifest,
  sampleSkill,
  skillPublishRequest,
} from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

const CHAT = {
  token: "tok_amd",
  manifestYaml: sampleManifest(),
  artifact: "wheel-bytes",
  filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
};

async function publish(env: Env, opts: Parameters<typeof publishRequest>[0]) {
  return worker.fetch(publishRequest(opts), env as never);
}

async function readJson<T>(env: Env, key: string): Promise<T | null> {
  const obj = await env.bucket.get(key);
  return obj ? ((await obj.json()) as T) : null;
}

async function indexIds(env: Env): Promise<string[]> {
  const index = await readJson<CatalogIndex>(env, "index.json");
  return (index?.agents ?? []).map((a) => a.id).sort();
}

/** Fail the first put to `key`, then let every later write through. */
function failOncePut(env: Env, key: string): void {
  let failed = false;
  env.bucket.beforePut = (k) => {
    if (k === key && !failed) {
      failed = true;
      throw new Error(`simulated R2 outage writing ${k}`);
    }
  };
}

describe("publish — a failed publish leaves nothing half-visible", () => {
  it("a retry after the manifest write failed publishes instead of a false 409", async () => {
    const env = makeEnv();
    failOncePut(env, "agents/chat/manifest.json");
    const first = await publish(env, CHAT);
    expect(first.status).toBe(500);
    // The artifact landed, but nothing in the catalog points at it.
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl");
    expect(await readJson(env, "agents/chat/manifest.json")).toBeNull();
    expect(await indexIds(env)).toEqual([]);

    const retry = await publish(env, CHAT);
    expect(retry.status).toBe(201);
    const manifest = await readJson<AgentManifest>(env, "agents/chat/manifest.json");
    expect(manifest!.versions["0.1.0"].artifacts.map((a) => a.filename)).toEqual([CHAT.filename]);
    expect(await indexIds(env)).toEqual(["chat"]);
  });

  it("a 409 for an indexed artifact leaves index.json untouched", async () => {
    const env = makeEnv();
    expect((await publish(env, CHAT)).status).toBe(201);
    const before = await (await env.bucket.get("index.json"))!.text();
    expect((await publish(env, CHAT)).status).toBe(409);
    expect(await (await env.bucket.get("index.json"))!.text()).toBe(before);
  });

  it("a retry after the index write failed reports 409 and repairs the index", async () => {
    const env = makeEnv();
    failOncePut(env, "index.json");
    expect((await publish(env, CHAT)).status).toBe(500);
    // The manifest committed, so the version is published — only the index lags.
    expect(await readJson(env, "agents/chat/manifest.json")).not.toBeNull();
    expect(await indexIds(env)).toEqual([]);

    const retry = await publish(env, CHAT);
    expect(retry.status).toBe(409);
    expect(((await retry.json()) as any).error.code).toBe("version_exists");
    expect(await indexIds(env)).toEqual(["chat"]);
  });

  it("an orphaned artifact with different bytes is replaced, not served", async () => {
    const env = makeEnv();
    failOncePut(env, "agents/chat/manifest.json");
    expect((await publish(env, { ...CHAT, artifact: "broken-build" })).status).toBe(500);

    expect((await publish(env, CHAT)).status).toBe(201);
    const obj = await env.bucket.get("agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl");
    expect(await obj!.text()).toBe("wheel-bytes");
  });

  it("a skill retry after the manifest write failed publishes instead of a false 409", async () => {
    const env = makeEnv();
    const skill = {
      token: "tok_amd",
      skillMarkdown: sampleSkill(),
      artifact: "skill-bundle-bytes",
      filename: "web-research-0.1.0.zip",
    };
    failOncePut(env, "skills/web-research/manifest.json");
    expect((await worker.fetch(skillPublishRequest(skill), env as never)).status).toBe(500);

    const retry = await worker.fetch(skillPublishRequest(skill), env as never);
    expect(retry.status).toBe(201);
    const manifest = await readJson<SkillManifest>(env, "skills/web-research/manifest.json");
    expect(Object.keys(manifest!.versions)).toEqual(["0.1.0"]);
    expect(await indexIds(env)).toEqual(["web-research"]);
  });
});

describe("catalog writes — concurrent writers don't drop each other", () => {
  it("an index write racing another publish keeps both agents", async () => {
    const env = makeEnv();
    let raced = false;
    env.bucket.beforePut = async (key) => {
      if (key !== "index.json" || raced) return;
      raced = true;
      // Another release job publishes `email` start to finish between this
      // publish deriving the index and writing it.
      expect(
        (
          await publish(env, {
            ...CHAT,
            manifestYaml: sampleManifest({ id: "email", name: "Email" }),
            filename: "gaia_agent_email-0.1.0-py3-none-any.whl",
          })
        ).status
      ).toBe(201);
    };
    expect((await publish(env, CHAT)).status).toBe(201);
    expect(await indexIds(env)).toEqual(["chat", "email"]);
  });

  it("a manifest write racing another version of the same agent keeps both versions", async () => {
    const env = makeEnv();
    let raced = false;
    env.bucket.beforePut = async (key) => {
      if (key !== "agents/chat/manifest.json" || raced) return;
      raced = true;
      expect(
        (
          await publish(env, {
            ...CHAT,
            manifestYaml: sampleManifest({ version: "0.2.0" }),
            filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
          })
        ).status
      ).toBe(201);
    };
    expect((await publish(env, CHAT)).status).toBe(201);
    const manifest = await readJson<AgentManifest>(env, "agents/chat/manifest.json");
    expect(Object.keys(manifest!.versions).sort()).toEqual(["0.1.0", "0.2.0"]);
    expect(manifest!.latest_version).toBe("0.2.0");
  });

  it("a racing publish of the same artifact loses with 409, not a silent overwrite", async () => {
    const env = makeEnv();
    let raced = false;
    env.bucket.beforePut = async (key) => {
      if (key !== "agents/chat/manifest.json" || raced) return;
      raced = true;
      expect((await publish(env, CHAT)).status).toBe(201);
    };
    const res = await publish(env, CHAT);
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error.code).toBe("version_exists");
    const manifest = await readJson<AgentManifest>(env, "agents/chat/manifest.json");
    expect(manifest!.versions["0.1.0"].artifacts).toHaveLength(1);
  });

  it("a racing publish of the same filename with different bytes fails loudly", async () => {
    const env = makeEnv();
    let raced = false;
    env.bucket.beforePut = async (key) => {
      if (key !== "agents/chat/manifest.json" || raced) return;
      raced = true;
      expect((await publish(env, { ...CHAT, artifact: "other-build" })).status).toBe(201);
    };
    const res = await publish(env, CHAT);
    expect(res.status).toBe(409);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("concurrent_publish_conflict");
    expect(body.error.message).toMatch(/new version/);
    const manifest = await readJson<AgentManifest>(env, "agents/chat/manifest.json");
    expect(manifest!.versions["0.1.0"].artifacts).toHaveLength(1);
  });

  it("the maintainer reindex is conditional too", async () => {
    const env = makeEnv();
    expect((await publish(env, CHAT)).status).toBe(201);
    // Reindex recovering a lost index.json while a release publishes.
    await env.bucket.delete("index.json");
    let raced = false;
    env.bucket.beforePut = async (key) => {
      if (key !== "index.json" || raced) return;
      raced = true;
      expect(
        (
          await publish(env, {
            ...CHAT,
            manifestYaml: sampleManifest({ id: "email", name: "Email" }),
            filename: "gaia_agent_email-0.1.0-py3-none-any.whl",
          })
        ).status
      ).toBe(201);
    };
    const index = await rebuildIndex(env.bucket as never);
    expect(index.agents.map((a) => a.id).sort()).toEqual(["chat", "email"]);
    expect(await indexIds(env)).toEqual(["chat", "email"]);
  });

  it("gives up with an actionable 503 when the catalog never settles", async () => {
    const env = makeEnv();
    let n = 0;
    env.bucket.beforePut = async (key) => {
      if (key !== "index.json") return;
      // A writer that lands before every attempt: the precondition never holds.
      const saved = env.bucket.beforePut;
      env.bucket.beforePut = undefined;
      await env.bucket.put("index.json", JSON.stringify({ churn: ++n }));
      env.bucket.beforePut = saved;
    };
    const res = await publish(env, CHAT);
    expect(res.status).toBe(503);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("catalog_conflict");
    expect(body.error.message).toMatch(/retry/i);
  });
});
