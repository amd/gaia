// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish handler.
 *
 * Two upload paths, selected by Content-Type:
 *
 * 1. multipart/form-data (EXISTING) — per-platform binaries and wheels. The
 *    artifact rides in the `artifact` form part (buffered). README, CHANGELOG,
 *    and package_files are optional form parts. All artifacts under ~128 MB
 *    (the Cloudflare Worker per-request memory limit).
 *
 * 2. application/octet-stream (NEW, streaming) — the whole-package zip (~177 MB).
 *    The raw request body is the artifact; metadata travels in X-Gaia-* headers.
 *    The body is passed directly to R2 without buffering, so memory usage is
 *    independent of artifact size. R2-side sha256 integrity is enforced via the
 *    X-Gaia-Sha256 header.
 *
 * Both paths share the same tail: makeVersionEntry → upsertVersion →
 * writeAgentManifest → rebuildIndex → 201.
 *
 * Flow for each path:
 *   authenticate → parse inputs → validate manifest → enforce publisher scope
 *   → enforce version immutability → store artifact → write ancillary objects
 *   → rebuild index. Every guard fails loudly with a structured error.
 */

import { assertAuthorAllowed, authenticate } from "./auth";
import { makeVersionEntry, rebuildIndex, upsertVersion } from "./catalog";
import { HttpError, json } from "./http";
import { parseManifest } from "./manifest";
import {
  artifactKey,
  changelogKey,
  packageFilesKey,
  rawManifestKey,
  readAgentManifest,
  readmeKey,
  writeAgentManifest,
} from "./storage";
import type { ArtifactInfo, AgentManifest, Env, ParsedManifest } from "./types";

const DEFAULT_MAX_BYTES = 262_144_000; // 250 MiB
// Artifact filename: a single safe path segment (no traversal, no separators).
const FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;
// Lowercase hex sha256: exactly 64 hex digits.
const SHA256_RE = /^[0-9a-f]{64}$/;

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

/**
 * Read an optional markdown form part (readme/changelog). Returns null when the
 * part is absent (the documented "" catalog default downstream), the LF-
 * normalized text when present, and fails loudly on a present-but-empty part —
 * an empty file is a mistake, so reject it rather than store a blank doc.
 */
async function optionalMarkdownPart(
  form: FormData,
  field: string,
  label: string
): Promise<string | null> {
  const part = form.get(field);
  if (part == null) return null;
  // Multipart string fields are CRLF-normalized by the form encoding —
  // canonicalize to LF so stored markdown is byte-stable either way.
  const text = (typeof part === "string" ? part : await (part as Blob).text()).replace(/\r\n/g, "\n");
  if (text.trim() === "") {
    throw new HttpError(
      400,
      "invalid_request",
      `The '${field}' part is empty. Send the ${label} markdown text, or omit the ` +
        `part entirely if the agent has none.`
    );
  }
  return text;
}

/**
 * Validate the package_files JSON text: must be `{ files: [{ name, size_bytes }] }`.
 * Returns the canonically re-serialized text on success. Throws HttpError on malformed input.
 * Used by both the multipart and streaming paths.
 */
function validatePackageFilesJson(text: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_request",
      `The 'package_files' value is not valid JSON: ${(e as Error).message}. Expected ` +
        `{ "files": [{ "name": "...", "size_bytes": 0 }] }, or omit it.`
    );
  }
  const files = (parsed as { files?: unknown }).files;
  if (
    !Array.isArray(files) ||
    files.length === 0 ||
    !files.every(
      (f) =>
        f &&
        typeof (f as Record<string, unknown>).name === "string" &&
        typeof (f as Record<string, unknown>).size_bytes === "number"
    )
  ) {
    throw new HttpError(
      400,
      "invalid_request",
      "The 'package_files' value must be { \"files\": [{ \"name\": string, " +
        '"size_bytes": number }, ...] } with at least one file.'
    );
  }
  // Re-serialize canonically (compact) so the stored object is byte-stable.
  return JSON.stringify({ files });
}

/**
 * Read + validate the optional `package_files` form part. Returns null when absent.
 */
async function optionalPackageFiles(form: FormData): Promise<string | null> {
  const part = form.get("package_files");
  if (part == null) return null;
  const text = typeof part === "string" ? part : await (part as Blob).text();
  return validatePackageFilesJson(text);
}

/**
 * UTF-8-safe base64 decode: handles non-ASCII characters in the encoded payload.
 */
function decodeBase64Utf8(b64: string): string {
  return new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
}

