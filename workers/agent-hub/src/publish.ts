// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish handler.
 *
 * Two upload encodings, chosen by Content-Type:
 *
 *   multipart/form-data       Small artifacts (wheels, ~40 MB per-platform
 *                             binaries). The whole body is buffered to compute a
 *                             server-side SHA-256. Carries optional README +
 *                             CHANGELOG + package_files parts.
 *   application/octet-stream  Large whole-package zips (the four platform
 *                             binaries + npm client + docs, 100s of MB). The raw
 *                             body is STREAMED straight to R2 — never buffered in
 *                             the Worker — so it can't OOM Cloudflare's 128 MB
 *                             per-isolate memory limit (issue #1848). Metadata
 *                             (manifest, filename, client SHA-256, package_files)
 *                             rides in `x-gaia-*` headers; R2 verifies the
 *                             client-supplied SHA-256 as it streams.
 *
 * Both paths share: authenticate -> validate manifest -> enforce publisher scope
 * -> enforce version immutability -> store artifact + raw manifest + per-agent
 * manifest -> rebuild index.json. Every guard fails loudly with a structured error.
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
 * Read + validate the optional `package_files` part: the listing of files inside
 * the published whole-package zip. Must be JSON of shape
 * `{ files: [{ name, size_bytes }] }`. Absent → null (no package zip). A
 * present-but-malformed part fails loudly rather than storing junk.
 */
async function optionalPackageFiles(form: FormData): Promise<string | null> {
  const part = form.get("package_files");
  if (part == null) return null;
  const text = typeof part === "string" ? part : await (part as Blob).text();
  return validatePackageFilesText(text, "The 'package_files' part");
}

/**
 * Validate + canonicalize a `package_files` JSON listing, from either the
 * multipart part or the streaming `x-gaia-package-files-b64` header. Must be
 * `{ files: [{ name, size_bytes }] }` with at least one file; anything else
 * fails loudly. Returns the compact re-serialization so the stored object is
 * byte-stable regardless of input whitespace. `label` names the source in errors.
 */
function validatePackageFilesText(text: string, label: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_request",
      `${label} is not valid JSON: ${(e as Error).message}. Expected ` +
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
      `${label} must be { "files": [{ "name": string, ` +
        '"size_bytes": number }, ...] } with at least one file.'
    );
  }
  // Re-serialize canonically (compact) so the stored object is byte-stable.
  return JSON.stringify({ files });
}

