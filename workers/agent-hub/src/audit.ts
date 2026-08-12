// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The pre-publish security-audit gate for the skills lane.
 *
 * ── Division of labour with #2468 ──────────────────────────────────────────
 * This module is the ENFORCEMENT half of the gate and is complete: it parses
 * the audit report a publisher attaches, decides whether the claimed security
 * tier is allowed to publish without one, and turns the verdict into a publish
 * outcome (BLOCK -> rejected, REVIEW -> held, ALLOW -> recorded in the catalog).
 *
 * The ANALYSIS half — the engine that produces the report by scanning a skill's
 * code (`tools.py`, `scripts/`) and its instruction body for prompt injection —
 * lives in Python at `src/gaia/skills/audit/`. It runs publisher-side and in CI
 * as `gaia skill audit ./skill/` and POSTs its output as the `audit` form part.
 *
 * There is deliberately NO always-ALLOW default. A tier whose gate requires an
 * audit and arrives without one is REJECTED with an actionable error (see
 * {@link TIERS_REQUIRING_AUDIT}), never quietly waved through.
 *
 * ── The report is BOUND to what it audited ─────────────────────────────────
 * A verdict alone proves nothing about what earned it, so a report is checked
 * against the publish rather than trusted: the claimed tier must appear in
 * `cleared_tiers`, and `skill` / `version` / `manifest_digest` must match what is
 * being published (see {@link assertReportBinding}). Without those, an ALLOW
 * earned by an `experimental` audit would publish as `verified`, an old
 * version's report would publish a new version, and skill A's report would
 * publish skill B.
 *
 * ── What this gate is NOT ──────────────────────────────────────────────────
 * Those checks close REPLAY and ACCIDENT, not FORGERY. The report is
 * publisher-supplied and unsigned — nothing here verifies its provenance — so a
 * hostile publisher can fabricate one whose every field agrees, including a
 * digest over their own bytes. Making a verdict unforgeable needs an attestation
 * the publisher cannot mint (#1710 signing with a CI-held key, or the Worker
 * running the audit itself), not a stricter parse.
 *
 * That is why the stored record says `attestation: "publisher-asserted"`. Read
 * it as "self-consistent", never as "AMD vouches for this". Nothing in this file
 * may be described as a security boundary against a motivated publisher until an
 * unforgeable attestation exists.
 *
 * `content_digest` (the whole skill tree) is recorded but NOT recomputed here:
 * the tree arrives as a packaged archive this Worker does not unpack. Only
 * `manifest_digest` is verified — which is the surface that decides trust, since
 * `security_tier`, `permissions`, and `version` all live in `SKILL.md`.
 */

import { HttpError } from "./http";
import type { SkillAuditRecord } from "./types";

/**
 * The governance verdict vocabulary (`DecisionType` in
 * `src/gaia/governance/schemas.py`), reused verbatim so a skill audit reads the
 * same as every other GAIA decision.
 */
export type AuditVerdict = "ALLOW" | "REVIEW" | "BLOCK";

const VALID_VERDICTS = new Set<string>(["ALLOW", "REVIEW", "BLOCK"]);

/**
 * Tiers whose gate an automated audit MUST have cleared before publish, per the
 * tier-scaled rigor in #2468:
 *
 *   experimental — scan advisory; publishing without a report is allowed and is
 *                  recorded as `unaudited` (install still demands
 *                  `--allow-experimental`, so the user is never surprised).
 *   community    — automated scan required.
 *   verified     — automated scan required (the human/AMD audit builds on it).
 */
export const TIERS_REQUIRING_AUDIT = new Set(["community", "verified"]);

/** The parsed `audit` form part: the report `gaia skill audit` emits (#2468). */
export interface SkillAuditReport {
  verdict: AuditVerdict;
  /** Audit engine id + version, e.g. "gaia-skill-audit/0.1.0". */
  engine: string;
  /** ISO-8601 timestamp the report was produced. */
  audited_at: string;
  /** Non-blocking findings the scan surfaced (advisory detail lives in the report artifact). */
  findings?: unknown[];

