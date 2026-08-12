// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * POST /publish/skill handler — the skills marketplace lane (#2467).
 *
 * Flow: authenticate -> parse multipart -> validate SKILL.md against the
 * skill-format grammar -> **security-audit gate (#2468)** -> enforce the single
 * cross-lane id namespace -> enforce ownership + version immutability ->
 * generate server-side SHA-256 -> store artifact + raw SKILL.md + optional
 * CHANGELOG + audit report + per-skill manifest -> rebuild index.json.
 *
 * It is a sibling of `publish.ts`, not a fork of it: auth, artifact hashing,
 * size limits, and the index rebuild are shared. Skills are published from a
 * `SKILL.md`, not a `gaia-agent.yaml`, which is why the two lanes validate
 * different manifests behind different routes.
 */

import { authenticate } from "./auth";
import { assertAuditGate, manifestDigest, parseAuditReport } from "./audit";
import { rebuildIndex, upsertSkillVersion } from "./catalog";
import { HttpError, json } from "./http";
import {
  ARTIFACT_FILENAME_RE,
  maxBytes,
  optionalTextPart,
  requiredTextPart,
  sha256Hex,
} from "./multipart";
import { parseSkillManifest } from "./skill-manifest";
import {
  agentManifestKey,
  readSkillManifest,
  skillArtifactKey,
  skillAuditKey,
  skillChangelogKey,
  skillDocKey,
  writeSkillManifest,
} from "./storage";
import type { ArtifactInfo, Env, VersionEntry } from "./types";

export async function handleSkillPublish(
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
      "POST /publish/skill expects multipart/form-data with 'skill' (the SKILL.md " +
        "text), 'artifact' (the packaged skill directory), and optionally " +
        "'changelog' and 'audit' (the security-audit report JSON) parts."
    );
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_request",
      `Could not parse multipart body: ${(e as Error).message}.`
    );
  }

  const skillMarkdown = await requiredTextPart(
    form,
    "skill",
    "the full SKILL.md: YAML front matter plus the instruction body"
  );
  const changelogText = await optionalTextPart(form, "changelog", "CHANGELOG.md");
  const auditText = await optionalTextPart(form, "audit", "security-audit report JSON");

  const artifactPart = form.get("artifact");
  if (artifactPart == null || typeof artifactPart === "string") {
    throw new HttpError(
      400,
      "invalid_request",
      "Missing 'artifact' file part (the packaged skill directory, e.g. " +
        "'web-research-0.1.0.zip')."
    );
  }
  const artifactFile = artifactPart as File;

  const { manifest } = parseSkillManifest(skillMarkdown);

  // ── Security-audit gate (#2468) ────────────────────────────────────────────
  // Runs BEFORE anything is written, so a BLOCKed or REVIEW-held skill leaves no
  // trace in R2. `assertAuditGate` refuses a tier that requires a cleared audit
  // and arrives without one — there is no permissive default. See `audit.ts`.
  // The binding argument is what lets the gate CHECK the report instead of
  // trusting it: the report must name this skill, this version, and the bytes of
  // the SKILL.md actually uploaded (#2468).
  const auditRecord = assertAuditGate(
    parseAuditReport(auditText),
    manifest.security_tier,
    {
      skill: manifest.name,
      version: manifest.version,
      manifestDigest: await manifestDigest(skillMarkdown),
    }
  );

  const filename = artifactFile.name;
  if (!ARTIFACT_FILENAME_RE.test(filename)) {
    throw new HttpError(
      400,
      "invalid_artifact",
      `Artifact filename ${JSON.stringify(filename)} is invalid. Use a single path ` +
        `segment of letters, digits, '.', '_', '+', '-' (e.g. 'web-research-0.1.0.zip').`
    );
  }

  // One id namespace across every catalog lane: hub URLs, install commands, and
  // the catalog are keyed by id, so a skill may not shadow an agent package.
  if (await env.BUCKET.head(agentManifestKey(manifest.name))) {
    throw new HttpError(
      409,
      "id_conflict",
      `'${manifest.name}' is already published as an agent package. Skill names ` +
        `share one namespace with agent ids — rename the skill.`
    );
  }

  // A skill's provenance is its publisher identity (SKILL.md has no `author`
  // field), so ownership compares against the token's publisher.
  const existing = await readSkillManifest(env.BUCKET, manifest.name);
  if (existing && existing.author !== publisher.publisher) {
    throw new HttpError(
      403,
      "forbidden_scope",
      `Skill '${manifest.name}' is owned by publisher '${existing.author}'. A publish ` +
        `from '${publisher.publisher}' cannot update it.`
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

  const key = skillArtifactKey(manifest.name, manifest.version, filename);
  if (await env.BUCKET.head(key)) {
    throw new HttpError(
      409,
      "version_exists",
      `Artifact already exists at ${key} and is immutable. Bump the skill's version ` +
        `to publish a change.`
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

  await env.BUCKET.put(key, bytes, {
    httpMetadata: { contentType: artifact.content_type },
    sha256,
  });

  // The SKILL.md, changelog, and audit report are per-version records: written
  // only on the first publish of a version so they stay the immutable evidence
  // of what was audited and shipped.
  if (!versionExists) {
    await env.BUCKET.put(skillDocKey(manifest.name, manifest.version), skillMarkdown, {
      httpMetadata: { contentType: "text/markdown; charset=utf-8" },
    });
    if (changelogText != null) {
      await env.BUCKET.put(skillChangelogKey(manifest.name, manifest.version), changelogText, {
        httpMetadata: { contentType: "text/markdown; charset=utf-8" },
      });
    }
    if (auditText != null) {
      await env.BUCKET.put(skillAuditKey(manifest.name, manifest.version), auditText, {
        httpMetadata: { contentType: "application/json; charset=utf-8" },
      });
    }
  }

  // A skill has no deprecation field in the format yet, so every version entry
  // is published un-deprecated.
  const versionEntry: VersionEntry = {
    version: manifest.version,
    published_at: now.toISOString(),
    publisher: publisher.publisher,
    deprecated: false,
    artifact,
    artifacts: [artifact],
  };
  const updated = upsertSkillVersion(
    existing,
    manifest,
    publisher.publisher,
    auditRecord,
    versionEntry
  );
  await writeSkillManifest(env.BUCKET, updated);

  const baseUrl = new URL(request.url).origin;
  const index = await rebuildIndex(env.BUCKET, now, baseUrl);

  return json(
    {
      published: {
        name: manifest.name,
        version: manifest.version,
        security_tier: manifest.security_tier,
        audit: auditRecord,
        artifact,
        latest_version: updated.latest_version,
      },
      catalog_entries: index.agents.length,
    },
    201
  );
}
