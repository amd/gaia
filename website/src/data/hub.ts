// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The skills lane of the Agent Hub catalog, for the /hub page.
//
// Fetched at build time because hub.amd-gaia.ai sends no CORS headers. Same
// contract as github.ts: no bundled fixture and no silent fallback — an
// unreachable hub or a malformed catalog fails the build. An empty skills lane
// is a real state, not a failure, and the page renders it as one.
//
// The entry shape is the Worker's `IndexEntry` (workers/agent-hub/src/types.ts),
// read the way `gaia skill search` reads it (skill_library_tools.py).

export const HUB_INDEX_URL = 'https://hub.amd-gaia.ai/index.json';
const SKILLS_SOURCE = 'https://github.com/amd/gaia/tree/main/hub/skills';

// Mirrors LOWEST_TIER in src/gaia/skills/tiers.py: `gaia skill install` refuses
// a skill at this tier unless --allow-experimental is passed.
const LOWEST_TIER = 'experimental';

export interface HubSkill {
  id: string;
  name: string;
  description: string;
  version: string;
  tier: string;
  installCommand: string;
  sourceUrl: string;
}

export interface HubCatalogEntry {
  id?: unknown;
  name?: unknown;
  description?: unknown;
  type?: unknown;
  latest_version?: unknown;
  version?: unknown;
  security_tier?: unknown;
  skill_metadata?: { security_tier?: unknown } | null;
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

export function isSkillEntry(entry: HubCatalogEntry): boolean {
  return entry.type === 'skill';
}

export function installCommandFor(id: string, tier: string): string {
  return tier === LOWEST_TIER
    ? `gaia skill install ${id} --allow-experimental`
    : `gaia skill install ${id}`;
}

export function toHubSkill(entry: HubCatalogEntry): HubSkill {
  const id = str(entry.id);
  if (!id) {
    throw new Error(`[hub] A skill entry in ${HUB_INDEX_URL} has no 'id'.`);
  }
  const version = str(entry.latest_version) ?? str(entry.version);
  const tier = str(entry.security_tier) ?? str(entry.skill_metadata?.security_tier);
  if (!version || !tier) {
    throw new Error(
      `[hub] Skill '${id}' in ${HUB_INDEX_URL} is missing ` +
        `${!version ? "'latest_version'" : "'security_tier'"}. Check the Worker's ` +
        `toSkillIndexEntry (workers/agent-hub/src/catalog.ts).`,
    );
  }
  return {
    id,
    name: str(entry.name) ?? id,
    description: typeof entry.description === 'string' ? entry.description : '',
    version,
    tier,
    installCommand: installCommandFor(id, tier),
    sourceUrl: `${SKILLS_SOURCE}/${id}`,
  };
}

/** The skills lane of a catalog document, in catalog order. */
export function skillsFromIndex(json: unknown): HubSkill[] {
  const agents = (json as { agents?: unknown } | null)?.agents;
  if (!Array.isArray(agents)) {
    throw new Error(`[hub] Response from ${HUB_INDEX_URL} is missing an 'agents' array.`);
  }
  return (agents as HubCatalogEntry[]).filter(isSkillEntry).map(toHubSkill);
}

async function fetchSkills(): Promise<HubSkill[]> {
  let res: Response;
  try {
    res = await fetch(HUB_INDEX_URL);
  } catch (e) {
    // undici's message is just "fetch failed"; the cause names the TLS/DNS error.
    const cause = (e as Error & { cause?: Error }).cause?.message;
    throw new Error(
      `[hub] Failed to fetch ${HUB_INDEX_URL}: ${(e as Error).message}` +
        `${cause ? ` (${cause})` : ''}. Check network ` +
        `connectivity; the /hub page has no bundled fallback.`,
    );
  }
  if (!res.ok) {
    throw new Error(`[hub] Request to ${HUB_INDEX_URL} returned HTTP ${res.status}.`);
  }
  const skills = skillsFromIndex(await res.json());
  console.log(`[hub] ${skills.length} skill(s) in the catalog`);
  return skills;
}

let skillsPromise: Promise<HubSkill[]> | null = null;

export async function getHubSkills(): Promise<HubSkill[]> {
  skillsPromise ??= fetchSkills();
  return skillsPromise;
}
