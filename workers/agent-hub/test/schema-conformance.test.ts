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
import type {
  AgentManifest,
  ArtifactInfo,
  CatalogIndex,
  Channels,
  IndexEntry,
  IndexRequirements,
  Interfaces,
  PackageFile,
  PackageInfo,
  Requirements,
  SkillAuditRecord,
  SkillHardware,
  SkillMetadata,
  SkillRequirements,
  SkillToolDecl,
  VersionEntry,
} from "../src/types";
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

  it("with an agent that published an eval scorecard", async () => {
    const env = makeEnv();
    await publishAgent(env, {
      evalScorecard: [
        "---",
        "schema_version: 1",
        "agent:",
        "  name: Chat",
        "  version: 0.1.0",
        "aggregate:",
        "  name: weighted_accuracy",
        "  value: 87.5",
        "---",
        "# Chat — Eval Scorecard v0.1.0",
      ].join("\n"),
    });
    const index = (await storedJson(env, "index.json")) as {
      agents: Array<Record<string, unknown>>;
    };
    // Pin that every scorecard-derived field is really emitted and checked.
    expect(index.agents[0]).toMatchObject({
      eval_score: 87.5,
      eval_score_version: "0.1.0",
      eval_scorecard_url: expect.stringMatching(/SCORECARD\.md$/),
    });
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

// A fixture only proves the fields it happens to emit. These inventories list
// every key of each emitted type — `satisfies` fails typecheck when a type gains
// or loses a key, or a key's optionality changes — and the test below then fails
// until the schema declares exactly that key set.
type Inventory<T> = { [K in keyof T]-?: {} extends Pick<T, K> ? "optional" : "always" };

const INDEX_SHAPES: Array<[string, string[], Record<string, string>]> = [
  ["CatalogIndex", [], {
    schema_version: "always", generated_at: "always", agents: "always",
  } satisfies Inventory<CatalogIndex>],
  ["IndexEntry", ["definitions", "indexEntry"], {
    id: "always", name: "always", description: "always", category: "always",
    latest_version: "always", icon: "always", language: "always", type: "always",
    author: "always", security_tier: "always", download_size_bytes: "always",
    tags: "always", tools_count: "always", models: "always", min_gaia_version: "always",
    min_gaia_versions: "optional", channels: "optional", permissions: "always",
    deprecated: "always", deprecation_message: "optional", requirements: "always",
    readme: "always", changelog: "always", spec: "always", skill: "always",
    evaluation: "always", capability_matrix: "always", scorecard: "always",
    npm_package: "optional", playground_url: "optional", eval_scorecard_url: "optional",
    eval_score: "optional", eval_score_version: "optional", package: "optional",
    skill_metadata: "optional",
  } satisfies Inventory<IndexEntry>],
  ["IndexRequirements", ["definitions", "indexEntry", "properties", "requirements"], {
    min_memory_gb: "always", min_disk_gb: "always", min_context_size: "always",
    platforms: "always", npu: "always", gpu_vram_gb: "always",
  } satisfies Inventory<IndexRequirements>],
  ["Channels (index)", ["definitions", "indexEntry", "properties", "channels"], {
    stable: "always", next: "always",
  } satisfies Inventory<Channels>],
  ["PackageInfo", ["definitions", "indexEntry", "properties", "package"], {
    filename: "always", size_bytes: "always", files: "always",
  } satisfies Inventory<PackageInfo>],
  ["PackageFile", ["definitions", "indexEntry", "properties", "package", "properties", "files", "items"], {
    name: "always", size_bytes: "always",
  } satisfies Inventory<PackageFile>],
  ["SkillMetadata", ["definitions", "indexEntry", "properties", "skill_metadata"], {
    tools: "always", tools_required: "always", requirements: "always", audit: "always",
  } satisfies Inventory<SkillMetadata>],
  ["SkillToolDecl", ["definitions", "indexEntry", "properties", "skill_metadata", "properties", "tools", "items"], {
    name: "always", description: "always",
  } satisfies Inventory<SkillToolDecl>],
  ["SkillRequirements", ["definitions", "indexEntry", "properties", "skill_metadata", "properties", "requirements"], {
    model: "always", context: "always", python: "always", dependencies: "always",
    node_dependencies: "always", env_vars: "always", hardware: "always",
  } satisfies Inventory<SkillRequirements>],
  ["SkillHardware", ["definitions", "indexEntry", "properties", "skill_metadata", "properties", "requirements", "properties", "hardware"], {
    npu: "always", gpu_vram: "always",
  } satisfies Inventory<SkillHardware>],
  ["SkillAuditRecord", ["definitions", "indexEntry", "properties", "skill_metadata", "properties", "audit"], {
    verdict: "always", engine: "always", audited_at: "always", findings: "always",
    attestation: "optional", cleared_tiers: "optional", content_digest: "optional",
    manifest_digest: "optional",
  } satisfies Inventory<SkillAuditRecord>],
];

const MANIFEST_SHAPES: Array<[string, string[], Record<string, string>]> = [
  ["AgentManifest", [], {
    id: "always", name: "always", description: "always", author: "always",
    license: "always", language: "always", type: "always", category: "always",
    tags: "always", icon: "always", security_tier: "always", min_gaia_version: "optional",
    models: "always", tools_count: "always", permissions: "always", requirements: "always",
    interfaces: "always", latest_version: "always", deprecated: "always",
    deprecation_message: "optional", npm_package: "optional", playground_url: "optional",
    channels: "optional", versions: "always", next_versions: "optional",
  } satisfies Inventory<AgentManifest>],
  ["Requirements", ["definitions", "requirements"], {
    platforms: "always", min_memory_gb: "always", min_disk_gb: "always",
    min_context_size: "always", npu: "always", gpu_vram_gb: "always",
  } satisfies Inventory<Requirements>],
  ["Interfaces", ["definitions", "interfaces"], {
    tui: "optional", cli: "optional", pipe: "optional", api_server: "optional",
    mcp_server: "optional",
  } satisfies Inventory<Interfaces>],
  ["Channels (manifest)", ["properties", "channels"], {
    stable: "always", next: "always",
  } satisfies Inventory<Channels>],
  ["VersionEntry", ["definitions", "versionEntry"], {
    version: "always", published_at: "always", publisher: "always", deprecated: "always",
    min_gaia_version: "optional", artifact: "always", artifacts: "always",
  } satisfies Inventory<VersionEntry>],
  ["ArtifactInfo", ["definitions", "artifact"], {
    filename: "always", path: "always", size_bytes: "always", sha256: "always",
    content_type: "always",
  } satisfies Inventory<ArtifactInfo>],
];

interface SchemaNode {
  $ref?: string;
  properties?: Record<string, SchemaNode>;
  required?: string[];
  items?: SchemaNode;
  [k: string]: unknown;
}

function nodeAt(root: SchemaNode, path: string[]): SchemaNode {
  let node: SchemaNode = root;
  for (const key of path) {
    node = node[key] as SchemaNode;
    expect(node, `schema has no ${path.join(".")}`).toBeDefined();
    if (node.$ref) {
      node = nodeAt(root, node.$ref.replace(/^#\//, "").split("/"));
    }
  }
  return node;
}

describe.each([
  ["index.schema.json", INDEX_SHAPES],
  ["manifest.schema.json", MANIFEST_SHAPES],
])("%s declares exactly the keys the Worker's types emit", (file, shapes) => {
  const root = loadSchema(file) as SchemaNode;

  it.each(shapes)("%s", (_type, path, inventory) => {
    const node = nodeAt(root, path);
    const declared = Object.keys(node.properties ?? {}).sort();
    expect(declared).toEqual(Object.keys(inventory).sort());
    // A key the schema requires but the Worker may omit would fail validation.
    const optional = (node.required ?? []).filter((k) => inventory[k] !== "always");
    expect(optional).toEqual([]);
  });
});
