// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish handler.
 *
 * Flow: authenticate -> parse multipart -> validate manifest -> enforce
 * publisher scope -> enforce version immutability -> generate server-side
 * SHA-256 -> store artifact + raw manifest + optional README + per-agent
 * manifest -> rebuild index.json. Every guard fails loudly with a structured
 * error.
 */

import { assertAuthorAllowed, authenticate } from "./auth";
import { makeVersionEntry, rebuildIndex, upsertVersion } from "./catalog";
import { HttpError, json } from "./http";
import { parseManifest } from "./manifest";
import {
  artifactKey,
  rawManifestKey,
  readAgentManifest,
  readmeKey,
  writeAgentManifest,
} from "./storage";
import type { ArtifactInfo, Env } from "./types";

const DEFAULT_MAX_BYTES = 262_144_000; // 250 MiB
// Artifact filename: a single safe path segment (no traversal, no separators).
const FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;

/** Lowercase hex SHA-256 of the given bytes, computed in the Worker. */
export async function sha256Hex(bytes: ArrayBuffer | Uint8Array): Promise<string> {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function maxBytes(env: Env): number {
  if (!env.MAX_ARTIFACT_BYTES) return DEFAULT_MAX_BYTES;
  const n = Number(env.MAX_ARTIFACT_BYTES);
  if (!Number.isFinite(n) || n <= 0) {
    throw new HttpError(
      500,
      "server_misconfigured",
      `MAX_ARTIFACT_BYTES is not a positive number: ${env.MAX_ARTIFACT_BYTES}.`
    );
  }
  return n;
}

export async function handlePublish(
  request: Request,
  env: Env,
  now: Date = new Date()
): Promise<Response> {
  const publisher = authenticate(request, env);

  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("multipart/form-data")) {
    throw new HttpError(
      415,
      "unsupported_media_type",
      "POST /publish expects multipart/form-data with 'manifest' (gaia-agent.yaml " +
        "text), 'artifact' (the wheel or binary file), and optionally 'readme' " +
        "(README.md markdown text) parts."
    );
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch (e) {
    throw new HttpError(400, "invalid_request", `Could not parse multipart body: ${(e as Error).message}.`);
  }

  const manifestPart = form.get("manifest");
  if (manifestPart == null) {
    throw new HttpError(400, "invalid_request", "Missing 'manifest' part (gaia-agent.yaml text).");
  }
  const manifestText =
    typeof manifestPart === "string" ? manifestPart : await (manifestPart as Blob).text();

  const artifactPart = form.get("artifact");
  if (artifactPart == null || typeof artifactPart === "string") {
    throw new HttpError(
      400,
      "invalid_request",
      "Missing 'artifact' file part (the wheel or binary to publish)."
    );
  }
  const artifactFile = artifactPart as File;

  // Optional README markdown for this version (rendered on the Hub pages).
  const readmePart = form.get("readme");
  let readmeText: string | null = null;
  if (readmePart != null) {
    readmeText = typeof readmePart === "string" ? readmePart : await (readmePart as Blob).text();
    // Multipart string fields are CRLF-normalized by the form encoding —
    // canonicalize to LF so stored READMEs are byte-stable either way.
    readmeText = readmeText.replace(/\r\n/g, "\n");
    if (readmeText.trim() === "") {
      throw new HttpError(
        400,
        "invalid_request",
        "The 'readme' part is empty. Send the README.md markdown text, or omit the " +
          "part entirely if the agent has no README."
      );
    }
  }

  const manifest = parseManifest(manifestText);
  assertAuthorAllowed(publisher, manifest.author);

  const filename = artifactFile.name;
  if (!FILENAME_RE.test(filename)) {
    throw new HttpError(
      400,
      "invalid_artifact",
      `Artifact filename ${JSON.stringify(filename)} is invalid. Use a single path ` +
        `segment of letters, digits, '.', '_', '+', '-' (e.g. 'gaia_agent_chat-0.1.0-py3-none-any.whl').`
    );
  }

  // Publisher scope (ownership) against the existing agent manifest. Version
  // immutability is enforced per-artifact below: a version's artifact set is
  // append-only per distinct filename, so a second platform binary can join an
  // existing version, but no published filename can ever be overwritten.
  const existing = await readAgentManifest(env.BUCKET, manifest.id);
  if (existing && existing.author !== manifest.author) {
    throw new HttpError(
      403,
      "forbidden_scope",
      `Agent '${manifest.id}' is owned by author '${existing.author}'. A publish ` +
        `with author '${manifest.author}' cannot update it.`
    );
  }
  const versionExists = Boolean(existing?.versions[manifest.version]);

  const bytes = new Uint8Array(await artifactFile.arrayBuffer());
  const limit = maxBytes(env);
  if (bytes.byteLength === 0) {
    throw new HttpError(400, "invalid_artifact", "Artifact is empty (0 bytes).");
  }
  if (bytes.byteLength > limit) {
    throw new HttpError(
      413,
      "artifact_too_large",
      `Artifact is ${bytes.byteLength} bytes, over the ${limit}-byte limit.`
    );
  }

  const key = artifactKey(manifest.id, manifest.version, filename);
  // Per-filename immutability: a published artifact is never overwritten. A new
  // platform binary under an existing version uses a distinct filename and is
  // allowed; re-uploading the same filename is rejected. (Idempotent re-runs of
  // a release job should treat this 409 as "already published" — success.)
  if (await env.BUCKET.head(key)) {
    throw new HttpError(
      409,
      "version_exists",
      `Artifact already exists at ${key} and is immutable. To add another ` +
        `platform binary use a distinct filename; to change this one, bump the version.`
    );
  }

  const sha256 = await sha256Hex(bytes);
  const artifact: ArtifactInfo = {
    filename,
    path: key,
    size_bytes: bytes.byteLength,
    sha256,
    content_type: artifactFile.type || "application/octet-stream",
  };

  // Store the artifact. The raw gaia-agent.yaml is written only on the first
  // publish of a version so it stays the immutable record of that release; a
  // later platform binary joining the same version must not rewrite it.
  await env.BUCKET.put(key, bytes, {
    httpMetadata: { contentType: artifact.content_type },
    sha256,
  });
  // The raw gaia-agent.yaml and README are per-version records: write them only
  // on the first publish of a version so a later platform binary joining the
  // same version cannot rewrite them.
  if (!versionExists) {
    await env.BUCKET.put(rawManifestKey(manifest.id, manifest.version), manifestText, {
      httpMetadata: { contentType: "application/x-yaml; charset=utf-8" },
    });
    if (readmeText != null) {
      await env.BUCKET.put(readmeKey(manifest.id, manifest.version), readmeText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
  }

  const versionEntry = makeVersionEntry(manifest, artifact, publisher.publisher, now.toISOString());
  const updated = upsertVersion(existing, manifest, versionEntry);
  await writeAgentManifest(env.BUCKET, updated);

  const index = await rebuildIndex(env.BUCKET, now);

  return json(
    {
      published: {
        id: manifest.id,
        version: manifest.version,
        artifact,
        // How many artifacts (platforms) now exist under this version.
        version_artifacts: updated.versions[manifest.version].artifacts.length,
        latest_version: updated.latest_version,
      },
      catalog_agents: index.agents.length,
    },
    201
  );
}
