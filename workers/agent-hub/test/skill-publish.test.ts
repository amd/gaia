// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish/skill + the skills catalog lane (#2467).
 *
 * These assert the SHAPE of what lands in R2 and index.json, not merely that a
 * handler ran: a stub that returns 201 without writing a valid catalog entry
 * would pass an "it was called" test and still break every installer.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import type { CatalogIndex, IndexEntry, SkillManifest } from "../src/types";
import {
  allowAudit,
  makeEnv,
  publishRequest,
  sampleManifest,
  sampleSkill,
  skillPublishRequest,
} from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

async function publishSkill(env: Env, opts: Parameters<typeof skillPublishRequest>[0]) {
  return worker.fetch(skillPublishRequest(opts), env as never);
}

async function publishAgent(env: Env, opts: Parameters<typeof publishRequest>[0]) {
  return worker.fetch(publishRequest(opts), env as never);
}

async function readIndex(env: Env): Promise<CatalogIndex> {
  const res = await worker.fetch(
    new Request("https://hub.amd-gaia.ai/index.json"),
    env as never
  );
  expect(res.status).toBe(200);
  return (await res.json()) as CatalogIndex;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function errorCode(res: Response): Promise<string> {
  return ((await res.json()) as any).error.code;
}

const VALID = {
  skillMarkdown: sampleSkill(),
  artifact: "skill-bundle-bytes",
  filename: "web-research-0.1.0.zip",
  token: "tok_amd",
};

describe("POST /publish/skill — happy path", () => {
  it("stores the artifact, the raw SKILL.md, and a per-skill manifest under skills/", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, VALID);
    expect(res.status).toBe(201);

    expect(env.bucket.keys()).toEqual(
      expect.arrayContaining([
        "skills/web-research/0.1.0/web-research-0.1.0.zip",
        "skills/web-research/0.1.0/SKILL.md",
        "skills/web-research/manifest.json",
      ])
    );

    // The stored SKILL.md is the exact upload — the immutable record of what
    // was published, front matter included.
    const doc = await env.bucket.get("skills/web-research/0.1.0/SKILL.md");
    expect(await doc!.text()).toBe(sampleSkill());

    const manifest = (await (
      await env.bucket.get("skills/web-research/manifest.json")
    )!.json()) as SkillManifest;
    expect(manifest.name).toBe("web-research");
    expect(manifest.latest_version).toBe("0.1.0");
    expect(manifest.security_tier).toBe("experimental");
    expect(manifest.permissions).toEqual(["network:read:*.brave.com"]);
    expect(manifest.tools).toEqual([
      { name: "search_web", description: "Search the web for current information" },
    ]);
    expect(manifest.tools_required).toEqual(["query_documents"]);
    expect(manifest.requirements).toEqual({
      model: ">=7B",
      context: "",
      python: ">=3.10",
      dependencies: ["requests>=2.31"],
      node_dependencies: [],
      env_vars: ["BRAVE_API_KEY"],
      hardware: { npu: "optional", gpu_vram: "" },
    });
    // Provenance: publisher identity + the Worker-computed digest, exactly as
    // the agent lane records it.
    expect(manifest.author).toBe("AMD");
    const version = manifest.versions["0.1.0"];
    expect(version.publisher).toBe("AMD");
    expect(version.artifact.path).toBe("skills/web-research/0.1.0/web-research-0.1.0.zip");
    expect(version.artifact.size_bytes).toBe("skill-bundle-bytes".length);
    expect(version.artifact.sha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it("publishes an instruction-only skill (no metadata.gaia block)", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ omitGaia: true }),
    });
    expect(res.status).toBe(201);

    const entry = (await readIndex(env)).agents[0];
    // Off-state safe floor: experimental, no tools, no permissions.
    expect(entry.security_tier).toBe("experimental");
    expect(entry.permissions).toEqual([]);
    expect(entry.skill_metadata!.tools).toEqual([]);
    expect(entry.tools_count).toBe(0);
    expect(entry.language).toBe("markdown");
  });

  it("adds a second version and tracks it as latest", async () => {
    const env = makeEnv();
    expect((await publishSkill(env, VALID)).status).toBe(201);
    const second = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ version: "0.2.0", description: "Better web research" }),
      filename: "web-research-0.2.0.zip",
    });
    expect(second.status).toBe(201);

    const entry = (await readIndex(env)).agents.find((a) => a.id === "web-research")!;
    expect(entry.latest_version).toBe("0.2.0");
    expect(entry.description).toBe("Better web research");
  });

  it("rejects republishing an existing version (409, artifact untouched)", async () => {
    const env = makeEnv();
    expect((await publishSkill(env, VALID)).status).toBe(201);
    const again = await publishSkill(env, { ...VALID, artifact: "tampered" });
    expect(again.status).toBe(409);
    expect(await errorCode(again)).toBe("version_exists");

    const obj = await env.bucket.get("skills/web-research/0.1.0/web-research-0.1.0.zip");
    expect(await obj!.text()).toBe("skill-bundle-bytes");
  });

  it("blocks another publisher from updating a skill it does not own (403)", async () => {
    const env = makeEnv();
    expect((await publishSkill(env, VALID)).status).toBe(201);
    const hijack = await publishSkill(env, {
      ...VALID,
      token: "tok_indie",
      skillMarkdown: sampleSkill({ version: "0.2.0" }),
      filename: "web-research-0.2.0.zip",
    });
    expect(hijack.status).toBe(403);
    expect(await errorCode(hijack)).toBe("forbidden_scope");
  });

  it("requires authentication (401, nothing written)", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, { ...VALID, token: undefined });
    expect(res.status).toBe(401);
    expect(env.bucket.keys()).toEqual([]);
  });
});

