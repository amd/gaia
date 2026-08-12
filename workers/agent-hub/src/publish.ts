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

  const filename = artifactFile.name;
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
