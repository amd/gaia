// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Build per-agent manifests and the top-level catalog index.
 */

import { compareSemver } from "./manifest";
import {
  listAgentIds,
  readAgentManifest,
  readChangelog,
  readReadme,
  writeIndex,
} from "./storage";
import type {
  AgentManifest,
  ArtifactInfo,
  CatalogIndex,
  IndexEntry,
  ParsedManifest,
  VersionEntry,
} from "./types";

/** Pick the highest SemVer among a set of version strings. */
export function latestVersion(versions: string[]): string {
  return versions.reduce((best, v) => (compareSemver(v, best) > 0 ? v : best));
}

/**
 * Produce an updated per-agent manifest with `newVersion` added.
 *
 * A version's artifact set is append-only per distinct filename: if the version
 * already exists, the new artifact(s) are appended (the caller has already
 * rejected duplicate filenames). The aggregate metadata (name/description/...)
 * is refreshed from the manifest of whatever version becomes `latest_version`,
 * so the catalog reflects the newest release's display fields.
 */
export function upsertVersion(
  existing: AgentManifest | null,
  manifest: ParsedManifest,
  version: VersionEntry
): AgentManifest {
  // If the version already exists, this publish adds another platform binary to
  // it: append the new artifact(s), keep the original published_at/publisher and
  // the primary artifact. The caller has already rejected duplicate filenames.
  const prior = existing?.versions?.[version.version];
  // Back-compat: a manifest written before `artifacts[]` existed has only the
  // singular `artifact`. Treat that as the starting set so appending never
  // dereferences an undefined array.
  const priorArtifacts = prior ? (prior.artifacts ?? [prior.artifact]) : [];
  const merged: VersionEntry = prior
    ? { ...prior, artifacts: [...priorArtifacts, ...version.artifacts] }
    : version;

  const versions: Record<string, VersionEntry> = {
    ...(existing?.versions ?? {}),
    [version.version]: merged,
  };
  const latest = latestVersion(Object.keys(versions));

  // Display metadata tracks the latest version. If the just-published version
  // is the new latest, use its (freshly parsed) fields; otherwise keep what the
  // existing manifest had for the older display metadata.
  const useNew = latest === manifest.version;
  const base = useNew ? manifest : null;

  return {
    id: manifest.id,
    name: base?.name ?? existing?.name ?? manifest.name,
    description: base?.description ?? existing?.description ?? manifest.description,
    author: existing?.author ?? manifest.author,
    license: base?.license ?? existing?.license ?? manifest.license,
    language: base?.language ?? existing?.language ?? manifest.language,
    category: base?.category ?? existing?.category ?? manifest.category,
    tags: base?.tags ?? existing?.tags ?? manifest.tags,
    icon: base?.icon ?? existing?.icon ?? manifest.icon,
    security_tier: base?.security_tier ?? existing?.security_tier ?? manifest.security_tier,
    min_gaia_version: base?.min_gaia_version ?? existing?.min_gaia_version,
    models: base?.models ?? existing?.models ?? manifest.models,
    tools_count: base?.tools_count ?? existing?.tools_count ?? manifest.tools_count,
    permissions: base?.permissions ?? existing?.permissions ?? manifest.permissions,
    requirements: base?.requirements ?? existing?.requirements ?? manifest.requirements,
    interfaces: base?.interfaces ?? existing?.interfaces ?? manifest.interfaces,
    latest_version: latest,
    deprecated: versions[latest].deprecated,
    // Tracks the latest version exactly: if the new latest drops the message
    // (e.g. un-deprecated), a stale message must not survive.
    deprecation_message: useNew ? manifest.deprecation_message : existing?.deprecation_message,
    versions,
  };
}

/**
 * Build the catalog entry for one agent manifest. `readme` and `changelog` are
 * the latest version's markdown ("" if none was published).
 */
export function toIndexEntry(
  agent: AgentManifest,
  readme: string,
  changelog: string
): IndexEntry {
  const latest = agent.versions[agent.latest_version];
  const req = agent.requirements;
  return {
    id: agent.id,
    name: agent.name,
    description: agent.description,
    category: agent.category,
    latest_version: agent.latest_version,
    icon: agent.icon,
    language: agent.language,
    author: agent.author,
    security_tier: agent.security_tier,
    download_size_bytes: latest?.artifact.size_bytes ?? 0,
    tags: agent.tags,
    tools_count: agent.tools_count,
    models: agent.models,
    min_gaia_version: agent.min_gaia_version ?? "",
    permissions: agent.permissions,
    deprecated: agent.deprecated,
    // undefined serializes to "key absent" — only present when set.
    deprecation_message: agent.deprecation_message,
    requirements: {
      min_memory_gb: req.min_memory_gb,
      min_disk_gb: req.min_disk_gb,
      min_context_size: req.min_context_size,
      platforms: req.platforms,
      npu: req.npu ? "required" : "optional",
      gpu_vram_gb: req.gpu_vram_gb,
    },
    readme,
    changelog,
  };
}

/**
 * Rebuild `index.json` from every per-agent manifest currently in the bucket.
 * Returns the catalog that was written (handy for tests/responses).
 */
export async function rebuildIndex(
  bucket: R2Bucket,
  now: Date = new Date()
): Promise<CatalogIndex> {
  const ids = await listAgentIds(bucket);
  const entries: IndexEntry[] = [];
  for (const id of ids) {
    const agent = await readAgentManifest(bucket, id);
    if (!agent) continue;
    const readme = await readReadme(bucket, id, agent.latest_version);
    const changelog = await readChangelog(bucket, id, agent.latest_version);
    entries.push(toIndexEntry(agent, readme, changelog));
  }
  entries.sort((a, b) => a.id.localeCompare(b.id));

  const index: CatalogIndex = {
    schema_version: 1,
    generated_at: now.toISOString(),
    agents: entries,
  };
  await writeIndex(bucket, index);
  return index;
}

/** Build a {@link VersionEntry} from a parsed manifest + stored artifact. */
export function makeVersionEntry(
  manifest: ParsedManifest,
  artifact: ArtifactInfo,
  publisher: string,
  publishedAt: string
): VersionEntry {
  return {
    version: manifest.version,
    published_at: publishedAt,
    publisher,
    deprecated: manifest.deprecated,
    artifact,
    artifacts: [artifact],
  };
}
