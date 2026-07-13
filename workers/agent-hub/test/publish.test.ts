// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import type { AgentManifest, CatalogIndex } from "../src/types";
import { makeEnv, publishRequest, sampleManifest } from "./fake-r2";

async function publish(env: ReturnType<typeof makeEnv>, opts: Parameters<typeof publishRequest>[0]) {
  return worker.fetch(publishRequest(opts), env as never);
}

describe("POST /publish — authentication", () => {
  it("rejects a request with no Authorization header (401)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      manifestYaml: sampleManifest(),
      artifact: "wheel-bytes",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(401);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("unauthorized");
    // Nothing should have been written.
    expect(env.bucket.keys()).toEqual([]);
  });

  it("rejects an unknown token (401)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_not_real",
      manifestYaml: sampleManifest(),
      artifact: "wheel-bytes",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(401);
  });

  it("returns 500 when PUBLISH_TOKENS is unset (fail loudly, not allow-all)", async () => {
    const env = makeEnv();
    delete env.PUBLISH_TOKENS;
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel-bytes",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(500);
    const body = (await res.json()) as any;
    expect(body.error.code).toBe("server_misconfigured");
  });
});

describe("POST /publish — publisher scope", () => {
  it("rejects publishing under an author outside the token scope (403)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_indie", // authors: ["Indie Dev"]
      manifestYaml: sampleManifest({ author: "AMD" }),
      artifact: "wheel-bytes",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(403);
    expect(((await res.json()) as any).error.code).toBe("forbidden_scope");
  });

  it("blocks a different author from updating an agent owned by someone else (403)", async () => {
    const env = makeEnv();
    const first = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "shared", author: "AMD", version: "0.1.0" }),
      artifact: "v1",
      filename: "shared-0.1.0.whl",
    });
    expect(first.status).toBe(201);

    // Admin token (authors: ["*"]) is allowed by scope, but the agent is owned
    // by AMD — ownership check must still block it.
    const second = await publish(env, {
      token: "tok_admin",
      manifestYaml: sampleManifest({ id: "shared", author: "Someone Else", version: "0.2.0" }),
      artifact: "v2",
      filename: "shared-0.2.0.whl",
    });
    expect(second.status).toBe(403);
    expect(((await second.json()) as any).error.code).toBe("forbidden_scope");
  });
});

describe("POST /publish — version immutability", () => {
  it("rejects republishing an existing version (409)", async () => {
    const env = makeEnv();
    const first = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "original",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(first.status).toBe(201);

    const second = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "tampered",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(second.status).toBe(409);
    expect(((await second.json()) as any).error.code).toBe("version_exists");

    // The original artifact must be untouched.
    const obj = await env.bucket.get("agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl");
    expect(await obj!.text()).toBe("original");
  });

  it("appends a second platform binary to an existing version (multi-platform release)", async () => {
    const env = makeEnv();
    // First platform binary for email@0.1.0.
    const first = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "email", name: "Email", version: "0.1.0" }),
      artifact: "win32-binary",
      filename: "email-agent-win32-x64.exe",
    });
    expect(first.status).toBe(201);
    expect(((await first.json()) as any).published.version_artifacts).toBe(1);

    // Second platform binary under the SAME version — must be accepted, not 409.
    const second = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "email", name: "Email", version: "0.1.0" }),
      artifact: "darwin-arm64-binary",
      filename: "email-agent-darwin-arm64",
    });
    expect(second.status).toBe(201);
    expect(((await second.json()) as any).published.version_artifacts).toBe(2);

    // Both artifacts exist in R2 and in the per-agent manifest.
    const keys = env.bucket.keys();
    expect(keys).toContain("agents/email/0.1.0/email-agent-win32-x64.exe");
    expect(keys).toContain("agents/email/0.1.0/email-agent-darwin-arm64");

    const manifest = (await (await env.bucket.get("agents/email/manifest.json"))!.json()) as AgentManifest;
    const v = manifest.versions["0.1.0"];
    expect(v.artifacts.map((a) => a.filename).sort()).toEqual([
      "email-agent-darwin-arm64",
      "email-agent-win32-x64.exe",
    ]);
    // Primary artifact stays the first-published one.
    expect(v.artifact.filename).toBe("email-agent-win32-x64.exe");
    expect(Object.keys(manifest.versions)).toEqual(["0.1.0"]);
  });

  it("still rejects re-uploading the SAME filename under a version (409)", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "email", version: "0.1.0" }),
      artifact: "original",
      filename: "email-agent-win32-x64.exe",
    });
    const dup = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "email", version: "0.1.0" }),
      artifact: "tampered",
      filename: "email-agent-win32-x64.exe",
    });
    expect(dup.status).toBe(409);
    expect(((await dup.json()) as any).error.code).toBe("version_exists");
    // Original bytes untouched.
    const obj = await env.bucket.get("agents/email/0.1.0/email-agent-win32-x64.exe");
    expect(await obj!.text()).toBe("original");
  });

  it("allows publishing a new version of an existing agent (201)", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "v1",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.2.0" }),
      artifact: "v2",
      filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
    });
    expect(res.status).toBe(201);

    const manifest = (await (await env.bucket.get("agents/chat/manifest.json"))!.json()) as AgentManifest;
    expect(Object.keys(manifest.versions).sort()).toEqual(["0.1.0", "0.2.0"]);
    expect(manifest.latest_version).toBe("0.2.0");
  });
});