/** Decode a base64 (`x-gaia-*-b64`) header value as UTF-8 text, failing loudly. */
function decodeB64Header(value: string, header: string): string {
  let binary: string;
  try {
    binary = atob(value);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_request",
      `Header '${header}' is not valid base64: ${(e as Error).message}.`
    );
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

export async function handlePublish(
  request: Request,
  env: Env,
  now: Date = new Date()
): Promise<Response> {
  const publisher = authenticate(request, env);

  const contentType = (request.headers.get("content-type") ?? "").toLowerCase();
  if (contentType.includes("multipart/form-data")) {
    return handleMultipartPublish(request, env, publisher, now);
  }
  if (contentType.includes("application/octet-stream")) {
    return handleStreamingPublish(request, env, publisher, now);
  }
  throw new HttpError(
    415,
    "unsupported_media_type",
    "POST /publish expects either multipart/form-data (small artifacts: 'manifest', " +
      "'artifact', optional 'readme'/'changelog'/'package_files' parts) or " +
      "application/octet-stream (large whole-package zip: the raw zip as the body, " +
      "with 'x-gaia-manifest-b64', 'x-gaia-artifact-filename', 'x-gaia-artifact-sha256', " +
      "and optional 'x-gaia-package-files-b64' headers)."
  );
}

/** Buffered multipart publish path (wheels + per-platform binaries). */
async function handleMultipartPublish(
  request: Request,
  env: Env,
  publisher: ReturnType<typeof authenticate>,
  now: Date
): Promise<Response> {
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

  // Store the buffered artifact (server-computed SHA-256, never trusts input).
  await env.BUCKET.put(key, bytes, {
    httpMetadata: { contentType: artifact.content_type },
    sha256,
  });

  return finalizeAndRespond({
    env,
    manifest,
    manifestText,
    artifact,
    existing,
    versionExists,
    readmeText,
    changelogText,
    packageFilesText,
    publisher,
    now,
  });
}

/**
 * Streaming publish path for large whole-package zips. The raw body is piped
 * straight to R2 — never buffered in the Worker — so a 100s-of-MB zip can't OOM
 * Cloudflare's 128 MB per-isolate limit (issue #1848). All metadata rides in
 * `x-gaia-*` headers; R2 verifies the client-supplied SHA-256 as it streams, so
 * integrity is preserved with no buffering and no silent fallback.
 */
async function handleStreamingPublish(
  request: Request,
  env: Env,
  publisher: ReturnType<typeof authenticate>,
  now: Date
): Promise<Response> {
  const manifestB64 = request.headers.get("x-gaia-manifest-b64");
  if (!manifestB64) {
    throw new HttpError(
      400,
      "invalid_request",
      "Streaming publish requires the 'x-gaia-manifest-b64' header (base64 of the " +
        "gaia-agent.yaml text)."
    );
  }
  const manifestText = decodeB64Header(manifestB64, "x-gaia-manifest-b64");

  const filename = request.headers.get("x-gaia-artifact-filename") ?? "";
  if (!FILENAME_RE.test(filename)) {
    throw new HttpError(
      400,
      "invalid_artifact",
      `Header 'x-gaia-artifact-filename' is ${JSON.stringify(filename)}, which is invalid. ` +
        `Use a single path segment of letters, digits, '.', '_', '+', '-' (e.g. 'agent-email-0.2.1.zip').`
    );
  }

  const sha256 = (request.headers.get("x-gaia-artifact-sha256") ?? "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(sha256)) {
    throw new HttpError(
      400,
      "invalid_request",
      "Header 'x-gaia-artifact-sha256' must be a 64-character lowercase hex SHA-256 of the " +
        "artifact body; R2 verifies the streamed bytes against it."
    );
  }

  // The body is streamed, so its size is taken from Content-Length (the limit is
  // enforced up front here, since nothing is buffered to measure afterward).
  const declaredLength = Number(request.headers.get("content-length"));
  if (!Number.isFinite(declaredLength) || declaredLength <= 0) {
    throw new HttpError(
      400,
      "invalid_artifact",
      "Streaming publish requires a positive Content-Length (the zip's byte size)."
    );
  }
  const limit = maxBytes(env);
  if (declaredLength > limit) {
    throw new HttpError(
      413,
      "artifact_too_large",
      `Artifact is ${declaredLength} bytes, over the ${limit}-byte limit.`
    );
  }

  if (!request.body) {
    throw new HttpError(400, "invalid_artifact", "Streaming publish has an empty request body.");
  }

  const packageFilesB64 = request.headers.get("x-gaia-package-files-b64");
  const packageFilesText =
    packageFilesB64 == null
      ? null
      : validatePackageFilesText(
          decodeB64Header(packageFilesB64, "x-gaia-package-files-b64"),
          "Header 'x-gaia-package-files-b64'"
        );

  const manifest = parseManifest(manifestText);
  assertAuthorAllowed(publisher, manifest.author);

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
  // Per-filename immutability — checked BEFORE consuming the body so an
  // idempotent re-run 409s without uploading.
  if (await env.BUCKET.head(key)) {
    throw new HttpError(
      409,
      "version_exists",
      `Artifact already exists at ${key} and is immutable. To add another ` +
        `platform binary use a distinct filename; to change this one, bump the version.`
    );
  }

  // Stream the body straight to R2. R2 verifies the client SHA-256 as it writes
  // and rejects a mismatch — surface that loudly (nothing partial is committed).
  try {
    await env.BUCKET.put(key, request.body, {
      httpMetadata: { contentType: "application/octet-stream" },
      sha256,
    });
  } catch (e) {
    throw new HttpError(
      422,
      "integrity_mismatch",
      `The streamed artifact failed server-side SHA-256 verification against the declared ` +
        `'x-gaia-artifact-sha256' (${sha256}). The upload was corrupted in transit or the ` +
        `declared digest is wrong; nothing was stored. Recompute the SHA-256 and retry. ` +
        `(${(e as Error).message})`
    );
  }

  const artifact: ArtifactInfo = {
    filename,
    path: key,
    size_bytes: declaredLength,
    sha256,
    content_type: "application/octet-stream",
  };

  // The large-zip path carries no README/CHANGELOG (only manifest + package_files).
  return finalizeAndRespond({
    env,
    manifest,
    manifestText,
    artifact,
    existing,
    versionExists,
    readmeText: null,
    changelogText: null,
    packageFilesText,
    publisher,
    now,
  });
}

/**
 * Shared tail for both publish paths: persist the per-version records (raw
 * manifest + optional README/CHANGELOG + optional package_files), upsert the
 * per-agent manifest, rebuild the catalog index, and build the 201 response. The
 * artifact bytes themselves are already stored by the caller.
 */
async function finalizeAndRespond(args: {
  env: Env;
  manifest: ReturnType<typeof parseManifest>;
  manifestText: string;
  artifact: ArtifactInfo;
  existing: Awaited<ReturnType<typeof readAgentManifest>>;
  versionExists: boolean;
  readmeText: string | null;
  changelogText: string | null;
  packageFilesText: string | null;
  publisher: ReturnType<typeof authenticate>;
  now: Date;
}): Promise<Response> {
  const {
    env,
    manifest,
    manifestText,
    artifact,
    existing,
    versionExists,
    readmeText,
    changelogText,
    packageFilesText,
    publisher,
    now,
  } = args;

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
