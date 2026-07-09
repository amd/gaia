// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import { makeEnv, publishRequest, sampleManifest } from "./fake-r2";

function get(path: string) {
  return new Request(`https://hub.amd-gaia.ai${path}`, { method: "GET" });
}

async function seed(env: ReturnType<typeof makeEnv>) {
  await worker.fetch(
    publishRequest({
      token: "tok_amd",
      manifestYaml: sampleManifest({ id: "chat", version: "0.1.0" }),
      artifact: "chat-wheel",
      filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
    }),
    env as never
  );
}

describe("GET routes", () => {
  it("serves /health without auth", async () => {
    const env = makeEnv();
    const res = await worker.fetch(get("/health"), env as never);
    expect(res.status).toBe(200);
    expect(((await res.json()) as any).status).toBe("ok");
  });

  it("serves /index.json after a publish", async () => {
    const env = makeEnv();
    await seed(env);
    const res = await worker.fetch(get("/index.json"), env as never);
    expect(res.status).toBe(200);
    const body = (await res.json()) as any;
    expect(body.agents[0].id).toBe("chat");
  });

  it("serves the per-agent manifest.json", async () => {
    const env = makeEnv();
    await seed(env);
    const res = await worker.fetch(get("/agents/chat/manifest.json"), env as never);
    expect(res.status).toBe(200);
    const body = (await res.json()) as any;
    expect(body.id).toBe("chat");
    expect(body.versions["0.1.0"].artifact.sha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it("serves the artifact bytes", async () => {
    const env = makeEnv();
    await seed(env);
    const res = await worker.fetch(
      get("/agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl"),
      env as never
    );
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("chat-wheel");
  });

  it("404s an unknown object", async () => {
    const env = makeEnv();
    const res = await worker.fetch(get("/agents/nope/manifest.json"), env as never);
    expect(res.status).toBe(404);
  });

  it("does not let path traversal escape the agents/ prefix", async () => {
    const env = makeEnv();
    // URL normalization collapses ../ so the request can never resolve to an
    // object outside agents/ — it falls through to a 404 rather than serving
    // an arbitrary key.
    const res = await worker.fetch(get("/agents/%2e%2e/secrets"), env as never);
    expect(res.status).toBe(404);
  });

  it("405s a GET to /publish", async () => {
    const env = makeEnv();
    const res = await worker.fetch(get("/publish"), env as never);
    expect(res.status).toBe(405);
  });
});

// Minimal YAML front matter matching the email agent's scorecard shape.
const SAMPLE_SCORECARD = [
  "---",
  "schema_version: 1",
  "agent:",
  "  name: Test Agent",
  "  version: 0.1.0",
  "aggregate:",
  "  name: weighted_accuracy",
  "  value: 87.5",
  "generated_at: '2026-06-26T00:00:00Z'",
  "---",
  "# Test Agent — Eval Scorecard v0.1.0",
  "",
  "**Aggregate score: 87.5** (out of 100)",
].join("\n");

describe("eval scorecard in catalog", () => {
  it("exposes eval_score and eval_scorecard_url when a scorecard is published", async () => {
    const env = makeEnv();
    await worker.fetch(
      publishRequest({
        token: "tok_amd",
        manifestYaml: sampleManifest({ id: "chat", version: "0.1.0" }),
        artifact: "chat-wheel",
        filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
        evalScorecard: SAMPLE_SCORECARD,
      }),
      env as never
    );

    const res = await worker.fetch(get("/index.json"), env as never);
    expect(res.status).toBe(200);
    const body = (await res.json()) as any;
    const entry = body.agents[0];
    expect(entry.eval_score).toBe(87.5);
    expect(entry.eval_scorecard_url).toMatch(/\/agents\/chat\/0\.1\.0\/SCORECARD\.md$/);
    // The render-ready `scorecard` field carries the body with the YAML front
    // matter stripped, so the hub tab shows prose, not raw `schema_version:` etc.
    expect(entry.scorecard).toContain("# Test Agent — Eval Scorecard v0.1.0");
    expect(entry.scorecard).toContain("**Aggregate score: 87.5**");
    expect(entry.scorecard).not.toContain("schema_version:");
    expect(entry.scorecard.startsWith("---")).toBe(false);
  });

  it("omits eval_score and eval_scorecard_url when no scorecard is published", async () => {
    const env = makeEnv();
    await worker.fetch(
      publishRequest({
        token: "tok_amd",
        manifestYaml: sampleManifest({ id: "chat", version: "0.1.0" }),
        artifact: "chat-wheel",
        filename: "gaia_agent_chat-0.1.0-py3-none-any.whl",
        // no evalScorecard
      }),
      env as never
    );

    const res = await worker.fetch(get("/index.json"), env as never);
    expect(res.status).toBe(200);
    const body = (await res.json()) as any;
    const entry = body.agents[0];
    expect(entry.eval_score).toBeUndefined();
    expect(entry.eval_scorecard_url).toBeUndefined();
    // No scorecard published → the render-ready field defaults to "".
    expect(entry.scorecard).toBe("");
  });
});