describe("POST /publish — server-side checksum", () => {
  it("generates the correct SHA-256 and never trusts client input", async () => {
    const env = makeEnv();
    const artifact = "deterministic-artifact-content";
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact,
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(201);

    // Compute the expected digest independently.
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(artifact));
    const expected = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");

    const body = (await res.json()) as any;
    expect(body.published.artifact.sha256).toBe(expected);
    expect(body.published.artifact.size_bytes).toBe(artifact.length);

    const manifest = (await (await env.bucket.get("agents/chat/manifest.json"))!.json()) as AgentManifest;
    expect(manifest.versions["0.1.0"].artifact.sha256).toBe(expected);
  });
});

describe("POST /publish — index rebuild & storage", () => {
  it("writes artifact, raw manifest, agent manifest, and rebuilds index.json", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "chat", version: "0.1.0" }),
      artifact: "chat-wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "code", name: "Code", version: "1.0.0", author: "AMD" }),
      artifact: "code-wheel",
      filename: "gaia_agent_code-1.0.0-py3-none-any.whl",
    });

    const keys = env.bucket.keys();
    expect(keys).toContain("agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl");
    expect(keys).toContain("agents/chat/0.1.0/gaia-agent.yaml");
    expect(keys).toContain("agents/chat/manifest.json");
    expect(keys).toContain("index.json");

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.schema_version).toBe(1);
    expect(index.agents.map((a) => a.id)).toEqual(["chat", "code"]); // sorted
    const chat = index.agents.find((a) => a.id === "chat")!;
    expect(chat).toMatchObject({
      name: "Chat",
      category: "conversation",
      latest_version: "0.1.0",
      language: "python",
      author: "AMD",
      security_tier: "verified",
      deprecated: false,
    });
    expect(chat.download_size_bytes).toBe("chat-wheel".length);
    expect(chat.requirements.platforms).toEqual(["win-x64", "linux-x64", "darwin-arm64"]);
  });

  it("keeps download_size_bytes pointed at the latest version's artifact", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "small",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.2.0" }),
      artifact: "a-much-larger-artifact-blob",
      filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents[0].download_size_bytes).toBe("a-much-larger-artifact-blob".length);
  });
});

