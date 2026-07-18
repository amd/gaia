// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Parse + validate an uploaded `gaia-agent.yaml`.
 *
 * This mirrors the rules in `src/gaia/hub/manifest.py` (the canonical
 * validator) for the fields the Worker needs to gate a publish and build the
 * catalog. It is a gatekeeper, not a replacement: invalid input is rejected
 * loudly with an actionable message rather than silently coerced.
 */

import { parse as parseYaml } from "yaml";

import { HttpError } from "./http";
import type { Interfaces, ParsedManifest, Requirements } from "./types";

// Mirror of the regexes/vocabulary in src/gaia/hub/manifest.py.
const ID_RE = /^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$/;
const SEMVER_RE =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;

const VALID_LANGUAGES = new Set(["python", "cpp"]);
const VALID_SECURITY_TIERS = new Set(["verified", "community", "experimental"]);
const DEFAULT_SECURITY_TIER = "experimental";
// Multi-component discriminator (#1716). Defaults to "agent" so existing
// agent-only manifests keep validating unchanged.
const VALID_TYPES = new Set(["agent", "app", "component"]);
const DEFAULT_TYPE = "agent";
const VALID_PLATFORMS = new Set([
  "win-x64",
  "win-arm64",
  "linux-x64",
  "linux-arm64",
  "darwin-x64",
  "darwin-arm64",
]);

const SPEC_URL = "https://amd-gaia.ai/docs/spec/agent-hub-restructure";

function bad(message: string): never {
  throw new HttpError(400, "invalid_manifest", `${message} See ${SPEC_URL}.`);
}

function nonEmptyStr(v: unknown): v is string {
  return typeof v === "string" && v.trim() !== "";
}

function strList(raw: unknown, field: string): string[] {
  if (raw == null) return [];
  if (!Array.isArray(raw) || !raw.every((x) => typeof x === "string")) {
    bad(`gaia-agent.yaml: ${field} must be a list of strings.`);
  }
  return raw as string[];
}

function optNumber(raw: unknown, field: string): number | undefined {
  if (raw == null) return undefined;
  if (typeof raw !== "number" || Number.isNaN(raw) || raw < 0) {
    bad(`gaia-agent.yaml: ${field} must be a number >= 0.`);
  }
  return raw;
}

function optBool(raw: unknown, field: string): boolean | undefined {
  if (raw == null) return undefined;
  if (typeof raw !== "boolean") bad(`gaia-agent.yaml: ${field} must be true or false.`);
  return raw;
}

function optInt(raw: unknown, field: string): number | undefined {
  if (raw == null) return undefined;
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0) {
    bad(`gaia-agent.yaml: ${field} must be an integer >= 0.`);
  }
  return raw;
}

function optStr(raw: unknown, field: string): string | undefined {
  if (raw == null) return undefined;
  if (typeof raw !== "string") bad(`gaia-agent.yaml: ${field} must be a string.`);
  return raw;
}

function parseRequirements(raw: unknown): Requirements {
  const defaults: Requirements = {
    platforms: [],
    min_memory_gb: 0,
    min_disk_gb: 0,
    min_context_size: 0,
    npu: false,
    gpu_vram_gb: 0,
  };
  if (raw == null) return defaults;
  if (typeof raw !== "object" || Array.isArray(raw)) {
    bad("gaia-agent.yaml: requirements must be a mapping.");
  }
  const r = raw as Record<string, unknown>;
  const platforms = strList(r.platforms, "requirements.platforms");
  const unknown = platforms.filter((p) => !VALID_PLATFORMS.has(p));
  if (unknown.length) {
    bad(
      `gaia-agent.yaml: requirements.platforms has unknown platform(s) ${JSON.stringify(
        unknown
      )}. Valid: ${[...VALID_PLATFORMS].sort().join(", ")}.`
    );
  }
  return {
    platforms,
    min_memory_gb: optNumber(r.min_memory_gb, "requirements.min_memory_gb") ?? 0,
    min_disk_gb: optNumber(r.min_disk_gb, "requirements.min_disk_gb") ?? 0,
    min_context_size: optNumber(r.min_context_size, "requirements.min_context_size") ?? 0,
    npu: optBool(r.npu, "requirements.npu") ?? false,
    gpu_vram_gb: optNumber(r.gpu_vram_gb, "requirements.gpu_vram_gb") ?? 0,
  };
}

function parseInterfaces(raw: unknown): Interfaces {
  if (raw == null) return {};
  if (typeof raw !== "object" || Array.isArray(raw)) {
    bad("gaia-agent.yaml: interfaces must be a mapping.");
  }
  const i = raw as Record<string, unknown>;
  const valid = ["tui", "cli", "pipe", "api_server", "mcp_server"];
  const unknown = Object.keys(i).filter((k) => !valid.includes(k));
  if (unknown.length) {
    bad(`gaia-agent.yaml: interfaces has unknown key(s) ${unknown.sort().join(", ")}.`);
  }
  return {
    tui: optBool(i.tui, "interfaces.tui"),
    cli: optBool(i.cli, "interfaces.cli"),
    pipe: optBool(i.pipe, "interfaces.pipe"),
    api_server: optBool(i.api_server, "interfaces.api_server"),
    mcp_server: optBool(i.mcp_server, "interfaces.mcp_server"),
  };
}