describe("POST /publish/skill — SKILL.md grammar validation", () => {
  const cases: Array<[string, string]> = [
    ["no YAML front matter at all", "# Just a heading\n"],
    ["missing name", "---\ndescription: Something useful\nversion: 0.1.0\n---\n\nBody\n"],
    ["missing description", "---\nname: web-research\nversion: 0.1.0\n---\n\nBody\n"],
    [
      "empty description",
      '---\nname: web-research\ndescription: ""\nversion: 0.1.0\n---\n\nBody\n',
    ],
    ["front matter that is not a mapping", "---\n- a\n- b\n---\n\nBody\n"],
  ];

  it.each(cases)("rejects a SKILL.md with %s (400)", async (_label, markdown) => {
    const env = makeEnv();
    const res = await publishSkill(env, { ...VALID, skillMarkdown: markdown });
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_skill_manifest");
    expect(env.bucket.keys()).toEqual([]);
  });

  it.each([
    ["Web-Research", "uppercase"],
    ["web_research", "underscore"],
    ["-web", "leading hyphen"],
    ["web-", "trailing hyphen"],
    ["web--research", "consecutive hyphens"],
  ])("rejects the invalid skill name %s (%s)", async (name) => {
    const env = makeEnv();
    const res = await publishSkill(env, { ...VALID, skillMarkdown: sampleSkill({ name }) });
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_skill_manifest");
  });

  it("rejects a name over 64 characters", async () => {
    const env = makeEnv();
    const name = "a".repeat(65);
    const res = await publishSkill(env, { ...VALID, skillMarkdown: sampleSkill({ name }) });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { code: string; message: string } };
    expect(body.error.code).toBe("invalid_skill_manifest");
    expect(body.error.message).toContain("64-character limit");
  });

  it("accepts a name of exactly 64 characters", async () => {
    const env = makeEnv();
    const name = "a".repeat(64);
    const res = await publishSkill(env, { ...VALID, skillMarkdown: sampleSkill({ name }) });
    expect(res.status).toBe(201);
  });

  it("rejects a description over 1024 characters", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ description: `"${"x".repeat(1025)}"` }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { message: string } };
    expect(body.error.message).toContain("1024-character limit");
  });

  it("rejects a missing version (publishing requires one)", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ omitVersion: true }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { message: string } };
    expect(body.error.message).toContain("version is required to publish");
  });

  it.each(["1.0", "v1.0.0", "1.0.0.0", "latest"])(
    "rejects the non-SemVer version %s",
    async (version) => {
      const env = makeEnv();
      const res = await publishSkill(env, { ...VALID, skillMarkdown: sampleSkill({ version }) });
      expect(res.status).toBe(400);
      expect(await errorCode(res)).toBe("invalid_skill_manifest");
    }
  );

  it("rejects the reserved unversioned sentinel 0.0.0", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ version: "0.0.0" }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { message: string } };
    expect(body.error.message).toContain("cannot be published");
    expect(env.bucket.keys()).toEqual([]);
  });

  it.each(["verified", "community", "experimental"])(
    "accepts security_tier %s",
    async (tier) => {
      const env = makeEnv();
      const res = await publishSkill(env, {
        ...VALID,
        skillMarkdown: sampleSkill({ security_tier: tier }),
        // verified/community require a cleared audit (#2468).
        audit: tier === "experimental" ? undefined : allowAudit(),
      });
      expect(res.status).toBe(201);
    }
  );

  it("rejects an unknown security_tier", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ security_tier: "trusted" }),
    });
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_skill_manifest");
  });

  it("rejects a tools entry with no name", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "metadata:",
          "  gaia:",
          "    tools:",
          "      - description: nameless",
        ].join("\n"),
      }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { message: string } };
    expect(body.error.message).toContain("tools[0].name");
  });

  it("rejects permissions that are not a list of strings", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "metadata:",
          "  gaia:",
          "    permissions: network:read",
        ].join("\n"),
      }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { message: string } };
    expect(body.error.message).toContain("metadata.gaia.permissions");
  });

  it("rejects an empty artifact and a path-unsafe filename", async () => {
    const env = makeEnv();
    const empty = await publishSkill(env, { ...VALID, artifact: "" });
    expect(empty.status).toBe(400);
    expect(await errorCode(empty)).toBe("invalid_artifact");

    const traversal = await publishSkill(env, { ...VALID, filename: "../escape.zip" });
    expect(traversal.status).toBe(400);
    expect(await errorCode(traversal)).toBe("invalid_artifact");
  });

  it("rejects a request with no artifact part", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, { ...VALID, omitArtifact: true });
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_request");
  });
});

