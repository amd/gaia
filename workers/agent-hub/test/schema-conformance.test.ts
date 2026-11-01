// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The published schemas are the contract the website builds from, and both
// declare additionalProperties: false — so anything the Worker emits that a
// schema does not declare makes the live catalog invalid against its own
// contract (#3542). These tests publish real packages through the Worker and run
// a full JSON-Schema validator over the stored manifest.json and index.json, so
// nested drift (an audit field, a new language) fails here, not on the hub.

import { readFileSync } from "node:fs";

import Ajv from "ajv";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import worker from "../src/index";
import {
  allowAudit,
  makeEnv,
  publishRequest,
  sampleManifest,
  sampleSkill,
  skillPublishRequest,
} from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

function loadSchema(name: string): object {
  return JSON.parse(readFileSync(new URL(`../schemas/${name}`, import.meta.url), "utf8"));
}

const ajv = new Ajv({ allErrors: true, strict: true });
addFormats(ajv);
const validateManifest = ajv.compile(loadSchema("manifest.schema.json"));
const validateIndex = ajv.compile(loadSchema("index.schema.json"));

function expectValid(validate: ReturnType<typeof ajv.compile>, doc: unknown): void {
  const ok = validate(doc);
  expect(ajv.errorsText(validate.errors, { separator: "\n" })).toBe("No errors");
  expect(ok).toBe(true);
}

async function publishAgent(env: Env, opts: Partial<Parameters<typeof publishRequest>[0]> = {}) {
  const res = await worker.fetch(
    publishRequest({
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "chat-0.1.0.whl",
      ...opts,
    }),
    env as never
  );
  expect(res.status).toBe(201);
}

async function publishSkill(env: Env, opts: Partial<Parameters<typeof skillPublishRequest>[0]>) {
  const res = await worker.fetch(
    skillPublishRequest({
      token: "tok_amd",
      skillMarkdown: sampleSkill(),
      artifact: "bundle",
      filename: "web-research-0.1.0.zip",
      ...opts,
    }),
    env as never
  );
  expect(res.status).toBe(201);
}

async function storedJson(env: Env, key: string): Promise<unknown> {
  const obj = await env.bucket.get(key);
  expect(obj, `${key} was not stored`).not.toBeNull();
  return obj!.json();
}

describe("manifest.json validates against schemas/manifest.schema.json", () => {
  it.each([
    ["a python agent", sampleManifest()],
    ["a go app", sampleManifest({ language: "go" }) + "type: app\n"],
    ["a typescript app", sampleManifest({ language: "typescript" }) + "type: app\n"],
    ["a cpp component", sampleManifest({ language: "cpp" }) + "type: component\n"],
    [
      "a deprecated agent with npm + playground",
      sampleManifest() +
        [
          "deprecated: true",
          'deprecation_message: "Use gaia instead"',
          "npm_package: '@amd-gaia/agent-chat'",
          "playground_url: http://localhost:4200/",
          "",
        ].join("\n"),
    ],
  ])("%s", async (_label, manifestYaml) => {
    const env = makeEnv();
    await publishAgent(env, { manifestYaml });
    expectValid(validateManifest, await storedJson(env, "agents/chat/manifest.json"));
  });

  it("a multi-artifact version plus a next-channel release candidate", async () => {
    const env = makeEnv();
    await publishAgent(env, { filename: "chat-win-x64.exe" });
    await publishAgent(env, { filename: "chat-linux-x64" });
    await publishAgent(env, {
      manifestYaml: sampleManifest({ version: "0.2.0-rc.1" }),
      filename: "chat-0.2.0rc1.whl",
      channel: "next",
    });
    const manifest = (await storedJson(env, "agents/chat/manifest.json")) as Record<string, unknown>;
    expect(manifest.next_versions).toBeDefined();
    expect(manifest.channels).toBeDefined();
    expectValid(validateManifest, manifest);
  });

  // The schema must still reject what publish rejects, or it is no contract.
  it.each([
    ["an unknown language", { language: "rust" }],
    ["the skill lane's type", { type: "skill" }],
    ["an undeclared field", { surprise: true }],
  ])("rejects %s", async (_label, patch) => {
    const env = makeEnv();
    await publishAgent(env);
    const manifest = (await storedJson(env, "agents/chat/manifest.json")) as Record<string, unknown>;
    expect(validateManifest({ ...manifest, ...patch })).toBe(false);
  });

  it("accepts a manifest stored before `type` was recorded", async () => {
    const env = makeEnv();
    await publishAgent(env);
    const { type: _dropped, ...legacy } = (await storedJson(
      env,
      "agents/chat/manifest.json"
    )) as Record<string, unknown>;
    expectValid(validateManifest, legacy);
  });
});

describe("index.json validates against schemas/index.schema.json", () => {
  it("with an agent, an app, an audited skill and an unaudited skill", async () => {
    const env = makeEnv();
    await publishAgent(env, {
      readme: "# Chat",
      changelog: "## 0.1.0",
      packageFiles: JSON.stringify({ files: [{ name: "chat.exe", size_bytes: 3 }] }),
      filename: "chat-0.1.0.zip",
    });
    await publishAgent(env, {
      manifestYaml: sampleManifest({ id: "tui", language: "go" }) + "type: app\n",
      filename: "tui-linux-x64",
    });

    const verified = sampleSkill({ name: "audited-skill", security_tier: "verified" });
    await publishSkill(env, {
      skillMarkdown: verified,
      filename: "audited-skill-0.1.0.zip",
      audit: await allowAudit(verified),
    });
    await publishSkill(env, {});

    const index = (await storedJson(env, "index.json")) as {
      agents: Array<{ type: string; skill_metadata?: { audit: Record<string, unknown> } }>;
    };
    const audits = index.agents
      .filter((e) => e.type === "skill")
      .map((e) => e.skill_metadata!.audit);
    // Pin that the nested fields that drifted are really emitted and checked.
    expect(audits.map((a) => a.attestation).sort()).toEqual(["publisher-asserted", "unaudited"]);
    for (const audit of audits) {
      expect(Object.keys(audit)).toEqual(
        expect.arrayContaining(["cleared_tiers", "content_digest", "manifest_digest"])
      );
    }
    expectValid(validateIndex, index);
  });

  it("rejects an undeclared field nested in skill_metadata.audit", async () => {
    const env = makeEnv();
    await publishSkill(env, {});
    const index = (await storedJson(env, "index.json")) as {
      agents: Array<{ skill_metadata: { audit: Record<string, unknown> } }>;
    };
    index.agents[0].skill_metadata.audit.signer = "nobody";
    expect(validateIndex(index)).toBe(false);
  });
});