/** Parse raw YAML text into a validated manifest, or throw HttpError(400). */
export function parseManifest(yamlText: string): ParsedManifest {
  let data: unknown;
  try {
    data = parseYaml(yamlText);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_manifest",
      `gaia-agent.yaml is not valid YAML: ${(e as Error).message}. See ${SPEC_URL}.`
    );
  }

  if (data == null || typeof data !== "object" || Array.isArray(data)) {
    bad("gaia-agent.yaml must be a YAML mapping (key: value).");
  }
  const d = data as Record<string, unknown>;

  const required = ["id", "name", "version", "description", "author", "license", "language"];
  const missing = required.filter((k) => !nonEmptyStr(d[k]));
  if (missing.length) {
    bad(`gaia-agent.yaml is missing required field(s): ${missing.join(", ")}.`);
  }

  const id = d.id as string;
  if (!ID_RE.test(id)) {
    bad(
      `gaia-agent.yaml: id ${JSON.stringify(id)} is invalid. Use 1-52 lowercase ` +
        `alphanumeric characters and internal hyphens (e.g. 'my-agent').`
    );
  }

  const version = d.version as string;
  if (!SEMVER_RE.test(version)) {
    bad(
      `gaia-agent.yaml: version ${JSON.stringify(version)} is not valid SemVer ` +
        `(MAJOR.MINOR.PATCH, e.g. '0.1.0').`
    );
  }

  const language = d.language as string;
  if (!VALID_LANGUAGES.has(language)) {
    bad(
      `gaia-agent.yaml: language ${JSON.stringify(language)} is not supported. ` +
        `Use one of: ${[...VALID_LANGUAGES].sort().join(", ")}.`
    );
  }

  const pkgType = (d.type as string) ?? DEFAULT_TYPE;
  if (!VALID_TYPES.has(pkgType)) {
    bad(
      `gaia-agent.yaml: type ${JSON.stringify(pkgType)} is not a valid package type. ` +
        `Use one of: ${[...VALID_TYPES].sort().join(", ")}, or omit it to default to 'agent'.`
    );
  }

  const securityTier = (d.security_tier as string) ?? DEFAULT_SECURITY_TIER;
  if (!VALID_SECURITY_TIERS.has(securityTier)) {
    bad(
      `gaia-agent.yaml: security_tier ${JSON.stringify(securityTier)} is invalid. ` +
        `Use one of: ${[...VALID_SECURITY_TIERS].sort().join(", ")}.`
    );
  }

  if (d.min_gaia_version != null && !SEMVER_RE.test(d.min_gaia_version as string)) {
    bad(`gaia-agent.yaml: min_gaia_version ${JSON.stringify(d.min_gaia_version)} is not valid SemVer.`);
  }

  return {
    id,
    name: d.name as string,
    version,
    description: d.description as string,
    author: d.author as string,
    license: d.license as string,
    language,
    type: pkgType,
    category: nonEmptyStr(d.category) ? (d.category as string) : "general",
    tags: strList(d.tags, "tags"),
    icon: nonEmptyStr(d.icon) ? (d.icon as string) : "",
    security_tier: securityTier,
    min_gaia_version: (d.min_gaia_version as string) ?? undefined,
    models: strList(d.models, "models"),
    tools_count: optInt(d.tools_count, "tools_count") ?? 0,
    permissions: strList(d.permissions, "permissions"),
    requirements: parseRequirements(d.requirements),
    interfaces: parseInterfaces(d.interfaces),
    deprecated: optBool(d.deprecated, "deprecated") ?? false,
    deprecation_message: optStr(d.deprecation_message, "deprecation_message"),
    npm_package: optStr(d.npm_package, "npm_package"),
    playground_url: optStr(d.playground_url, "playground_url"),
  };
}

/**
 * Compare two SemVer strings. Returns >0 if a is newer, <0 if older, 0 equal.
 * Release versions outrank pre-releases of the same core (1.0.0 > 1.0.0-rc.1).
 */
export function compareSemver(a: string, b: string): number {
  const pa = splitSemver(a);
  const pb = splitSemver(b);
  for (let i = 0; i < 3; i++) {
    if (pa.core[i] !== pb.core[i]) return pa.core[i] - pb.core[i];
  }
  // Equal core: a version without a pre-release is greater than one with.
  if (!pa.pre.length && pb.pre.length) return 1;
  if (pa.pre.length && !pb.pre.length) return -1;
  const len = Math.max(pa.pre.length, pb.pre.length);
  for (let i = 0; i < len; i++) {
    const x = pa.pre[i];
    const y = pb.pre[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const xn = /^\d+$/.test(x);
    const yn = /^\d+$/.test(y);
    if (xn && yn) {
      const diff = Number(x) - Number(y);
      if (diff !== 0) return diff;
    } else if (xn !== yn) {
      // Numeric identifiers always have lower precedence than alphanumeric.
      return xn ? -1 : 1;
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return 0;
}

function splitSemver(v: string): { core: [number, number, number]; pre: string[] } {
  const [coreAndPre] = v.split("+"); // drop build metadata
  const [core, pre] = coreAndPre.split("-");
  const [major, minor, patch] = core.split(".").map((n) => Number(n));
  return { core: [major, minor, patch], pre: pre ? pre.split(".") : [] };
}
