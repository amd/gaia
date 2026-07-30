// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Parse + validate an uploaded `SKILL.md` for the skills lane (#2467).
 *
 * A skill's manifest is the YAML front matter of its `SKILL.md`: the Agent
 * Skills standard base (`name`, `description`, `license`) plus GAIA's
 * `version` superset and the `metadata.gaia` namespace. The grammar is
 * `docs/plans/skill-format.mdx`; the canonical loader-side validator is
 * `src/gaia/skills/` (#888). This is the publish gatekeeper: it rejects a
 * malformed skill loudly rather than admitting it to the catalog.
 */

import { parse as parseYaml } from "yaml";

import { HttpError } from "./http";
import type {
  ParsedSkillManifest,
  SkillHardware,
  SkillRequirements,
  SkillToolDecl,
} from "./types";

// Agent Skills naming rule: lowercase alphanumeric with single internal
// hyphens, no leading/trailing/consecutive hyphen. Length is checked
// separately so an over-long name gets its own actionable message.
const SKILL_NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const MAX_NAME_LEN = 64;
const MAX_DESCRIPTION_LEN = 1024;

const SEMVER_RE =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;

/**
 * The unversioned sentinel. `skill-format` reserves `0.0.0` for a skill that
 * has not been versioned yet and states it blocks publishing, so the publish
 * path must reject it rather than mint an immutable `0.0.0` in the catalog.
 */
const UNVERSIONED = "0.0.0";

const VALID_SECURITY_TIERS = new Set(["verified", "community", "experimental"]);
const DEFAULT_SECURITY_TIER = "experimental";

const SPEC_URL = "https://amd-gaia.ai/docs/plans/skill-format";

function bad(message: string): never {
  throw new HttpError(400, "invalid_skill_manifest", `${message} See ${SPEC_URL}.`);
}

function nonEmptyStr(v: unknown): v is string {
  return typeof v === "string" && v.trim() !== "";
}

function strList(raw: unknown, field: string): string[] {
  if (raw == null) return [];
  if (!Array.isArray(raw) || !raw.every((x) => typeof x === "string")) {
    bad(`SKILL.md: ${field} must be a list of strings.`);
  }
  return raw as string[];
}

function optStr(raw: unknown, field: string): string {
  if (raw == null) return "";
  if (typeof raw !== "string") bad(`SKILL.md: ${field} must be a string.`);
  return raw as string;
}

function asMapping(raw: unknown, field: string): Record<string, unknown> {
  if (typeof raw !== "object" || raw == null || Array.isArray(raw)) {
    bad(`SKILL.md: ${field} must be a mapping.`);
  }
  return raw as Record<string, unknown>;
}

/**
 * Split a `SKILL.md` into its YAML front matter and Markdown body. The front
 * matter delimiter must be the very first line — a `SKILL.md` without one has
 * no manifest at all, which is a hard error, not an empty default.
 */
export function splitFrontMatter(text: string): { frontMatter: string; body: string } {
  // Tolerate CRLF (a Windows-authored SKILL.md) and a UTF-8 BOM.
  const normalized = text.replace(/^﻿/, "").replace(/\r\n/g, "\n");
  const match = /^---\n([\s\S]*?)\n---\n?/.exec(normalized);
  if (!match) {
    bad(
      "SKILL.md has no YAML front matter. The file must open with a '---' line, " +
        "the manifest (name, description, version, metadata.gaia), and a closing '---'."
    );
  }
  return {
    frontMatter: match[1],
    body: normalized.slice(match[0].length).replace(/^\n+/, ""),
  };
}

function parseSkillRequirements(raw: unknown): SkillRequirements {
  const defaults: SkillRequirements = {
    model: "",
    context: "",
    python: "",
    dependencies: [],
    node_dependencies: [],
    env_vars: [],
    hardware: { npu: "", gpu_vram: "" },
  };
  if (raw == null) return defaults;
  const r = asMapping(raw, "metadata.gaia.requirements");

  let hardware: SkillHardware = { npu: "", gpu_vram: "" };
  if (r.hardware != null) {
    const h = asMapping(r.hardware, "metadata.gaia.requirements.hardware");
    hardware = {
      npu: optStr(h.npu, "metadata.gaia.requirements.hardware.npu"),
      gpu_vram: optStr(h.gpu_vram, "metadata.gaia.requirements.hardware.gpu_vram"),
    };
  }

  return {
    model: optStr(r.model, "metadata.gaia.requirements.model"),
    context: optStr(r.context, "metadata.gaia.requirements.context"),
    python: optStr(r.python, "metadata.gaia.requirements.python"),
    dependencies: strList(r.dependencies, "metadata.gaia.requirements.dependencies"),
    node_dependencies: strList(
      r.node_dependencies,
      "metadata.gaia.requirements.node_dependencies"
    ),
    env_vars: strList(r.env_vars, "metadata.gaia.requirements.env_vars"),
    hardware,
  };
}

/**
 * Parse `metadata.gaia.tools` — the `@tool` functions the skill PROVIDES. Only
 * `name` + `description` are lifted into the catalog; the full typed signature
 * stays in the stored SKILL.md, which the installer reads.
 */
function parseSkillTools(raw: unknown): SkillToolDecl[] {
  if (raw == null) return [];
  if (!Array.isArray(raw)) bad("SKILL.md: metadata.gaia.tools must be a list.");
  return (raw as unknown[]).map((entry, i) => {
    const t = asMapping(entry, `metadata.gaia.tools[${i}]`);
    if (!nonEmptyStr(t.name)) {
      bad(`SKILL.md: metadata.gaia.tools[${i}].name is required and must be a non-empty string.`);
    }
    return {
      name: t.name as string,
      description: optStr(t.description, `metadata.gaia.tools[${i}].description`),
    };
  });
}

/**
 * Parse a full `SKILL.md` (front matter + body) into a validated manifest plus
 * its Markdown body, or throw HttpError(400) naming the offending field.
 */
export function parseSkillManifest(skillMarkdown: string): {
  manifest: ParsedSkillManifest;
  body: string;
} {
  const { frontMatter, body } = splitFrontMatter(skillMarkdown);

  let data: unknown;
  try {
    data = parseYaml(frontMatter);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_skill_manifest",
      `SKILL.md front matter is not valid YAML: ${(e as Error).message}. See ${SPEC_URL}.`
    );
  }
  if (data == null || typeof data !== "object" || Array.isArray(data)) {
    bad("SKILL.md front matter must be a YAML mapping (key: value).");
  }
  const d = data as Record<string, unknown>;

  const missing = ["name", "description"].filter((k) => !nonEmptyStr(d[k]));
  if (missing.length) {
    bad(`SKILL.md is missing required field(s): ${missing.join(", ")}.`);
  }

  const name = d.name as string;
  if (name.length > MAX_NAME_LEN) {
    bad(`SKILL.md: name is ${name.length} characters, over the ${MAX_NAME_LEN}-character limit.`);
  }
  if (!SKILL_NAME_RE.test(name)) {
    bad(
      `SKILL.md: name ${JSON.stringify(name)} is invalid. Use lowercase letters, ` +
        `digits, and single internal hyphens (e.g. 'web-research') — no leading, ` +
        `trailing, or consecutive hyphens. It must equal the skill's directory name.`
    );
  }

  const description = d.description as string;
  if (description.length > MAX_DESCRIPTION_LEN) {
    bad(
      `SKILL.md: description is ${description.length} characters, over the ` +
        `${MAX_DESCRIPTION_LEN}-character limit.`
    );
  }

  // `version` is optional in the format (an unpublished skill may omit it) but
  // REQUIRED to publish — the catalog is versioned and immutable per version.
  if (!nonEmptyStr(d.version)) {
    bad(
      "SKILL.md: version is required to publish. Add a top-level SemVer " +
        "`version:` (e.g. '0.1.0') to the front matter."
    );
  }
  const version = d.version as string;
  if (!SEMVER_RE.test(version)) {
    bad(
      `SKILL.md: version ${JSON.stringify(version)} is not valid SemVer ` +
        `(MAJOR.MINOR.PATCH, e.g. '0.1.0').`
    );
  }
  if (version === UNVERSIONED) {
    bad(
      `SKILL.md: version ${UNVERSIONED} is the reserved "unversioned" sentinel and ` +
        `cannot be published. Bump to a real SemVer (e.g. '0.1.0') first.`
    );
  }

  const metadata = d.metadata == null ? {} : asMapping(d.metadata, "metadata");
  const gaia = metadata.gaia == null ? {} : asMapping(metadata.gaia, "metadata.gaia");

  const securityTier = (gaia.security_tier as string) ?? DEFAULT_SECURITY_TIER;
  if (!VALID_SECURITY_TIERS.has(securityTier)) {
    bad(
      `SKILL.md: metadata.gaia.security_tier ${JSON.stringify(securityTier)} is ` +
        `invalid. Use one of: ${[...VALID_SECURITY_TIERS].sort().join(", ")}.`
    );
  }

  return {
    manifest: {
      name,
      version,
      description,
      license: optStr(d.license, "license"),
      security_tier: securityTier,
      permissions: strList(gaia.permissions, "metadata.gaia.permissions"),
      tools: parseSkillTools(gaia.tools),
      tools_required: strList(gaia.tools_required, "metadata.gaia.tools_required"),
      requirements: parseSkillRequirements(gaia.requirements),
    },
    body,
  };
}