describe("POST /publish/skill — security-audit gate (#2468)", () => {
  it.each(["community", "verified"])(
    "refuses to publish tier %s with no audit report (428, nothing written)",
    async (tier) => {
      const env = makeEnv();
      const res = await publishSkill(env, {
        ...VALID,
        skillMarkdown: sampleSkill({ security_tier: tier }),
      });
      expect(res.status).toBe(428);
      expect(await errorCode(res)).toBe("audit_required");
      expect(env.bucket.keys()).toEqual([]);
    }
  );

  it("records `unaudited` for experimental, rather than stamping a fake ALLOW", async () => {
    const env = makeEnv();
    expect((await publishSkill(env, VALID)).status).toBe(201);
    const entry = (await readIndex(env)).agents[0];
    expect(entry.skill_metadata!.audit).toEqual({
      verdict: "unaudited",
      engine: "",
      audited_at: "",
      findings: 0,
    });
  });

  it("records the cleared verdict + engine when an ALLOW report is attached", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      skillMarkdown: sampleSkill({ security_tier: "verified" }),
      audit: allowAudit(2),
    });
    expect(res.status).toBe(201);

    const entry = (await readIndex(env)).agents[0];
    expect(entry.security_tier).toBe("verified");
    expect(entry.skill_metadata!.audit).toEqual({
      verdict: "ALLOW",
      engine: "gaia-skill-audit/0.1.0",
      audited_at: "2026-07-29T00:00:00.000Z",
      findings: 2,
    });
    // The report itself is kept as the per-version evidence of what was cleared.
    const stored = await env.bucket.get("skills/web-research/0.1.0/audit.json");
    expect(JSON.parse(await stored!.text()).verdict).toBe("ALLOW");
  });

  it("rejects a BLOCK verdict and writes nothing", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      audit: JSON.stringify({
        verdict: "BLOCK",
        engine: "gaia-skill-audit/0.1.0",
        audited_at: "2026-07-29T00:00:00.000Z",
        findings: [{ id: "shell-exec" }],
      }),
    });
    expect(res.status).toBe(403);
    expect(await errorCode(res)).toBe("audit_blocked");
    expect(env.bucket.keys()).toEqual([]);
  });

  it("holds a REVIEW verdict out of the catalog", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, {
      ...VALID,
      audit: JSON.stringify({
        verdict: "REVIEW",
        engine: "gaia-skill-audit/0.1.0",
        audited_at: "2026-07-29T00:00:00.000Z",
      }),
    });
    expect(res.status).toBe(409);
    expect(await errorCode(res)).toBe("audit_review_required");
    expect(env.bucket.keys()).toEqual([]);
  });

  it.each([
    ["not JSON", "not-json"],
    ["an unknown verdict", '{"verdict":"MAYBE","engine":"e","audited_at":"2026-07-29T00:00:00Z"}'],
    ["no engine attribution", '{"verdict":"ALLOW","audited_at":"2026-07-29T00:00:00Z"}'],
    ["no audited_at timestamp", '{"verdict":"ALLOW","engine":"gaia-skill-audit/0.1.0"}'],
  ])("rejects an audit report that is %s (400)", async (_label, audit) => {
    const env = makeEnv();
    const res = await publishSkill(env, { ...VALID, audit });
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_audit_report");
  });
});