  // ── Binding fields (#2468) ────────────────────────────────────────────────
  // A verdict alone proves nothing about WHAT earned it. These tie the report to
  // one skill, one version, one claimed tier, and one set of bytes, so a report
  // cannot be moved to a different publish. Optional in the type because a
  // pre-#2468 report predates them; {@link assertAuditGate} requires them for
  // any tier whose gate demands an audit.

  /** The skill name the audit ran against. */
  skill?: string;
  /** The version the audit ran against. */
  version?: string;
  /** The tier the audit ran for (the tier declared in the audited SKILL.md). */
  security_tier?: string;
  /** Every tier these findings actually clear. The enforceable tier claim. */
  cleared_tiers?: string[];
  /** sha256 over the audited skill tree. Recorded; not recomputable here (see below). */
  content_digest?: string;
  /** sha256 over the audited SKILL.md text — the digest this Worker CAN recompute. */
  manifest_digest?: string;
}

const AUDIT_DOC =
  "See https://github.com/amd/gaia/issues/2468 for the audit gate, and run " +
  "`gaia skill audit ./<skill>/` to produce the report.";

/**
 * Parse the optional `audit` form part. Returns null when absent (whether that
 * is acceptable is {@link assertAuditGate}'s call, not this function's). A
 * present-but-malformed report fails loudly — a publisher who tried to attach a
 * verdict and got the shape wrong must not silently fall through to "unaudited".
 */
export function parseAuditReport(text: string | null): SkillAuditReport | null {
  if (text == null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part is not valid JSON: ${(e as Error).message}. Expected ` +
        `{ "verdict": "ALLOW", "engine": "...", "audited_at": "..." }. ${AUDIT_DOC}`
    );
  }
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part must be a JSON object. ${AUDIT_DOC}`
    );
  }
  const r = parsed as Record<string, unknown>;
  if (typeof r.verdict !== "string" || !VALID_VERDICTS.has(r.verdict)) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part has verdict ${JSON.stringify(r.verdict)}; expected one of ` +
        `ALLOW, REVIEW, BLOCK. ${AUDIT_DOC}`
    );
  }
  if (typeof r.engine !== "string" || r.engine.trim() === "") {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part is missing 'engine' (the audit engine id + version that ` +
        `produced this verdict). An unattributed verdict is not accepted. ${AUDIT_DOC}`
    );
  }
  if (typeof r.audited_at !== "string" || Number.isNaN(Date.parse(r.audited_at))) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part is missing a valid ISO-8601 'audited_at' timestamp. ${AUDIT_DOC}`
    );
  }
  if (r.findings != null && !Array.isArray(r.findings)) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part's 'findings' must be a list when present. ${AUDIT_DOC}`
    );
  }
  if (r.cleared_tiers != null && !Array.isArray(r.cleared_tiers)) {
    throw new HttpError(
      400,
      "invalid_audit_report",
      `The 'audit' part's 'cleared_tiers' must be a list of tier names when ` +
        `present. ${AUDIT_DOC}`
    );
  }
  return {
    verdict: r.verdict as AuditVerdict,
    engine: r.engine,
    audited_at: r.audited_at,
    findings: (r.findings as unknown[]) ?? [],
    skill: typeof r.skill === "string" ? r.skill : undefined,
    version: typeof r.version === "string" ? r.version : undefined,
    security_tier:
      typeof r.security_tier === "string" ? r.security_tier : undefined,
    cleared_tiers: Array.isArray(r.cleared_tiers)
      ? (r.cleared_tiers as unknown[]).map(String)
      : undefined,
    content_digest:
      typeof r.content_digest === "string" ? r.content_digest : undefined,
    manifest_digest:
      typeof r.manifest_digest === "string" ? r.manifest_digest : undefined,
  };
}