describe("POST /publish — input validation", () => {
  it("rejects an invalid manifest (400)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: "id: Bad_ID\nname: X\n", // missing required + bad id
      artifact: "x",
      filename: "x.whl",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_manifest");
  });

  it("rejects a path-traversal artifact filename (400)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "x",
      filename: "../../etc/passwd",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_artifact");
  });

  it("rejects an empty artifact (400)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: new Uint8Array(0),
      filename: "empty.whl",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_artifact");
  });

  it("rejects an oversized artifact (413)", async () => {
    const env = makeEnv({ maxBytes: "4" });
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "way too many bytes",
      filename: "big.whl",
    });
    expect(res.status).toBe(413);
    expect(((await res.json()) as any).error.code).toBe("artifact_too_large");
  });

  it("rejects an unknown security_tier on publish (400)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ security_tier: "gold" }),
      artifact: "wheel-bytes",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_manifest");
    // Nothing written for a rejected publish.
    expect(env.bucket.keys()).toEqual([]);
  });
});

describe("POST /publish — catalog enrichment (website contract)", () => {
  it("passes tags, tools_count, models, min_gaia_version, permissions, and full requirements into index.json", async () => {
    const env = makeEnv();
    const yaml =
      sampleManifest({ tools_count: "7" }) + "permissions: [filesystem:read, network:none]\n";
    await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const chat = index.agents.find((a) => a.id === "chat")!;
    expect(chat.tags).toEqual(["chat", "general"]);
    expect(chat.tools_count).toBe(7);
    expect(chat.models).toEqual(["Qwen3.5-35B-A3B-GGUF"]);
    expect(chat.min_gaia_version).toBe("0.18.0");
    expect(chat.permissions).toEqual(["filesystem:read", "network:none"]);
    expect(chat.requirements).toEqual({
      min_memory_gb: 8,
      min_disk_gb: 0,
      min_context_size: 0,
      platforms: ["win-x64", "linux-x64", "darwin-arm64"],
      npu: "optional",
      gpu_vram_gb: 0,
    });
    // No deprecation_message was declared — the key must be absent, not null/"".
    expect("deprecation_message" in chat).toBe(false);
  });

  it("defaults permissions to [] and tools_count to 0 when the manifest omits them", async () => {
    const env = makeEnv();
    const yaml = sampleManifest()
      .split("\n")
      .filter((l) => !l.startsWith("tools_count:"))
      .join("\n");
    await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const chat = index.agents.find((a) => a.id === "chat")!;
    expect(chat.permissions).toEqual([]);
    expect(chat.tools_count).toBe(0);
  });

  it("maps requirements.npu true -> \"required\" in the index entry", async () => {
    const env = makeEnv();
    const yaml = sampleManifest().replace("  min_memory_gb: 8", "  min_memory_gb: 8\n  npu: true");
    await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents[0].requirements.npu).toBe("required");
  });

  it("surfaces deprecation_message in manifest.json and index.json", async () => {
    const env = makeEnv();
    const yaml =
      sampleManifest({ id: "old" }) + 'deprecated: true\ndeprecation_message: "Use chat instead."\n';
    await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "wheel",
      filename: "gaia_agent_old-0.1.0-py3-none-any.whl",
    });
    const manifest = (await (await env.bucket.get("agents/old/manifest.json"))!.json()) as AgentManifest;
    expect(manifest.deprecation_message).toBe("Use chat instead.");
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const old = index.agents.find((a) => a.id === "old")!;
    expect(old.deprecated).toBe(true);
    expect(old.deprecation_message).toBe("Use chat instead.");
  });
});