describe("GET /index.json — Skills lane", () => {
  it("returns skills as filterable type: 'skill' entries alongside agents", async () => {
    const env = makeEnv();
    expect(
      (await publishAgent(env, { token: "tok_amd", manifestYaml: sampleManifest(), artifact: "w", filename: "chat-0.1.0.whl" }))
        .status
    ).toBe(201);
    expect((await publishSkill(env, VALID)).status).toBe(201);

    const index = await readIndex(env);
    expect(index.agents).toHaveLength(2);

    const skills = index.agents.filter((a) => a.type === "skill");
    const agents = index.agents.filter((a) => a.type !== "skill");
    expect(skills.map((s) => s.id)).toEqual(["web-research"]);
    expect(agents.map((a) => a.id)).toEqual(["chat"]);

    const skill = skills[0];
    expect(skill.name).toBe("web-research");
    expect(skill.category).toBe("skills");
    expect(skill.language).toBe("python"); // declares metadata.gaia.tools
    expect(skill.tools_count).toBe(1);
    expect(skill.permissions).toEqual(["network:read:*.brave.com"]);
    expect(skill.skill_metadata!.tools_required).toEqual(["query_documents"]);
    // The SKILL.md body renders as the entry's primary doc, front matter stripped.
    expect(skill.readme).toContain("# Web Research");
    expect(skill.readme).not.toContain("security_tier");
  });

  it("keeps every agent-lane field populated on a skill entry", async () => {
    const env = makeEnv();
    expect((await publishSkill(env, VALID)).status).toBe(201);
    const entry = (await readIndex(env)).agents[0];

    // A reader that touches these unconditionally must not hit undefined.
    expect(entry.requirements).toEqual({
      min_memory_gb: 0,
      min_disk_gb: 0,
      min_context_size: 0,
      platforms: [],
      npu: "optional",
      gpu_vram_gb: 0,
    });
    expect(entry.tags).toEqual([]);
    expect(entry.models).toEqual([]);
    expect(entry.min_gaia_version).toBe("");
    expect(entry.deprecated).toBe(false);
    expect(entry.changelog).toBe("");
    expect(entry.download_size_bytes).toBe("skill-bundle-bytes".length);
  });

  it("leaves skill_metadata absent on agent entries", async () => {
    const env = makeEnv();
    await publishAgent(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "w",
      filename: "chat-0.1.0.whl",
    });
    const entry = (await readIndex(env)).agents[0];
    expect(entry.type).toBe("agent");
    expect(entry.skill_metadata).toBeUndefined();
  });

  it("carries a published CHANGELOG into the skill entry", async () => {
    const env = makeEnv();
    await publishSkill(env, { ...VALID, changelog: "## 0.1.0\n\nFirst release.\n" });
    const entry = (await readIndex(env)).agents[0];
    expect(entry.changelog).toContain("First release.");
  });
});