/**
 * sha256 over the `SKILL.md` text, matching Python's
 * `gaia.skills.audit.manifest_digest`. CRLF and CR are normalized to LF so a
 * Windows checkout hashes the same bytes the author audited.
 *
 * Shared test vectors live in `tests/fixtures/skill_audit_digest/vectors.json`
 * and are asserted by both this Worker's suite and the Python suite — two
 * implementations of one hash verified only against their own mocks would prove
 * the call happened, not that they interoperate.
 */
export async function manifestDigest(skillMarkdown: string): Promise<string> {
  const normalized = skillMarkdown.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const bytes = new TextEncoder().encode(normalized);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `sha256:${hex}`;
}

/**
 * Gate a skill publish on its audit verdict and claimed tier, returning the
 * record to store in the catalog. Throws — publish is refused — when the skill
 * did not clear the gate for the tier it claims.
 *
 * @param report The parsed audit report, or null when none was attached.
 * @param securityTier The tier the *manifest* claims.
 * @param binding What the publish is actually for, so the report can be checked
 *   against it rather than trusted. Omit only for a caller that has no publish
 *   context (there is none in the Worker; the publish path always passes it).
 */
export function assertAuditGate(
  report: SkillAuditReport | null,
  securityTier: string,
  binding?: { skill: string; version: string; manifestDigest: string }
): SkillAuditRecord {
  if (report != null && binding != null) {
    assertReportBinding(report, securityTier, binding);
  }
  if (report == null) {
    if (TIERS_REQUIRING_AUDIT.has(securityTier)) {
      throw new HttpError(
        428,
        "audit_required",
        `Skill claims security_tier '${securityTier}', which cannot be published ` +
          `without a cleared security audit. Attach the report as the 'audit' form ` +
          `part, or publish as 'experimental' (advisory scan). ${AUDIT_DOC}`
      );
    }
    // Advisory tier, no report: recorded honestly as unaudited rather than
    // stamped ALLOW. Consumers can see the difference; install still gates it.
    return {
      verdict: "unaudited",
      engine: "",
      audited_at: "",
      findings: 0,
      attestation: "unaudited",
      cleared_tiers: [],
      content_digest: "",
      manifest_digest: "",
    };
  }

  if (report.verdict === "BLOCK") {
    throw new HttpError(
      403,
      "audit_blocked",
      `The security audit BLOCKED this skill (engine ${report.engine}, ` +
        `${report.findings?.length ?? 0} finding(s)). Fix the findings and re-audit; ` +
        `a blocked skill cannot enter the catalog. ${AUDIT_DOC}`
    );
  }

  if (report.verdict === "REVIEW") {
    // TODO(#2468): route REVIEW into a quarantine lane held out of the public
    // catalog pending maintainer sign-off. Until that lane exists, refusing is
    // the only honest option — admitting it would publish an unreviewed skill.
    throw new HttpError(
      409,
      "audit_review_required",
      `The security audit returned REVIEW for this skill (engine ${report.engine}, ` +
        `${report.findings?.length ?? 0} finding(s)); it needs maintainer sign-off ` +
        `before it can be published. ${AUDIT_DOC}`
    );
  }

  return {
    verdict: "ALLOW",
    engine: report.engine,
    audited_at: report.audited_at,
    findings: report.findings?.length ?? 0,
    // The verdict was ASSERTED by the publisher, not attested by a trusted
    // signer. Consumers must be able to tell the difference; see the module
    // header on why these checks close replay but not forgery (#1710).
    attestation: "publisher-asserted",
    cleared_tiers: report.cleared_tiers ?? [],
    content_digest: report.content_digest ?? "",
    manifest_digest: report.manifest_digest ?? "",
  };
}

