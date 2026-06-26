// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Agent Hub catalog access layer.
//
// The hub pages are built ENTIRELY from the live hub catalog — there is no
// bundled fixture, so the site can never drift from what is actually published.
// The catalog is fetched at build time from `${HUB_CATALOG_URL}/index.json`
// (the agent-hub Worker, workers/agent-hub). HUB_CATALOG_URL is REQUIRED: if it
// is unset, or the fetch fails, or the shape is wrong, the build FAILS LOUDLY —
// there is no silent fallback to stale data.
//
//   Production (Railway): set HUB_CATALOG_URL=https://hub.amd-gaia.ai
//   Local dev:            HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run dev
//                         (or point it at a local Worker — workers/agent-hub/README.md)
//
// Nothing else in the app changes: pages consume getCatalog()/getAgent() only.

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
  // CHANGELOG.md markdown of the latest version; "" if none was published.
  // Optional at the type level so the site stays resilient to an older index.json
  // served before the hub Worker that adds this field is redeployed.
  changelog?: string;
  // SPEC.md (technical reference) + SKILL.md (AI-integration playbook) markdown of
  // the latest version, rendered as their own doc tabs. "" / absent if none was
  // published. Optional for the same older-index.json resilience as `changelog`.
  spec?: string;
  skill?: string;
  // npm package name (e.g. "@amd-gaia/agent-email") when the agent is
  // distributed as an npm client + frozen sidecar. Present → npm is the install
  // path. Absent → the agent installs via pip/GAIA (language-driven).
  npm_package?: string;
  // Localhost URL of the agent's interactive playground, served by its sidecar
  // (e.g. "http://127.0.0.1:8131/v1/email/playground"). Only resolves once the
  // package is installed and the sidecar is running — a best-effort dev link.
  playground_url?: string;
  // Whole-package download: a single zip (all platform binaries + client + docs)
  // and its file listing. Present only when the latest version published one.
  package?: {
    filename: string;
    size_bytes: number;
    files: { name: string; size_bytes: number }[];
  };
}

interface CatalogFile {
  schema_version: number;
  generated_at: string;
  agents: Agent[];
}

async function fetchLiveCatalog(baseUrl: string): Promise<CatalogFile> {
  const url = `${baseUrl.replace(/\/+$/, '')}/index.json`;
  // Cache-bust the edge. A release publishes the new index.json moments before the
  // website redeploy runs, but the Cloudflare edge in front of hub.amd-gaia.ai can
  // still serve a stale copy (the deploy races the cache invalidation) — which would
  // build the site from the previous version's catalog. A unique per-build query
  // param + `no-store` forces a fresh origin fetch, so every build reflects the
  // just-published catalog. Build-time only, so there's no runtime cost.
  const fetchUrl = `${url}?t=${Date.now()}`;
  console.log(`[catalog] HUB_CATALOG_URL is set — fetching live catalog from ${url}`);
  let res: Response;
  try {
    res = await fetch(fetchUrl, { cache: 'no-store' });
  } catch (e) {
    throw new Error(
      `[catalog] Failed to fetch the live catalog from ${url}: ${(e as Error).message}. ` +
        `HUB_CATALOG_URL is set, so the build must use the live hub — it will not ` +
        `fall back to the bundled fixture. Start the agent-hub worker (see ` +
        `workers/agent-hub/README.md) or unset HUB_CATALOG_URL to build from the fixture.`
    );
  }
  if (!res.ok) {
    throw new Error(
      `[catalog] Live catalog request to ${url} returned HTTP ${res.status}. ` +
        `Check that the agent-hub worker is healthy (GET /health) and has at least ` +
        `one published agent, or unset HUB_CATALOG_URL to build from the fixture.`
    );
  }
  const catalog = (await res.json()) as CatalogFile;
  if (!Array.isArray(catalog.agents)) {
    throw new Error(
      `[catalog] Live catalog at ${url} has no 'agents' array — the hub worker ` +
        `returned an unexpected shape. See workers/agent-hub/schemas/index.schema.json.`
    );
  }
  console.log(`[catalog] Loaded ${catalog.agents.length} agents from the live catalog`);
  return catalog;
}