describe("POST /publish — README", () => {
  it("stores the readme, serves it on the download route, and includes it in index.json", async () => {
    const env = makeEnv();
    const readme = "# Chat Agent\n\nGeneral conversation with RAG.\n";
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      readme,
    });
    expect(res.status).toBe(201);
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/README.md");

    // Served by the existing GET /agents/... download route.
    const get = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/README.md"),
      env as never
    );
    expect(get.status).toBe(200);
    expect(get.headers.get("content-type")).toContain("text/markdown");
    expect(await get.text()).toBe(readme);

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "chat")!.readme).toBe(readme);
  });

  it("defaults readme to \"\" in index.json when none is published", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(env.bucket.keys()).not.toContain("agents/chat/0.1.0/README.md");
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "chat")!.readme).toBe("");
  });

  it("rejects an empty readme part (400) — omit it instead", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      readme: "   ",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_request");
    expect(env.bucket.keys()).toEqual([]);
  });

  it("index.json carries the latest version's readme", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "v1",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      readme: "# v1 readme",
    });
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.2.0" }),
      artifact: "v2",
      filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
      readme: "# v2 readme",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents[0].readme).toBe("# v2 readme");

    // Both versions' READMEs remain individually downloadable.
    const v1 = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/README.md"),
      env as never
    );
    expect(await v1.text()).toBe("# v1 readme");
  });
});

describe("POST /publish — CHANGELOG", () => {
  it("stores the changelog, serves it on the download route, and includes it in index.json", async () => {
    const env = makeEnv();
    const changelog = "# Changelog\n\n## 0.1.0\n\n- Initial release.\n";
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      changelog,
    });
    expect(res.status).toBe(201);
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/CHANGELOG.md");

    // Served by the existing GET /agents/... download route.
    const get = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/CHANGELOG.md"),
      env as never
    );
    expect(get.status).toBe(200);
    expect(get.headers.get("content-type")).toContain("text/markdown");
    expect(await get.text()).toBe(changelog);

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "chat")!.changelog).toBe(changelog);
  });

  it("defaults changelog to \"\" in index.json when none is published", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(env.bucket.keys()).not.toContain("agents/chat/0.1.0/CHANGELOG.md");
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "chat")!.changelog).toBe("");
  });

  it("rejects an empty changelog part (400) — omit it instead", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      changelog: "   ",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_request");
    expect(env.bucket.keys()).toEqual([]);
  });

  it("index.json carries the latest version's changelog", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "v1",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      changelog: "## 0.1.0\n- first",
    });
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.2.0" }),
      artifact: "v2",
      filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
      changelog: "## 0.2.0\n- second",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents[0].changelog).toBe("## 0.2.0\n- second");

    // Both versions' changelogs remain individually downloadable.
    const v1 = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/CHANGELOG.md"),
      env as never
    );
    expect(await v1.text()).toBe("## 0.1.0\n- first");
  });
});