/**
 * Shared publish tail: write ancillary objects → update catalog → rebuild index → 201.
 *
 * Called by both the multipart and streaming paths once the artifact is stored.
 */
async function finishPublish(
  env: Env,
  manifest: ParsedManifest,
  artifact: ArtifactInfo,
  manifestText: string,
  packageFilesText: string | null,
  existing: AgentManifest | null,
  versionExists: boolean,
  publisher: string,
  now: Date
): Promise<Response> {
  // The raw gaia-agent.yaml, README, and CHANGELOG are per-version records.
  // Write only on the first publish of a version; a later artifact in the same
  // version must not overwrite them.
  if (!versionExists) {
    await env.BUCKET.put(rawManifestKey(manifest.id, manifest.version), manifestText, {
      httpMetadata: { contentType: "application/x-yaml; charset=utf-8" },
    });
  }

  // The package file listing rides the whole-package zip POST.  It must not be
  // gated on !versionExists: the zip arrives AFTER per-platform binaries have
  // already created the version.  Write it once (head check prevents re-write).
  if (
    packageFilesText != null &&
    !(await env.BUCKET.head(packageFilesKey(manifest.id, manifest.version)))
  ) {
    await env.BUCKET.put(packageFilesKey(manifest.id, manifest.version), packageFilesText, {
      httpMetadata: { contentType: "application/json; charset=utf-8" },
    });
  }

  const versionEntry = makeVersionEntry(manifest, artifact, publisher, now.toISOString());
  const updated = upsertVersion(existing, manifest, versionEntry);
  await writeAgentManifest(env.BUCKET, updated);

  const index = await rebuildIndex(env.BUCKET, now);

  return json(
    {
      published: {
        id: manifest.id,
        version: manifest.version,
        artifact,
        version_artifacts: updated.versions[manifest.version].artifacts.length,
        latest_version: updated.latest_version,
      },
      catalog_agents: index.agents.length,
    },
    201
  );
}

/**
 * Handle a streaming application/octet-stream publish.
 *
 * Metadata travels in X-Gaia-* headers; the raw request body is passed
 * directly to R2 without buffering.
 */
async function handleStreamingPublish(
  request: Request,
  env: Env,
  publisher: { publisher: string; authors: string[] },
  now: Date
): Promise<Response> {
  // Read required headers.
  const manifestB64 = request.headers.get("x-gaia-manifest");
  if (!manifestB64) {
    throw new HttpError(400, "invalid_request", "Missing required header X-Gaia-Manifest.");
  }
  const filename = request.headers.get("x-gaia-filename");
  if (!filename) {
    throw new HttpError(400, "invalid_request", "Missing required header X-Gaia-Filename.");
  }
  const sha256 = request.headers.get("x-gaia-sha256");
  if (!sha256) {
    throw new HttpError(400, "invalid_request", "Missing required header X-Gaia-Sha256.");
  }
  if (!SHA256_RE.test(sha256)) {
    throw new HttpError(
      400,
      "invalid_request",
      `X-Gaia-Sha256 must be a lowercase 64-hex-digit SHA-256 hash; got: ${JSON.stringify(sha256)}.`
    );
  }

  let manifestText: string;
  try {
    manifestText = decodeBase64Utf8(manifestB64);
  } catch {
    throw new HttpError(400, "invalid_request", "X-Gaia-Manifest is not valid base64.");
  }

  if (!FILENAME_RE.test(filename)) {
    throw new HttpError(
      400,
      "invalid_artifact",
      `Artifact filename ${JSON.stringify(filename)} is invalid. Use a single path ` +
        `segment of letters, digits, '.', '_', '+', '-' (e.g. 'agent-email-0.1.0.zip').`
    );
  }

  // Optional X-Gaia-Package-Files: base64 of package-files.json text.
  let packageFilesText: string | null = null;
  const packageFilesB64 = request.headers.get("x-gaia-package-files");
  if (packageFilesB64) {
    let raw: string;
    try {
      raw = decodeBase64Utf8(packageFilesB64);
    } catch {
      throw new HttpError(400, "invalid_request", "X-Gaia-Package-Files is not valid base64.");
    }
    packageFilesText = validatePackageFilesJson(raw);
  }

  const contentType =
    request.headers.get("x-gaia-content-type") ?? "application/octet-stream";

  const manifest = parseManifest(manifestText);
  assertAuthorAllowed(publisher, manifest.author);

  // Size guard without buffering: trust Content-Length for the pre-flight check.
  const declaredLenRaw = request.headers.get("content-length");
  const declaredLen = declaredLenRaw ? Number(declaredLenRaw) : NaN;
  if (!declaredLen || declaredLen <= 0 || !Number.isFinite(declaredLen)) {
    throw new HttpError(400, "invalid_artifact", "Content-Length is missing or zero; cannot determine artifact size.");
  }
  const limit = maxBytes(env);
  if (declaredLen > limit) {
    throw new HttpError(
      413,
      "artifact_too_large",
      `Content-Length ${declaredLen} bytes exceeds the ${limit}-byte limit.`
    );
  }

  // Ownership check.
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

  const key = artifactKey(manifest.id, manifest.version, filename);

  // Per-filename immutability.
  if (await env.BUCKET.head(key)) {
    throw new HttpError(
      409,
      "version_exists",
      `Artifact already exists at ${key} and is immutable. To add another ` +
        `platform binary use a distinct filename; to change this one, bump the version.`
    );
  }

  // Stream-store with R2-side integrity — no buffering.
  if (!request.body) {
    throw new HttpError(400, "invalid_artifact", "Request body is empty.");
  }
  let stored: { size: number } | null = null;
  try {
    stored = await env.BUCKET.put(key, request.body, {
      httpMetadata: { contentType },
      sha256,
    });
  } catch (e) {
    throw new HttpError(
      400,
      "integrity_check_failed",
      `Stored bytes did not match X-Gaia-Sha256=${sha256} (or the R2 put failed): ${(e as Error).message}.`
    );
  }

  const sizeBytes = stored?.size ?? declaredLen;
  const artifact: ArtifactInfo = {
    filename,
    path: key,
    size_bytes: sizeBytes,
    sha256,
    content_type: contentType,
  };

  return finishPublish(
    env,
    manifest,
    artifact,
    manifestText,
    packageFilesText,
    existing,
    versionExists,
    publisher.publisher,
    now
  );
}