describe("catalog backward compatibility", () => {
  /**
   * The pre-#2467 consumer: it reads `catalog.agents`, assumes every entry is an
   * installable agent, and knows nothing about `type`. Publishing skills must
   * not make it throw or mis-render — the guarantee the whole lane rests on.
   */
  interface OldShapeAgent {
    id: string;
    name: string;
    description: string;
    latest_version: string;
    security_tier: string;
    requirements: { platforms: string[]; min_memory_gb: number };
    permissions: string[];
    readme: string;
  }

  function oldConsumer(raw: unknown): OldShapeAgent[] {
    const catalog = raw as { schema_version: number; agents: OldShapeAgent[] };
    if (catalog.schema_version !== 1) throw new Error("unsupported catalog schema");
    if (!Array.isArray(catalog.agents)) throw new Error("no agents array");
    return catalog.agents.map((a) => ({
      id: a.id,
      name: a.name,
      description: a.description,
      latest_version: a.latest_version,
      security_tier: a.security_tier,
      // The field an old reader dereferences without checking `type`.
      requirements: {
        platforms: a.requirements.platforms,
        min_memory_gb: a.requirements.min_memory_gb,
      },
      permissions: a.permissions,
      readme: a.readme,
    }));
  }

  it("an old-shape consumer still parses a catalog containing skills", async () => {
    const env = makeEnv();
    await publishAgent(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "w",
      filename: "chat-0.1.0.whl",
      readme: "# Chat",
    });
    await publishSkill(env, VALID);

    const index = await readIndex(env);
    const parsed = oldConsumer(index);

    expect(parsed.map((p) => p.id).sort()).toEqual(["chat", "web-research"]);
    for (const entry of parsed) {
      expect(typeof entry.name).toBe("string");
      expect(entry.latest_version).toMatch(/^\d+\.\d+\.\d+/);
      expect(Array.isArray(entry.requirements.platforms)).toBe(true);
      expect(typeof entry.requirements.min_memory_gb).toBe("number");
      expect(Array.isArray(entry.permissions)).toBe(true);
      expect(typeof entry.readme).toBe("string");
    }
    // schema_version is unchanged: skills are a new lane, not a new schema.
    expect(index.schema_version).toBe(1);
    expect(Object.keys(index).sort()).toEqual(["agents", "generated_at", "schema_version"]);
  });
});

describe("published index.json matches schemas/index.schema.json", () => {
  // The schema declares additionalProperties: false, so a field the Worker emits
  // but the schema never declared would make the published catalog invalid
  // against its own contract. Checked structurally rather than with a full
  // validator so the Worker keeps its zero-runtime-dependency footprint.
  const schema = JSON.parse(
    readFileSync(new URL("../schemas/index.schema.json", import.meta.url), "utf8")
  ) as {
    definitions: {
      indexEntry: { required: string[]; properties: Record<string, unknown> };
    };
  };

  it.each([
    ["skill", async (env: Env) => publishSkill(env, VALID)],
    [
      "agent",
      async (env: Env) =>
        publishAgent(env, {
          token: "tok_amd",
          manifestYaml: sampleManifest(),
          artifact: "w",
          filename: "chat-0.1.0.whl",
          readme: "# Chat",
          changelog: "## 0.1.0",
        }),
    ],
  ])("declares every field a %s entry emits", async (_lane, publishOne) => {
    const env = makeEnv();
    expect((await publishOne(env)).status).toBe(201);
    const entry = (await readIndex(env)).agents[0] as unknown as Record<string, unknown>;

    const declared = new Set(Object.keys(schema.definitions.indexEntry.properties));
    const undeclared = Object.keys(entry).filter((k) => !declared.has(k));
    expect(undeclared).toEqual([]);

    const missing = schema.definitions.indexEntry.required.filter((k) => !(k in entry));
    expect(missing).toEqual([]);
  });
});

