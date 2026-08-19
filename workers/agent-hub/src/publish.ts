// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish handler.
 *
 * Flow: authenticate -> parse multipart -> validate manifest -> enforce
 * publisher scope -> enforce version immutability -> generate server-side
 * SHA-256 -> store artifact + raw manifest + optional README + optional
 * CHANGELOG + optional SPEC + optional SKILL + optional EVALUATION +
 * optional CAPABILITY_MATRIX + per-agent manifest -> rebuild index.json.
 * Every guard fails loudly with a structured error.
 */

import { assertAuthorAllowed, authenticate } from "./auth";
import { makeVersionEntry, rebuildIndex, upsertVersion } from "./catalog";
import { HttpError, json } from "./http";
import { parseManifest } from "./manifest";
import {
  ARTIFACT_FILENAME_RE,
  maxBytes,
  optionalTextPart,
  sha256Hex,
} from "./multipart";
import {
  artifactKey,
  capabilityMatrixKey,
  changelogKey,
  evalScorecardKey,
  evaluationKey,
  packageFilesKey,
  rawManifestKey,
  readAgentManifest,
  readmeKey,
  skillKey,
  skillManifestKey,
  specKey,
  writeAgentManifest,
} from "./storage";
import type { ArtifactInfo, Env } from "./types";

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
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_request",
      `The 'package_files' part is not valid JSON: ${(e as Error).message}. Expected ` +
        `{ "files": [{ "name": "...", "size_bytes": 0 }] }, or omit the part.`
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
      "The 'package_files' part must be { \"files\": [{ \"name\": string, " +
        '"size_bytes": number }, ...] } with at least one file.'
    );
  }
  // Re-serialize canonically (compact) so the stored object is byte-stable.
  return JSON.stringify({ files });
}

/**
 * Pick the artifact source for this publish: an inline `artifact` file part, or
 * a by-reference upload named by `artifact_ref_filename`. Exactly one is valid.
 */
function selectArtifactSource(
  form: FormData,
  byReference: boolean,
  refFilename: string | null
): { artifactFile: File | null; filename: string } {
  const part = form.get("artifact");
  if (byReference) {
    if (part != null) {
      throw new HttpError(
        400,
        "invalid_request",
        "Send either an 'artifact' file part or 'artifact_ref_*' fields, not both."
      );
    }
    return { artifactFile: null, filename: refFilename as string };
  }
  if (part == null || typeof part === "string") {
    throw new HttpError(
      400,
      "invalid_request",
      "Missing 'artifact' file part (the wheel or binary to publish), and no " +
        "'artifact_ref_filename' for a by-reference publish."
    );
  }
  // workers-types declares FormData.get() as `string | null`, so the guard above
  // narrows to `never`; the cast is how the rest of this file already bridges
  // that gap between the declared type and the runtime File.
  const file = part as File;
  return { artifactFile: file, filename: file.name };
}

/** Hex-encode an ArrayBuffer (R2 returns checksums as raw bytes). */
function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Verify an artifact CI uploaded straight to R2, and build its catalog record.
 *
 * The Worker never sees these bytes — that is the whole point, since anything
 * over the plan's request-body cap (100 MB on Free/Pro) 413s at the edge before
 * this code runs. So "trust the publisher's claim" is not good enough: the
 * caller states a size and SHA-256, and both are checked against what R2 itself
 * recorded when it accepted the PUT.
 *
 * R2 only stores a whole-object SHA-256 for NON-multipart uploads. A missing
 * checksum is therefore refused rather than waved through — a multipart upload
 * would otherwise silently downgrade this to an unverified claim, which is
 * exactly the guarantee the inline path never gives up. CI must force a
 * single-part PUT with x-amz-checksum-sha256.
 */