describe("POST /publish — SPEC & SKILL doc tabs", () => {
  it("stores spec + skill, serves them, and includes them in index.json", async () => {
    const env = makeEnv();
    const spec = "# Spec\n\nThe wire contract is 2.0.\n";
    const skill = "# Skill\n\nSpawn the sidecar, then call triage.\n";
    const evaluation = "# Evaluation\n\nRun the eval, compare to baseline.\n";
    const capabilityMatrix = "# Capability Matrix\n\n| Capability | Supported |\n| --- | --- |\n| triage | yes |\n";
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      spec,
      skill,
      evaluation,
      capabilityMatrix,
    });
    expect(res.status).toBe(201);
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/SPEC.md");
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/SKILL.md");
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/EVALUATION.md");
    expect(env.bucket.keys()).toContain("agents/chat/0.1.0/CAPABILITY_MATRIX.md");

    // Served by the existing GET /agents/... download route, as markdown.
    const getSpec = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/SPEC.md"),
      env as never
    );
    expect(getSpec.status).toBe(200);
    expect(getSpec.headers.get("content-type")).toContain("text/markdown");
    expect(await getSpec.text()).toBe(spec);

    const getEvaluation = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/EVALUATION.md"),
      env as never
    );
    expect(getEvaluation.status).toBe(200);
    expect(getEvaluation.headers.get("content-type")).toContain("text/markdown");
    expect(await getEvaluation.text()).toBe(evaluation);

    const getCapabilityMatrix = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/CAPABILITY_MATRIX.md"),
      env as never
    );
    expect(getCapabilityMatrix.status).toBe(200);
    expect(getCapabilityMatrix.headers.get("content-type")).toContain("text/markdown");
    expect(await getCapabilityMatrix.text()).toBe(capabilityMatrix);

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const entry = index.agents.find((a) => a.id === "chat")!;
    expect(entry.spec).toBe(spec);
    expect(entry.skill).toBe(skill);
    expect(entry.evaluation).toBe(evaluation);
    expect(entry.capability_matrix).toBe(capabilityMatrix);

    // No eval_scorecard was published — neighboring scorecard fields must stay
    // at their defaults, unaffected by the new capability_matrix wiring.
    expect(entry.scorecard).toBe("");
    expect(entry.eval_score).toBeUndefined();
    expect(entry.eval_scorecard_url).toBeUndefined();
  });

  it('defaults spec + skill to "" in index.json when none are published', async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    });
    expect(env.bucket.keys()).not.toContain("agents/chat/0.1.0/SPEC.md");
    expect(env.bucket.keys()).not.toContain("agents/chat/0.1.0/EVALUATION.md");
    expect(env.bucket.keys()).not.toContain("agents/chat/0.1.0/CAPABILITY_MATRIX.md");
    const entry = (
      (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex
    ).agents.find((a) => a.id === "chat")!;
    expect(entry.spec).toBe("");
    expect(entry.skill).toBe("");
    expect(entry.evaluation).toBe("");
    expect(entry.capability_matrix).toBe("");
  });

  it("index.json carries the latest version's capability_matrix, but older versions stay downloadable", async () => {
    const env = makeEnv();
    const capabilityMatrixV1 = "# Capability Matrix v1\n\n| Capability | Supported |\n| --- | --- |\n| triage | yes |\n";
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.1.0" }),
      artifact: "v1",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      capabilityMatrix: capabilityMatrixV1,
    });
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ version: "0.2.0" }),
      artifact: "v2",
      filename: "gaia_agent_chat-0.2.0-py3-none-any.whl",
      // No capabilityMatrix for this version.
    });

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const entry = index.agents.find((a) => a.id === "chat")!;
    expect(entry.capability_matrix).toBe("");

    // The old version's capability matrix remains individually downloadable.
    const v1 = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/agents/chat/0.1.0/CAPABILITY_MATRIX.md"),
      env as never
    );
    expect(v1.status).toBe(200);
    expect(await v1.text()).toBe(capabilityMatrixV1);
  });

  it("rejects an empty spec part (400) — omit it instead", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest(),
      artifact: "wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
      spec: "   ",
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_request");
    expect(env.bucket.keys()).toEqual([]);
  });
});

describe("POST /publish — npm_package & playground_url", () => {
  it("carries npm_package and playground_url through to index.json", async () => {
    const env = makeEnv();
    const yaml =
      sampleManifest({ id: "sidecar" }) +
      'npm_package: "@amd-gaia/agent-sidecar"\n' +
      'playground_url: "http://127.0.0.1:8131/v1/x/playground"\n';
    await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "wheel",
      filename: "gaia_agent_sidecar-0.1.0-py3-none-any.whl",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const entry = index.agents.find((a) => a.id === "sidecar")!;
    expect(entry.npm_package).toBe("@amd-gaia/agent-sidecar");
    expect(entry.playground_url).toBe("http://127.0.0.1:8131/v1/x/playground");
  });

  it("omits both fields when the manifest doesn't declare them", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "plain" }),
      artifact: "wheel",
      filename: "gaia_agent_plain-0.1.0-py3-none-any.whl",
    });
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    const entry = index.agents.find((a) => a.id === "plain")!;
    expect(entry.npm_package).toBeUndefined();
    expect(entry.playground_url).toBeUndefined();
  });
});