/**
 * Check that the report describes the publish actually being made.
 *
 * Without these four checks `assertAuditGate` reads only `verdict`, so an ALLOW
 * earned as `experimental` for v1.0.0 of skill A would publish v1.1.0 of skill B
 * as `verified`. They close **replay and accident** — stale reports, forgotten
 * re-audits, careless tier bumps — which is where the real-world risk is.
 *
 * They do NOT close forgery: the report is a publisher-supplied form part with
 * no attestation, so a hostile publisher can fabricate one whose every field
 * agrees. That needs a signature minted by CI (#1710) or the Worker running the
 * audit itself. Until then the recorded verdict says `publisher-asserted`, and
 * nothing here may be described as proving a skill's tier is trustworthy — only
 * that it is self-consistent.
 */
function assertReportBinding(
  report: SkillAuditReport,
  securityTier: string,
  binding: { skill: string; version: string; manifestDigest: string }
): void {
  // For a tier whose gate demands an audit, the binding fields are REQUIRED.
  // Treating a missing field as "skip the check" would make omitting it the
  // bypass, which is the whole hole these checks exist to close.
  const gated = TIERS_REQUIRING_AUDIT.has(securityTier);
  if (gated) {
    const missing = (
      [
        ["cleared_tiers", report.cleared_tiers],
        ["skill", report.skill],
        ["version", report.version],
        ["manifest_digest", report.manifest_digest],
      ] as const
    )
      .filter(([, value]) => value == null)
      .map(([field]) => field);
    if (missing.length > 0) {
      throw new HttpError(
        428,
        "audit_required",
        `Skill claims security_tier '${securityTier}', which requires an audit ` +
          `report bound to what it audited, but the report is missing: ` +
          `${missing.join(", ")}. An unbound verdict could have been earned by ` +
          `any skill, version, or tier. Re-run \`gaia skill audit\` with a ` +
          `current GAIA. ${AUDIT_DOC}`
      );
    }
  }

  // 1. The claimed tier must be one the findings actually cleared.
  if (gated) {
    const cleared = report.cleared_tiers as string[];
    if (!cleared.includes(securityTier)) {
      const best = cleared.length > 0 ? cleared[cleared.length - 1] : "none";
      throw new HttpError(
        403,
        "audit_tier_not_cleared",
        `Skill claims security_tier '${securityTier}', but its audit cleared ` +
          `only '${best}'. A skill cannot be stamped a tier whose gate it did ` +
          `not clear: fix the findings and re-audit, or publish as '${best}'. ` +
          `${AUDIT_DOC}`
      );
    }
  }

  // 2. The report must be for THIS skill.
  if (report.skill != null && report.skill !== binding.skill) {
    throw new HttpError(
      400,
      "audit_skill_mismatch",
      `The audit report is for skill '${report.skill}' but this publish is for ` +
        `'${binding.skill}'. Audit the skill you are publishing. ${AUDIT_DOC}`
    );
  }

  // 3. The report must be for THIS version — a new version re-earns its verdict.
  if (report.version != null && report.version !== binding.version) {
    throw new HttpError(
      428,
      "audit_stale",
      `The audit report is for version ${report.version} but this publish is ` +
        `${binding.version}. Every version re-earns its verdict: re-run ` +
        `\`gaia skill audit\` against the version you are publishing. ${AUDIT_DOC}`
    );
  }

  // 4. The report must be for THESE bytes. Only the SKILL.md digest is checked:
  // the rest of the skill arrives as a packaged archive this Worker does not
  // unpack, so `content_digest` is recorded rather than recomputed. SKILL.md is
  // where security_tier, permissions, and version live, so it is the surface
  // that decides trust.
  if (
    report.manifest_digest != null &&
    report.manifest_digest !== binding.manifestDigest
  ) {
    throw new HttpError(
      400,
      "audit_digest_mismatch",
      `The audit report was produced for a different SKILL.md ` +
        `(report ${report.manifest_digest}, upload ${binding.manifestDigest}). ` +
        `Editing the manifest after auditing invalidates the verdict — ` +
        `re-run \`gaia skill audit\` on what you are publishing. ${AUDIT_DOC}`
    );
  }
}