async function verifyUploadedArtifact(
  env: Env,
  form: FormData,
  key: string,
  filename: string
): Promise<ArtifactInfo> {
  const claimedSha = (await optionalTextPart(form, "artifact_ref_sha256", "artifact_ref_sha256"))
    ?.trim()
    .toLowerCase();
  const claimedSizeText = await optionalTextPart(form, "artifact_ref_size", "artifact_ref_size");
  const contentType =
    (await optionalTextPart(form, "artifact_ref_content_type", "artifact_ref_content_type")) ??
    "application/octet-stream";

  if (!claimedSha || !/^[0-9a-f]{64}$/.test(claimedSha)) {
    throw new HttpError(
      400,
      "invalid_request",
      "A by-reference publish needs 'artifact_ref_sha256' as 64 lowercase hex characters."
    );
  }
  const claimedSize = Number(claimedSizeText);
  if (!Number.isInteger(claimedSize) || claimedSize <= 0) {
    throw new HttpError(
      400,
      "invalid_request",
      "A by-reference publish needs 'artifact_ref_size' as the object's byte count."
    );
  }

  const head = await env.BUCKET.head(key);
  if (!head) {
    throw new HttpError(
      404,
      "artifact_not_uploaded",
      `No object at ${key}. Upload the artifact to R2 first (S3 API), then publish ` +
        `it by reference. Nothing has been recorded in the catalog.`
    );
  }
  if (head.size !== claimedSize) {
    throw new HttpError(
      409,
      "artifact_mismatch",
      `Object at ${key} is ${head.size} bytes but the publish claims ${claimedSize}. ` +
        `Re-upload the artifact; the catalog was not modified.`
    );
  }

  const stored = head.checksums?.sha256;
  if (!stored) {
    throw new HttpError(
      409,
      "artifact_unverifiable",
      `Object at ${key} has no SHA-256 recorded by R2, so its integrity cannot be ` +
        `confirmed. R2 stores a whole-object SHA-256 only for single-part uploads — ` +
        `re-upload without multipart and with x-amz-checksum-sha256 set.`
    );
  }
  const actualSha = hex(stored);
  if (actualSha !== claimedSha) {
    throw new HttpError(
      409,
      "artifact_mismatch",
      `Object at ${key} hashes to ${actualSha} but the publish claims ${claimedSha}. ` +
        `The catalog was not modified.`
    );
  }

  return {
    filename,
    path: key,
    size_bytes: head.size,
    sha256: actualSha,
    content_type: contentType,
  };
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
        "(README.md markdown text) and 'changelog' (CHANGELOG.md markdown text) parts."
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

  // Two ways to supply the artifact:
  //
  //   inline     — an `artifact` file part. The Worker hashes and stores it.
  //   by-reference — `artifact_ref_*` text parts naming an object CI already
  //                  PUT straight into R2 over the S3 API.
  //
  // by-reference exists because a Worker request body is capped by the
  // Cloudflare plan (100 MB on Free/Pro) and the Agent UI installers are
  // 106-135 MiB, so they 413 at the edge before the Worker ever runs. Uploading
  // to R2 directly has no such cap. The integrity guarantees are NOT relaxed:
  // the object is verified below against the size and SHA-256 the caller
  // claims, using the checksum R2 itself recorded at PUT time.
  const refFilename = await optionalTextPart(form, "artifact_ref_filename", "artifact_ref_filename");
  const byReference = refFilename != null;

  // Exactly one of the two must be present. Resolved through a small helper so
  // the File narrowing stays local and cannot leak into the rest of the handler.
  const { artifactFile, filename } = selectArtifactSource(form, byReference, refFilename);

  // Optional README + CHANGELOG markdown for this version (rendered on the Hub
  // pages). Both are optional; an empty part is rejected (omit it instead).
  const readmeText = await optionalTextPart(form, "readme", "README.md");
  const changelogText = await optionalTextPart(form, "changelog", "CHANGELOG.md");
  // Optional SPEC.md (technical reference) + SKILL.md (AI-integration playbook),
  // rendered as their own doc tabs on the hub page. Same per-version, first-POST
  // semantics as README/CHANGELOG.
  const specText = await optionalTextPart(form, "spec", "SPEC.md");
  const skillText = await optionalTextPart(form, "skill", "SKILL.md");
  // Optional EVALUATION.md (evaluation guide), rendered as its own doc tab on the
  // hub page. Same per-version, first-POST semantics as SPEC/SKILL.
  const evaluationText = await optionalTextPart(form, "evaluation", "EVALUATION.md");
  // Optional CAPABILITY_MATRIX.md (tool-level capability matrix), rendered as its
  // own doc tab on the hub page. Same per-version, first-POST semantics as
  // SPEC/SKILL/EVALUATION.
  const capabilityMatrixText = await optionalTextPart(
    form,
    "capability_matrix",
    "CAPABILITY_MATRIX.md"
  );
  // Optional eval scorecard markdown (the agent's benchmark results, rendered on
  // the hub listing as an aggregate score + link). Per-version, first-POST semantics.
  const evalScorecardText = await optionalTextPart(form, "eval_scorecard", "SCORECARD.md");
  // Optional whole-package file listing (the zip's contents, for the hub's file
  // list). The zip itself rides in as a normal `artifact`; this is just the
  // manifest of what's inside it.
  const packageFilesText = await optionalPackageFiles(form);

  const manifest = parseManifest(manifestText);
  assertAuthorAllowed(publisher, manifest.author);

  if (!ARTIFACT_FILENAME_RE.test(filename)) {
    throw new HttpError(
      400,
      "invalid_artifact",
      `Artifact filename ${JSON.stringify(filename)} is invalid. Use a single path ` +
        `segment of letters, digits, '.', '_', '+', '-' (e.g. 'gaia_agent_chat-0.1.0-py3-none-any.whl').`
    );
  }

  // One id namespace across every catalog lane (#2467): hub URLs, install
  // commands, and the catalog are keyed by id, so an agent may not shadow a
  // published skill of the same name (nor the reverse — see skill-publish.ts).
  if (await env.BUCKET.head(skillManifestKey(manifest.id))) {
    throw new HttpError(
      409,
      "id_conflict",
      `'${manifest.id}' is already published as a skill. Agent ids share one ` +
        `namespace with skill names — rename the agent.`
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

  const key = artifactKey(manifest.id, manifest.version, filename);
  // Per-filename immutability: a published artifact is never overwritten. A new
  // platform binary under an existing version uses a distinct filename and is
  // allowed; re-publishing the same filename is rejected. (Idempotent re-runs of
  // a release job should treat this 409 as "already published" — success.)
  //
  // What counts as "published" differs by mode, and the distinction matters:
  // inline, the R2 object only exists once the Worker has stored it, so its
  // presence IS the record. By-reference, CI has already PUT the object before
  // calling this, so the object always exists and heading it would 409 every
  // time — the record is the AGENT MANIFEST, which only lists artifacts this
  // endpoint accepted.
  const alreadyPublished = byReference
    ? Boolean(existing?.versions[manifest.version]?.artifacts?.some((a) => a.filename === filename))
    : Boolean(await env.BUCKET.head(key));
  if (alreadyPublished) {
    throw new HttpError(
      409,
      "version_exists",
      `Artifact ${filename} is already published under ${manifest.id}@${manifest.version} ` +
        `and is immutable. To add another platform binary use a distinct filename; ` +
        `to change this one, bump the version.`
    );
  }

  let artifact: ArtifactInfo;
  if (byReference) {
    artifact = await verifyUploadedArtifact(env, form, key, filename);
  } else {
    const file = artifactFile!;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const limit = maxBytes(env);
    if (bytes.byteLength === 0) {
      throw new HttpError(400, "invalid_artifact", "Artifact is empty (0 bytes).");
    }
    if (bytes.byteLength > limit) {
      throw new HttpError(
        413,
        "artifact_too_large",
        `Artifact is ${bytes.byteLength} bytes, over the ${limit}-byte limit. ` +
          `Artifacts above the Cloudflare request-body cap must be uploaded to R2 ` +
          `directly and published by reference (artifact_ref_* fields).`
      );
    }
    const sha256 = await sha256Hex(bytes);
    artifact = {
      filename,
      path: key,
      size_bytes: bytes.byteLength,
      sha256,
      content_type: file.type || "application/octet-stream",
    };
    // Store the artifact. The raw gaia-agent.yaml is written only on the first
    // publish of a version so it stays the immutable record of that release; a
    // later platform binary joining the same version must not rewrite it.
    await env.BUCKET.put(key, bytes, {
      httpMetadata: { contentType: artifact.content_type },
      sha256,
    });
  }
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
    if (specText != null) {
      await env.BUCKET.put(specKey(manifest.id, manifest.version), specText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
    if (skillText != null) {
      await env.BUCKET.put(skillKey(manifest.id, manifest.version), skillText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
    if (evaluationText != null) {
      await env.BUCKET.put(evaluationKey(manifest.id, manifest.version), evaluationText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
    if (capabilityMatrixText != null) {
      await env.BUCKET.put(
        capabilityMatrixKey(manifest.id, manifest.version),
        capabilityMatrixText,
        { httpMetadata: { contentType: "text/markdown; charset=utf-8" } }
      );
    }
    if (evalScorecardText != null) {
      await env.BUCKET.put(evalScorecardKey(manifest.id, manifest.version), evalScorecardText, {
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

  const baseUrl = new URL(request.url).origin;
  const index = await rebuildIndex(env.BUCKET, now, baseUrl);

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