describe("POST /publish — whole-package zip + file list", () => {
  const filesJson = JSON.stringify({
    files: [
      { name: "binaries/email-agent-linux-x64", size_bytes: 35000000 },
      { name: "dist/index.js", size_bytes: 4096 },
      { name: "README.md", size_bytes: 13000 },
    ],
  });

  it("surfaces package { filename, size_bytes, files } in index.json when a zip + package_files are published", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "sidecar" }),
      artifact: "ZIPBYTES",
      filename: "agent-sidecar-0.1.0.zip",
      packageFiles: filesJson,
    });
    const entry = (
      (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex
    ).agents.find((a) => a.id === "sidecar")!;
    expect(entry.package).toBeDefined();
    expect(entry.package!.filename).toBe("agent-sidecar-0.1.0.zip");
    expect(entry.package!.size_bytes).toBe("ZIPBYTES".length);
    expect(entry.package!.files).toHaveLength(3);
    expect(entry.package!.files[0].name).toBe("binaries/email-agent-linux-x64");
  });

  it("stores package_files on a LATER POST after the version already exists (real release order)", async () => {
    // The real release publishes the per-platform binaries first (creating the
    // version), THEN the whole-package zip + package_files in a separate POST. So
    // the listing arrives when versionExists is already true — it must still be
    // stored, or the catalog never gets `package` and the file list never renders.
    const env = makeEnv();
    const yaml = sampleManifest({ id: "email", name: "Email", version: "0.1.0" });

    const first = await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "linux-binary",
      filename: "email-agent-linux-x64",
    });
    expect(first.status).toBe(201);

    // Second POST: the whole-package zip + the file listing, version now exists.
    const second = await publish(env, {
      token: "tok_amd",
      manifestYaml: yaml,
      artifact: "ZIPBYTES",
      filename: "agent-email-0.1.0.zip",
      packageFiles: filesJson,
    });
    expect(second.status).toBe(201);

    const entry = (
      (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex
    ).agents.find((a) => a.id === "email")!;
    expect(entry.package).toBeDefined();
    expect(entry.package!.filename).toBe("agent-email-0.1.0.zip");
    expect(entry.package!.files).toHaveLength(3);
  });

  it("omits package when no package_files manifest is published (even if a .zip exists)", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "plain" }),
      artifact: "z",
      filename: "plain-0.1.0.zip",
    });
    const entry = (
      (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex
    ).agents.find((a) => a.id === "plain")!;
    expect(entry.package).toBeUndefined();
  });

  it("rejects a malformed package_files part (400)", async () => {
    const env = makeEnv();
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "bad" }),
      artifact: "z",
      filename: "bad-0.1.0.zip",
      packageFiles: '{"files":[{"name":"x"}]}', // missing size_bytes
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as any).error.code).toBe("invalid_request");
  });
});

describe("POST /publish — security tier & deprecation", () => {
  it("records the security_tier in the per-agent manifest and index", async () => {
    const env = makeEnv();
    await publish(env, {
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "exp", security_tier: "experimental" }),
      artifact: "wheel",
      filename: "gaia_agent_exp-0.1.0-py3-none-any.whl",
    });
    const manifest = (await (await env.bucket.get("agents/exp/manifest.json"))!.json()) as AgentManifest;
    expect(manifest.security_tier).toBe("experimental");
    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "exp")!.security_tier).toBe("experimental");
  });

  it("records deprecated state through to the index", async () => {
    const env = makeEnv();
    const deprecatedYaml = sampleManifest({ id: "old" }) + "deprecated: true\n";
    const res = await publish(env, {
      token: "tok_amd",
      manifestYaml: deprecatedYaml,
      artifact: "wheel",
      filename: "gaia_agent_old-0.1.0-py3-none-any.whl",
    });
    expect(res.status).toBe(201);

    const manifest = (await (await env.bucket.get("agents/old/manifest.json"))!.json()) as AgentManifest;
    expect(manifest.deprecated).toBe(true);
    expect(manifest.versions["0.1.0"].deprecated).toBe(true);

    const index = (await (await env.bucket.get("index.json"))!.json()) as CatalogIndex;
    expect(index.agents.find((a) => a.id === "old")!.deprecated).toBe(true);
  });
});
