// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Agent Hub catalog access layer.
//
// Today the catalog is read from a bundled fixture (`./index.json`) so the
// Hub pages build and render without a network round-trip. When the R2 bucket
// + Cloudflare Worker land (#1095), swap to the live catalog by flipping the
// single boundary below — change `CATALOG_SOURCE` to the R2 URL and the
// `loadCatalog()` import to a `fetch()`. Nothing else in the app changes:
// pages consume `getCatalog()` / `getAgent()` only.

import fixture from './index.json';

// One-line swap target. Point this at the R2 index when #1095 wires it up.
export const CATALOG_SOURCE = 'fixture';
// export const CATALOG_SOURCE = 'https://hub.amd-gaia.ai/index.json';

export type SecurityTier = 'verified' | 'community' | 'experimental';
export type AgentLanguage = 'python' | 'cpp';

export interface AgentRequirements {
  min_memory_gb: number;
  min_disk_gb: number;
  min_context_size: number;
  platforms: string[];
  npu: string;
  gpu_vram_gb: number;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  category: string;
  latest_version: string;
  icon: string;
  language: AgentLanguage;
  author: string;
  security_tier: SecurityTier;
  download_size_bytes: number;
  tags: string[];
  tools_count: number;
  models: string[];
  min_gaia_version: string;
  permissions: string[];
  deprecated: boolean;
  deprecation_message?: string;
  requirements: AgentRequirements;
  readme: string;
}

interface CatalogFile {
  schema_version: number;
  generated_at: string;
  agents: Agent[];
}

// Load the raw catalog. The ONLY function that knows where the data comes from.
// R2 swap: replace the fixture return with
//   `return (await fetch(CATALOG_SOURCE).then((r) => r.json())) as CatalogFile;`
async function loadCatalog(): Promise<CatalogFile> {
  return fixture as unknown as CatalogFile;
}

/** All agents in the catalog, sorted: verified first, then alphabetical. */
export async function getCatalog(): Promise<Agent[]> {
  const { agents } = await loadCatalog();
  const tierRank: Record<SecurityTier, number> = {
    verified: 0,
    community: 1,
    experimental: 2,
  };
  return [...agents].sort((a, b) => {
    if (a.deprecated !== b.deprecated) return a.deprecated ? 1 : -1;
    const tier = tierRank[a.security_tier] - tierRank[b.security_tier];
    if (tier !== 0) return tier;
    return a.name.localeCompare(b.name);
  });
}

/** A single agent by id, or undefined if not found. */
export async function getAgent(id: string): Promise<Agent | undefined> {
  const { agents } = await loadCatalog();
  return agents.find((a) => a.id === id);
}

// ---- Display helpers ----

const CATEGORY_LABELS: Record<string, string> = {
  conversation: 'Conversation',
  development: 'Development',
  productivity: 'Productivity',
  integrations: 'Integrations',
  creative: 'Creative',
  vision: 'Vision',
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

const LANGUAGE_LABELS: Record<AgentLanguage, string> = {
  python: 'Python',
  cpp: 'C++',
};

export function languageLabel(language: AgentLanguage): string {
  return LANGUAGE_LABELS[language] ?? language;
}

const SECURITY_TIER_LABELS: Record<SecurityTier, string> = {
  verified: 'Verified',
  community: 'Community',
  experimental: 'Experimental',
};

export function securityTierLabel(tier: SecurityTier): string {
  return SECURITY_TIER_LABELS[tier] ?? tier;
}

/** Tailwind classes for a security-tier badge. */
export function securityTierClasses(tier: SecurityTier): string {
  switch (tier) {
    case 'verified':
      return 'bg-green-500/10 text-green-400 border-green-500/30';
    case 'community':
      return 'bg-blue-500/10 text-blue-400 border-blue-500/30';
    case 'experimental':
      return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30';
    default:
      return 'bg-gaia-card text-gaia-muted border-gaia-border';
  }
}

/** Human-readable download size, e.g. "2.3 MB". */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Pretty platform label, e.g. "win-x64" → "Windows x64". */
export function platformLabel(platform: string): string {
  const map: Record<string, string> = {
    'win-x64': 'Windows x64',
    'linux-x64': 'Linux x64',
    'darwin-arm64': 'macOS (Apple Silicon)',
    'darwin-x64': 'macOS (Intel)',
  };
  return map[platform] ?? platform;
}

/** Distinct sorted values of a field across the catalog (for filter chips). */
export function distinct<K extends keyof Agent>(agents: Agent[], key: K): string[] {
  const set = new Set<string>();
  for (const a of agents) set.add(String(a[key]));
  return [...set].sort();
}
