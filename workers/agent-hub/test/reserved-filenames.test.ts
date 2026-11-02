// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// An artifact may not be named after a per-version doc of its lane. Artifacts
// and docs share one key space (`<lane>/<id>/<version>/<name>`), and the docs
// are written right after the artifact — so `README.md` as an artifact would be
// replaced by the README after its SHA-256 was recorded, and every install of
// that release would then fail its checksum (#3541).

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import { AGENT_RESERVED_FILENAMES, SKILL_RESERVED_FILENAMES } from "../src/storage";
import type { AgentManifest } from "../src/types";
import {
  makeEnv,
  publishByRefRequest,
  publishRequest,
  sampleManifest,
  sampleSkill,
  sha256Of,
  skillPublishRequest,
} from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function error(res: Response): Promise<{ code: string; message: string }> {
  return ((await res.json()) as any).error;
}

const AGENT_DOCS = [
  "gaia-agent.yaml",
  "README.md",
  "CHANGELOG.md",
  "SPEC.md",
  "SKILL.md",
  "EVALUATION.md",
  "CAPABILITY_MATRIX.md",
  "SCORECARD.md",
  "package-files.json",
];
const SKILL_DOCS = ["SKILL.md", "CHANGELOG.md", "audit.json"];

describe("reserved artifact filenames — the sets match the doc keys", () => {
  it("covers every per-version doc the agent lane writes", () => {
    expect([...AGENT_RESERVED_FILENAMES].sort()).toEqual([...AGENT_DOCS].sort());
  });

  it("covers every per-version doc the skills lane writes", () => {
    expect([...SKILL_RESERVED_FILENAMES].sort()).toEqual([...SKILL_DOCS].sort());
  });
});

describe("POST /publish — reserved artifact filenames", () => {
  it.each(AGENT_DOCS)("rejects an artifact named %s and stores nothing", async (name) => {
    const env = makeEnv();
    const res = await worker.fetch(
      publishRequest({
        token: "tok_amd",
        manifestYaml: sampleManifest(),
        artifact: "artifact-bytes",
        filename: name,
        readme: "# Chat",
      }),
      env as never
    );
    expect(res.status).toBe(400);
    const err = await error(res);
    expect(err.code).toBe("invalid_artifact");
    expect(err.message).toContain("reserved");
    expect(env.bucket.keys().filter((k) => k.startsWith("agents/"))).toEqual([]);
  });

  it("rejects a reserved name on a by-reference publish too", async () => {
    const env = makeEnv();
    const bytes = new TextEncoder().encode("staged");
    const sha = await sha256Of(bytes);
    await env.BUCKET.put("agents/chat/0.1.0/README.md", bytes, { sha256: sha });
    const res = await worker.fetch(
      publishByRefRequest({
        manifestYaml: sampleManifest(),
        filename: "README.md",
        sha256: sha,
        size: bytes.byteLength,
      }),
      env as never
    );
    expect(res.status).toBe(400);
    expect((await error(res)).code).toBe("invalid_artifact");
    expect(await env.bucket.get("agents/chat/manifest.json")).toBeNull();
  });

  it("rejects a reserved name joining an already-published version", async () => {
    const env = makeEnv();
    const first = await worker.fetch(
      publishRequest({
        token: "tok_amd",
        manifestYaml: sampleManifest(),
        artifact: "wheel",
        filename: "chat-0.1.0.whl",
        readme: "# Chat",
      }),
      env as never
    );
    expect(first.status).toBe(201);

    const second = await worker.fetch(
      publishRequest({
        token: "tok_amd",
        manifestYaml: sampleManifest(),
        artifact: "not a readme",
        filename: "README.md",
      }),
      env as never
    );
    expect(second.status).toBe(400);
    expect(await (await env.bucket.get("agents/chat/0.1.0/README.md"))!.text()).toBe("# Chat");
  });

  // R2 keys are case-sensitive, so only the exact doc names collide.
  it.each(["readme.md", "README.md.whl", "gaia-agent.yaml.zip", "SKILL.MD"])(
    "accepts the near-miss %s and keeps its recorded checksum true",
    async (name) => {
      const env = makeEnv();
      const res = await worker.fetch(
        publishRequest({
          token: "tok_amd",
          manifestYaml: sampleManifest(),
          artifact: "artifact-bytes",
          filename: name,
          readme: "# Chat",
          skill: "# Skill",
        }),
        env as never
      );
      expect(res.status).toBe(201);
      const manifest = (await (await env.bucket.get("agents/chat/manifest.json"))!.json()) as AgentManifest;
      const recorded = manifest.versions["0.1.0"].artifact;
      const stored = await (await env.bucket.get(recorded.path))!.arrayBuffer();
      expect(recorded.sha256).toBe(await sha256Of(new Uint8Array(stored)));
    }
  );
});

describe("POST /publish/skill — reserved artifact filenames", () => {
  async function publishSkill(env: Env, filename: string) {
    return worker.fetch(
      skillPublishRequest({
        skillMarkdown: sampleSkill(),
        artifact: "skill-bundle-bytes",
        filename,
        token: "tok_amd",
      }),
      env as never
    );
  }

  it.each(SKILL_DOCS)("rejects an artifact named %s and stores nothing", async (name) => {
    const env = makeEnv();
    const res = await publishSkill(env, name);
    expect(res.status).toBe(400);
    const err = await error(res);
    expect(err.code).toBe("invalid_artifact");
    expect(err.message).toContain("reserved");
    expect(env.bucket.keys().filter((k) => k.startsWith("skills/"))).toEqual([]);
  });

  // The skills lane writes no README, so that name is free there.
  it("accepts README.md as a skill artifact name", async () => {
    const env = makeEnv();
    const res = await publishSkill(env, "README.md");
    expect(res.status).toBe(201);
  });
});
