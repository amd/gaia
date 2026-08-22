// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Publishing an artifact that CI uploaded straight to R2.
//
// Why this path exists: a Worker request body is capped by the Cloudflare plan
// (100 MB on Free/Pro), and the Agent UI installers are 106-135 MiB, so they
// 413 at the edge before the Worker runs at all. Uploading to R2 over the S3
// API has no such cap.
//
// The risk this introduces is that the Worker never sees the bytes, so it
// cannot hash them itself. These tests exist to pin the compensating control:
// the object is verified against the size and SHA-256 R2 recorded at PUT time,
// and anything unverifiable is REFUSED rather than trusted. A regression here
// would silently turn "the hub checked this artifact" into "the publisher said
// so", which is the difference between an integrity guarantee and a claim.

import { describe, expect, it } from "vitest";

import worker from "../src/index";
import type { AgentManifest } from "../src/types";
import { makeEnv, publishByRefRequest, sampleManifest, sha256Of } from "./fake-r2";

const BYTES = new TextEncoder().encode("pretend this is a 130 MiB installer");
const FILENAME = "gaia-agent-ui-1.0.0-x64-setup.exe";
const KEY = "agents/chat/0.1.0/" + FILENAME;

/** Stage an object in R2 the way CI's S3 upload would, with or without a checksum. */
async function stage(
  env: ReturnType<typeof makeEnv>,
  opts: { bytes?: Uint8Array; withChecksum?: boolean } = {}
) {
  const bytes = opts.bytes ?? BYTES;
  const sha = await sha256Of(bytes);
  await env.BUCKET.put(KEY, bytes, {
    httpMetadata: { contentType: "application/octet-stream" },
    ...(opts.withChecksum === false ? {} : { sha256: sha }),
  });
  return { bytes, sha };
}

async function publishRef(
  env: ReturnType<typeof makeEnv>,
  over: Partial<Parameters<typeof publishByRefRequest>[0]> = {},
  sha = "",
  size = BYTES.byteLength
) {
  return worker.fetch(
    publishByRefRequest({
      manifestYaml: sampleManifest(),
      filename: FILENAME,
      sha256: sha,
      size,
      ...over,
    }),
    env as never
  );
}

describe("publish by reference", () => {
  it("records an artifact the Worker never received", async () => {
    const env = makeEnv();
    const { sha, bytes } = await stage(env);

    const res = await publishRef(env, {}, sha, bytes.byteLength);
    expect(res.status).toBe(201);

    // The catalog must carry the same facts the inline path would have written,
    // or downloads and the install-time lock check have nothing to verify against.
    const manifest = JSON.parse(
      await (await env.BUCKET.get("agents/chat/manifest.json")).text()
    ) as AgentManifest;
    const artifact = manifest.versions["0.1.0"].artifacts[0];
    expect(artifact.filename).toBe(FILENAME);
    expect(artifact.sha256).toBe(sha);
    expect(artifact.size_bytes).toBe(bytes.byteLength);
    expect(artifact.path).toBe(KEY);
  });

  it("refuses when the object was never uploaded", async () => {
    const env = makeEnv();
    const res = await publishRef(env, {}, await sha256Of(BYTES));
    expect(res.status).toBe(404);
    expect((await res.json() as any).error.code).toBe("artifact_not_uploaded");
  });

  it("refuses when the stored bytes hash differently than claimed", async () => {
    // The case that matters: a publisher claiming a hash for bytes it did not
    // upload would otherwise poison the catalog for every future download.
    const env = makeEnv();
    await stage(env);
    const lie = "f".repeat(64);
    const res = await publishRef(env, {}, lie);
    expect(res.status).toBe(409);
    expect((await res.json() as any).error.code).toBe("artifact_mismatch");
  });

  it("refuses when the stored size differs from the claim", async () => {
    const env = makeEnv();
    const { sha } = await stage(env);
    const res = await publishRef(env, {}, sha, BYTES.byteLength + 1);
    expect(res.status).toBe(409);
    expect((await res.json() as any).error.code).toBe("artifact_mismatch");
  });

  it("refuses an object R2 has no checksum for, rather than trusting the claim", async () => {
    // R2 records a whole-object SHA-256 only for single-part uploads. A
    // multipart upload leaves none — and accepting the publisher's word there
    // would quietly reduce this to an unverified claim.
    const env = makeEnv();
    const { sha } = await stage(env, { withChecksum: false });
    const res = await publishRef(env, {}, sha);
    expect(res.status).toBe(409);
    expect((await res.json() as any).error.code).toBe("artifact_unverifiable");
  });

  it("rejects a malformed sha256 before touching the bucket", async () => {
    const env = makeEnv();
    await stage(env);
    for (const bad of ["", "abc", "G".repeat(64), "a".repeat(63)]) {
      const res = await publishRef(env, {}, bad);
      expect(res.status, `sha ${JSON.stringify(bad)} was accepted`).toBe(400);
    }
  });

  it("rejects a non-positive size", async () => {
    const env = makeEnv();
    const { sha } = await stage(env);
    for (const bad of [0, -1]) {
      const res = await publishRef(env, {}, sha, bad);
      expect(res.status, `size ${bad} was accepted`).toBe(400);
    }
  });

  it("keeps per-filename immutability, keyed on the manifest not the bucket", async () => {
    // Inline, the object's presence is the record. By-reference the object is
    // ALWAYS present by the time we are called, so the manifest is the record —
    // otherwise every by-reference publish would 409 against its own upload.
    const env = makeEnv();
    const { sha, bytes } = await stage(env);
    expect((await publishRef(env, {}, sha, bytes.byteLength)).status).toBe(201);

    const again = await publishRef(env, {}, sha, bytes.byteLength);
    expect(again.status).toBe(409);
    expect((await again.json() as any).error.code).toBe("version_exists");
  });

  it("refuses a request carrying both an inline artifact and a reference", async () => {
    const env = makeEnv();
    const form = new FormData();
    form.set("manifest", sampleManifest());
    form.set("artifact_ref_filename", FILENAME);
    form.set("artifact", new Blob([BYTES]), FILENAME);
    const res = await worker.fetch(
      new Request("https://hub.amd-gaia.ai/publish", {
        method: "POST",
        headers: { authorization: "Bearer tok_amd" },
        body: form,
      }),
      env as never
    );
    expect(res.status).toBe(400);
  });

  it("still requires authentication", async () => {
    const env = makeEnv();
    const { sha } = await stage(env);
    const res = await publishRef(env, { token: "not-a-real-token" }, sha);
    expect(res.status).toBe(401);
  });

  it("applies MAX_ARTIFACT_BYTES to a by-reference publish too", async () => {
    // Otherwise the documented artifact ceiling quietly stops covering the one
    // lane that exists specifically for the largest artifacts.
    const env = makeEnv({ maxBytes: "16" });
    const { sha, bytes } = await stage(env);
    const res = await publishRef(env, {}, sha, bytes.byteLength);
    expect(res.status).toBe(413);
    expect(((await res.json()) as any).error.code).toBe("artifact_too_large");
  });
});
