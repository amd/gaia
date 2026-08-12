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
 * TODO(#2468): the ANALYSIS half — the engine that produces the report by
 * scanning a skill's code (`tools.py`, `scripts/`) and its instruction body for
 * prompt injection — is issue #2468 and does NOT live here. It runs
 * publisher-side / in CI as `gaia skill audit ./skill/` and POSTs its output as
 * the `audit` form part. When it lands, nothing in this file changes shape: it
 * already consumes the `GovernanceDecision`-style verdict that issue emits.
 *
 * There is deliberately NO always-ALLOW default. A tier whose gate requires an
 * audit and arrives without one is REJECTED with an actionable error (see
 * {@link TIERS_REQUIRING_AUDIT}), never quietly waved through.
 *
 * ── Known gap: the report is not yet BOUND to what it audited ──────────────
 * TODO(#2468): a report is accepted on shape + verdict alone. Nothing checks
 * that it was produced for THIS skill, THIS version, or THIS tier, so an ALLOW
 * earned by an `experimental` audit can be replayed onto a `verified` publish,
 * an old version's report onto a new version, or one skill's onto another. The
 * binding fields (`skill`, `version`, `cleared_tiers`, `content_digest`) and
 * their checks land with the audit engine.
 *
 * Those checks close REPLAY, not FORGERY. The report is publisher-supplied and
 * unsigned — nothing here verifies its provenance — so a hostile publisher can
 * still fabricate one whose digest matches their own bytes. Making the verdict
 * unforgeable needs an attestation the publisher cannot mint (#1710 signing, a
 * CI-held key, or the Worker running the audit itself), not a stricter parse.
 * Until then this gate is a guard against mistakes and stale reports, and must
 * not be described as a security boundary against a motivated publisher.
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
  return {
    verdict: r.verdict as AuditVerdict,
    engine: r.engine,
    audited_at: r.audited_at,
    findings: (r.findings as unknown[]) ?? [],
  };
}

/**
 * Gate a skill publish on its audit verdict and claimed tier, returning the
 * record to store in the catalog. Throws — publish is refused — when the skill
 * did not clear the gate for the tier it claims.
 */
export function assertAuditGate(
  report: SkillAuditReport | null,
  securityTier: string
): SkillAuditRecord {
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
    return { verdict: "unaudited", engine: "", audited_at: "", findings: 0 };
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
  };
}