describe("cross-lane id namespace", () => {
  it("rejects a skill whose name collides with a published agent id (409)", async () => {
    const env = makeEnv();
    await publishAgent(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "web-research" }),
      artifact: "w",
      filename: "web-research-0.1.0.whl",
    });
    const res = await publishSkill(env, VALID);
    expect(res.status).toBe(409);
    expect(await errorCode(res)).toBe("id_conflict");
  });

  it("rejects an agent whose id collides with a published skill name (409)", async () => {
    const env = makeEnv();
    await publishSkill(env, VALID);
    const res = await publishAgent(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "web-research" }),
      artifact: "w",
      filename: "web-research-0.1.0.whl",
    });
    expect(res.status).toBe(409);
    expect(await errorCode(res)).toBe("id_conflict");
  });

  it("rejects a gaia-agent.yaml declaring type: skill, pointing at the skill route", async () => {
    const env = makeEnv();
    const res = await publishAgent(env, {
      token: "tok_amd",
      manifestYaml: `${sampleManifest()}type: skill\n`,
      artifact: "w",
      filename: "chat-0.1.0.whl",
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: { code: string; message: string } };
    expect(body.error.code).toBe("invalid_manifest");
    expect(body.error.message).toContain("/publish/skill");
  });
});

describe("skill object routes", () => {
  it("serves the per-skill manifest and the stored SKILL.md", async () => {
    const env = makeEnv();
    await publishSkill(env, VALID);

    const manifestRes = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/skills/web-research/manifest.json"),
      env as never
    );
    expect(manifestRes.status).toBe(200);
    expect(((await manifestRes.json()) as SkillManifest).name).toBe("web-research");

    const docRes = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/skills/web-research/0.1.0/SKILL.md"),
      env as never
    );
    expect(docRes.status).toBe(200);
    expect(await docRes.text()).toContain("name: web-research");
  });

  it("rejects percent-encoded path traversal under /skills/ (400)", async () => {
    const env = makeEnv();
    // %2f survives URL normalization, so the ".." only appears after the key is
    // decoded — exactly the case the object-key guard exists for.
    const res = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/skills/web-research/%2e%2e%2f%2e%2e%2findex.json"),
      env as never
    );
    expect(res.status).toBe(400);
    expect(await errorCode(res)).toBe("invalid_path");
  });

  it("rejects GET on /publish/skill (405)", async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/publish/skill"),
      env as never
    );
    expect(res.status).toBe(405);
  });
});

describe("POST /reindex — skills survive a rebuild", () => {
  it("re-derives skill entries from R2 without a re-publish", async () => {
    const env = makeEnv();
    (env as { REINDEX_TOKEN?: string }).REINDEX_TOKEN = "tok_reindex";
    await publishSkill(env, VALID);

    const before = (await readIndex(env)).agents.find((a) => a.id === "web-research") as IndexEntry;
    await env.bucket.delete("index.json");

    const res = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/reindex", {
        method: "POST",
        headers: { authorization: "Bearer tok_reindex" },
      }),
      env as never
    );
    expect(res.status).toBe(200);

    const after = (await readIndex(env)).agents.find((a) => a.id === "web-research") as IndexEntry;
    expect(after).toEqual(before);
  });
});
