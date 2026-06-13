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