// One fetch per build, shared across pages.
let liveCatalog: Promise<CatalogFile> | null = null;

// Load the raw catalog. The ONLY function that knows where the data comes from.
async function loadCatalog(): Promise<CatalogFile> {
  const hubUrl = process.env.HUB_CATALOG_URL;
  if (!hubUrl) {
    throw new Error(
      '[catalog] HUB_CATALOG_URL is not set. The website builds its Agent Hub ' +
        'pages from the live hub catalog and has no bundled fixture fallback. ' +
        'Set HUB_CATALOG_URL=https://hub.amd-gaia.ai for production/Railway, or ' +
        'point it at a local agent-hub Worker (workers/agent-hub/README.md), e.g. ' +
        '`HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run build`.'
    );
  }
  liveCatalog ??= fetchLiveCatalog(hubUrl);
  return liveCatalog;
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

/**
 * Absolute URL of an agent's whole-package zip, served from the same hub origin
 * as the catalog (`${HUB_CATALOG_URL}/agents/<id>/<version>/<filename>`). Returns
 * null when the agent has no published package zip. Build-time only.
 */
export function packageDownloadUrl(agent: Agent): string | null {
  if (!agent.package) return null;
  const base = process.env.HUB_CATALOG_URL;
  if (!base) return null;
  return `${base.replace(/\/+$/, '')}/agents/${agent.id}/${agent.latest_version}/${agent.package.filename}`;
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

export interface InstallMethod {
  key: string;
  label: string;
  command: string;
  note: string;
}

/**
 * Install methods for an agent, derived from the MANIFEST — never from README
 * markup. We only ever show channels that actually work:
 *
 *  - An agent with `npm_package` (the email sidecar) is distributed as an npm
 *    client + frozen binary, NOT a PyPI wheel. npm is its single supported path,
 *    so we show only that — no broken `pip install` (there's no wheel) and no
 *    unverified source build.
 *  - Otherwise: the GAIA app install, a pip package for Python agents, and a
 *    source build (language-driven, the long-standing default).
 */
export function installMethods(agent: Agent): InstallMethod[] {
  if (agent.npm_package) {
    return [
      {
        key: 'npm',
        label: 'npm',
        command: `npm i ${agent.npm_package}`,
        note: '',
      },
    ];
  }

  const methods: InstallMethod[] = [
    {
      key: 'gaia',
      label: 'GAIA',
      command: `gaia agent install ${agent.id}`,
      note: 'Recommended — installs into your GAIA app and registers the agent automatically.',
    },
  ];
  if (agent.language === 'python') {
    methods.push({
      key: 'pip',
      label: 'pip',
      command: `pip install gaia-agent-${agent.id}`,
      note: 'Python package from PyPI. Discovered via the gaia.agent entry-point group.',
    });
  }
  methods.push({
    key: 'source',
    label: 'Source',
    command: 'git clone https://github.com/amd/gaia.git',
    note: 'Build from the GAIA repository — clone, then follow the agent README to install it.',
  });
  return methods;
}

const SECURITY_TIER_DESCRIPTIONS: Record<SecurityTier, string> = {
  verified: 'Built and reviewed by AMD.',
  community: 'Publisher-signed but not reviewed by AMD — install with the usual third-party caution.',
  experimental: 'Opt-in only; may run outside the Python sandbox. Review the source before installing.',
};

export function securityTierDescription(tier: SecurityTier): string {
  return SECURITY_TIER_DESCRIPTIONS[tier] ?? '';
}

/** Human label for the catalog's normalized npu value ("required" | "optional"). */
export function npuLabel(npu: string): string {
  return npu === 'required' ? 'Required' : 'Optional';
}