export async function handlePublish(
  request: Request,
  env: Env,
  now: Date = new Date()
): Promise<Response> {
  const publisher = authenticate(request, env);

  const contentType = request.headers.get("content-type") ?? "";

  // Route to the streaming path for application/octet-stream.
  if (contentType.toLowerCase().startsWith("application/octet-stream")) {
    return handleStreamingPublish(request, env, publisher, now);
  }

  if (!contentType.toLowerCase().includes("multipart/form-data")) {
    throw new HttpError(
      415,
      "unsupported_media_type",
      "POST /publish expects either multipart/form-data (per-platform binaries/wheels) " +
        "or application/octet-stream (streaming whole-package zip with X-Gaia-* headers). " +
        "See workers/agent-hub/README.md for the full protocol."
    );
  }

  // --------------------------------------------------------------------------
  // Multipart path (EXISTING — unchanged behavior)
  // --------------------------------------------------------------------------

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

  // Optional README + CHANGELOG markdown for this version (rendered on the Hub
  // pages). Both are optional; an empty part is rejected (omit it instead).
  const readmeText = await optionalMarkdownPart(form, "readme", "README.md");
  const changelogText = await optionalMarkdownPart(form, "changelog", "CHANGELOG.md");
  // Optional whole-package file listing (the zip's contents, for the hub's file
  // list). The zip itself rides in as a normal `artifact`; this is just the
  // manifest of what's inside it.
  const packageFilesText = await optionalPackageFiles(form);

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

  // The raw gaia-agent.yaml, README, and CHANGELOG are per-version records:
  // write them only on the first publish of a version so a later platform binary
  // joining the same version cannot rewrite them.
  if (!versionExists) {
    await env.BUCKET.put(rawManifestKey(manifest.id, manifest.version), manifestText, {
      httpMetadata: { contentType: "application/x-yaml; charset=utf-8" },
    });
    if (readmeText != null) {
      await env.BUCKET.put(readmeKey(manifest.id, manifest.version), readmeText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
    if (changelogText != null) {
      await env.BUCKET.put(changelogKey(manifest.id, manifest.version), changelogText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
  }

  // The package file listing rides the whole-package zip POST, which in a real
  // release lands AFTER the per-platform binaries have already created this
  // version — so it must NOT be gated on `!versionExists` (that path only runs on
  // the first POST). Write it once, keyed per version; a re-POST of the immutable
  // zip 409s on the artifact above before reaching here, so this can't be rewritten.
  if (
    packageFilesText != null &&
    !(await env.BUCKET.head(packageFilesKey(manifest.id, manifest.version)))
  ) {
    await env.BUCKET.put(packageFilesKey(manifest.id, manifest.version), packageFilesText, {
      httpMetadata: { contentType: "application/json; charset=utf-8" },
    });
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
