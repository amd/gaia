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
// `markdown` is the skills lane: an instruction-only skill ships no code.
export type AgentLanguage = 'python' | 'cpp' | 'go' | 'typescript' | 'markdown';

// What a catalog entry IS. Agents are the default; the terminal hub publishes
// as a component, the Agent UI as an app, and marketplace skills as `skill`
// (#2467) — so a listing that shows only agents must filter on this rather than
// assume every entry is one.
export type PackageType = 'agent' | 'app' | 'component' | 'skill';

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
  // Optional so the site keeps building against an index.json served before the
  // Worker that emits `type` is deployed. Read it through packageType().
  type?: PackageType;
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
  // EVALUATION.md (evaluation guide) markdown of the latest version, rendered as its
  // own doc tab. "" / absent if none was published. Optional for the same
  // older-index.json resilience as `spec`/`skill`.
  evaluation?: string;
  // CAPABILITY_MATRIX.md markdown of the latest version, rendered as its own doc
  // tab. "" / absent if none was published. Optional for the same older-index.json
  // resilience as `spec`/`skill`.
  capability_matrix?: string;
  // Eval-scorecard markdown (SCORECARD.md body, YAML front matter already stripped
  // by the hub Worker), rendered as its own doc tab. "" / absent if none was
  // published. Same older-index.json resilience as `spec`/`skill`.
  scorecard?: string;
  // Public URL of the eval scorecard markdown for the latest version (the canonical
  // source, with anchors/relative links intact); absent when none was published.
  eval_scorecard_url?: string;
  // Aggregate eval score (0–100) parsed from the scorecard front matter; absent
  // when none was published or parseable. Drives the sidebar score badge.
  eval_score?: number;
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
  // Skill-lane fields from SKILL.md's metadata.gaia namespace (#2467). Present
  // ONLY on `type: 'skill'` entries — read it through a `type` check, never
  // unconditionally. `security_tier` and `permissions` stay top-level because
  // they mean the same thing in both lanes.
  skill_metadata?: SkillMetadata;
}

export interface SkillToolDecl {
  name: string;
  description: string;
}

// metadata.gaia.requirements — unrelated to the agent-lane AgentRequirements.
export interface SkillRequirements {
  model: string;
  context: string;
  python: string;
  dependencies: string[];
  node_dependencies: string[];
  env_vars: string[];
  hardware: { npu: string; gpu_vram: string };
}

export interface SkillMetadata {
  // @tool functions the skill PROVIDES.
  tools: SkillToolDecl[];
  // Registry tool names the skill CONSUMES (the recipe contract).
  tools_required: string[];
  requirements: SkillRequirements;
  // Pre-publish security-audit result (#2468); `unaudited` when the skill's
  // tier made the scan advisory and none was supplied.
  audit: {
    verdict: 'ALLOW' | 'unaudited';
    engine: string;
    audited_at: string;
    findings: number;
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

/**
 * Every catalog entry, all lanes, sorted: verified first, then alphabetical.
 * Use this when you genuinely want everything (e.g. generating a page per
 * published package); use getAgentPackages()/getSkills() to render one lane.
 */
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

/**
 * The installable-package lanes (agents, apps, components) — everything the
 * agent listing has always shown. Excludes skills, which install through
 * `gaia skill install` and get their own lane (#2467).
 */
export async function getAgentPackages(): Promise<Agent[]> {
  return (await getCatalog()).filter((a) => !isSkill(a));
}

/** The marketplace Skills lane (#2467). */
export async function getSkills(): Promise<Agent[]> {
  return (await getCatalog()).filter(isSkill);
}

/** A single catalog entry by id (any lane), or undefined if not found. */
export async function getAgent(id: string): Promise<Agent | undefined> {
  const { agents } = await loadCatalog();
  return agents.find((a) => a.id === id);
}

// ---- Display helpers ----

// Every category any hub/agents manifest declares. A missing entry falls through
// to the raw slug, which renders as a lowercase odd-one-out next to the labelled
// pills — so add the label here when a manifest introduces a new category.
const CATEGORY_LABELS: Record<string, string> = {
  conversation: 'Conversation',
  development: 'Development',
  productivity: 'Productivity',
  integrations: 'Integrations',
  creative: 'Creative',
  vision: 'Vision',
  research: 'Research',
  infrastructure: 'Infrastructure',
  healthcare: 'Healthcare',
  examples: 'Examples',
  skills: 'Skills',
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

const LANGUAGE_LABELS: Record<AgentLanguage, string> = {
  python: 'Python',
  cpp: 'C++',
  go: 'Go',
  typescript: 'TypeScript',
  markdown: 'Markdown',
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
/** An entry's package type, defaulting to 'agent' as the manifest schema does. */
export function packageType(agent: Agent): PackageType {
  return agent.type ?? 'agent';
}

/** True for the entries that ARE agents — excludes the Agent UI and terminal hub. */
export function isAgent(agent: Agent): boolean {
  return packageType(agent) === 'agent';
}

/** True for marketplace skills (#2467) — a different lane and a different installer. */
export function isSkill(agent: Agent): boolean {
  return packageType(agent) === 'skill';
}

export function installMethods(agent: Agent): InstallMethod[] {
  // A skill is not an agent package: it installs into ~/.gaia/skills/ and is
  // composed by any agent, so `gaia agent install` would not work for it.
  if (isSkill(agent)) {
    return [
      {
        key: 'skill',
        label: 'GAIA',
        command: `gaia skill install ${agent.id}`,
        note:
          agent.security_tier === 'experimental'
            ? 'Experimental — installing requires --allow-experimental.'
            : 'Installs into ~/.gaia/skills/ and can be composed by any agent.',
      },
    ];
  }

  // A component/app is not installed *into* GAIA and has no PyPI wheel — it is
  // downloaded per platform. `gaia agent install <id>` would not work for it.
  // An npm package, where one exists, is a real second path (the Agent UI
  // ships `gaia-ui` globally), so offer it alongside rather than instead.
  if (!isAgent(agent)) {
    const methods: InstallMethod[] = [
      {
        key: 'download',
        label: 'Download',
        command: '',
        note: 'Download the build for your platform from the release below.',
      },
    ];
    if (agent.npm_package) {
      methods.push({
        key: 'npm',
        label: 'npm',
        command: `npm install -g ${agent.npm_package}`,
        note: 'Global CLI install from npm.',
      });
    }
    return methods;
  }

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
