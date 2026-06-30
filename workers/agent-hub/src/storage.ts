// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * R2 key layout and small read/write helpers.
 *
 * Bucket layout (documented in README.md):
 *   index.json                              top-level lightweight catalog
 *   agents/<id>/manifest.json               per-agent aggregate manifest
 *   agents/<id>/<version>/gaia-agent.yaml   raw uploaded manifest, per version
 *   agents/<id>/<version>/README.md         README markdown, per version (optional)
 *   agents/<id>/<version>/CHANGELOG.md      changelog markdown, per version (optional)
 *   agents/<id>/<version>/SPEC.md           spec/reference markdown, per version (optional)
 *   agents/<id>/<version>/SKILL.md          AI-integration playbook markdown (optional)
 *   agents/<id>/<version>/<filename>        the artifact (wheel or binary)
 */

import type { AgentManifest, CatalogIndex } from "./types";

export const INDEX_KEY = "index.json";
export const AGENTS_PREFIX = "agents/";

export function agentManifestKey(id: string): string {
  return `${AGENTS_PREFIX}${id}/manifest.json`;
}

export function versionDir(id: string, version: string): string {
  return `${AGENTS_PREFIX}${id}/${version}/`;
}

export function artifactKey(id: string, version: string, filename: string): string {
  return `${versionDir(id, version)}${filename}`;
}

export function rawManifestKey(id: string, version: string): string {
  return `${versionDir(id, version)}gaia-agent.yaml`;
}

export function readmeKey(id: string, version: string): string {
  return `${versionDir(id, version)}README.md`;
}

export function changelogKey(id: string, version: string): string {
  return `${versionDir(id, version)}CHANGELOG.md`;
}

export function specKey(id: string, version: string): string {
  return `${versionDir(id, version)}SPEC.md`;
}

export function skillKey(id: string, version: string): string {
  return `${versionDir(id, version)}SKILL.md`;
}

export function evalScorecardKey(id: string, version: string): string {
  return `${versionDir(id, version)}SCORECARD.md`;
}

export function packageFilesKey(id: string, version: string): string {
  return `${versionDir(id, version)}package-files.json`;
}

/**
 * Read the README markdown for one published version. Returns "" when no
 * README was published for that version — the catalog's documented default,
 * not an error (the `readme` form part is optional on POST /publish).
 */
export async function readReadme(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<string> {
  const obj = await bucket.get(readmeKey(id, version));
  if (!obj) return "";
  return obj.text();
}

/**
 * Read the CHANGELOG markdown for one published version. Returns "" when no
 * changelog was published for that version — the catalog's documented default,
 * not an error (the `changelog` form part is optional on POST /publish).
 */
export async function readChangelog(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<string> {
  const obj = await bucket.get(changelogKey(id, version));
  if (!obj) return "";
  return obj.text();
}

/**
 * Read the SPEC.md (technical reference) markdown for one version. Returns "" when
 * none was published — the `spec` form part on POST /publish is optional.
 */
export async function readSpec(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<string> {
  const obj = await bucket.get(specKey(id, version));
  if (!obj) return "";
  return obj.text();
}

/**
 * Read the SKILL.md (AI-integration playbook) markdown for one version. Returns ""
 * when none was published — the `skill` form part on POST /publish is optional.
 */
export async function readSkill(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<string> {
  const obj = await bucket.get(skillKey(id, version));
  if (!obj) return "";
  return obj.text();
}

/**
 * Read the eval scorecard markdown for one published version. Returns null when
 * none was published — the `eval_scorecard` form part is optional, so its
 * absence is not an error.
 */
export async function readEvalScorecard(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<string | null> {
  const obj = await bucket.get(evalScorecardKey(id, version));
  if (!obj) return null;
  return obj.text();
}

/**
 * Read the whole-package file listing (`{ files: [{name, size_bytes}] }`) for one
 * version, or null when none was published — the `package_files` form part on
 * POST /publish is optional, so its absence is the documented "no package zip"
 * default, not an error.
 */
export async function readPackageFiles(
  bucket: R2Bucket,
  id: string,
  version: string
): Promise<{ files: { name: string; size_bytes: number }[] } | null> {
  const obj = await bucket.get(packageFilesKey(id, version));
  if (!obj) return null;
  return (await obj.json()) as { files: { name: string; size_bytes: number }[] };
}

/** Read and parse the per-agent manifest, or null if the agent doesn't exist. */
export async function readAgentManifest(
  bucket: R2Bucket,
  id: string
): Promise<AgentManifest | null> {
  const obj = await bucket.get(agentManifestKey(id));
  if (!obj) return null;
  return (await obj.json()) as AgentManifest;
}

/** Write the per-agent manifest as pretty JSON. */
export async function writeAgentManifest(
  bucket: R2Bucket,
  manifest: AgentManifest
): Promise<void> {
  await bucket.put(agentManifestKey(manifest.id), JSON.stringify(manifest, null, 2), {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
  });
}

/** Write the top-level catalog index. */
export async function writeIndex(bucket: R2Bucket, index: CatalogIndex): Promise<void> {
  await bucket.put(INDEX_KEY, JSON.stringify(index, null, 2), {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
  });
}

/**
 * List every agent id present under `agents/`. Uses a delimited list so only
 * the directory prefixes are returned regardless of how many versions/artifacts
 * each agent has, paging through truncated results.
 */
export async function listAgentIds(bucket: R2Bucket): Promise<string[]> {
  const ids: string[] = [];
  let cursor: string | undefined;
  do {
    const res = await bucket.list({ prefix: AGENTS_PREFIX, delimiter: "/", cursor });
    for (const prefix of res.delimitedPrefixes) {
      // prefix looks like "agents/chat/" -> extract "chat"
      const id = prefix.slice(AGENTS_PREFIX.length, -1);
      if (id) ids.push(id);
    }
    cursor = res.truncated ? res.cursor : undefined;
  } while (cursor);
  return ids;
}
