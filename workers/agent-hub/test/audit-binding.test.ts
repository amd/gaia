// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The audit-report binding checks (#2468).
 *
 * Without these, `assertAuditGate` reads only `verdict`, so one ALLOW could be
 * replayed onto any publish: a different tier, a different version, a different
 * skill. Each test here moves exactly one binding and asserts the publish is
 * refused — and, critically, that **nothing was written to R2**, since a gate
 * that rejects after writing has already leaked the artifact.
 *
 * These close replay and accident, not forgery: `allowAudit` can mint a report
 * claiming any tier, which is the documented limit of an unsigned,
 * publisher-supplied report (see `src/audit.ts`).
 */

import { describe, expect, it } from "vitest";

import { manifestDigest } from "../src/audit";
import worker from "../src/index";
import { allowAudit, makeEnv, sampleSkill, skillPublishRequest } from "./fake-r2";

type Env = ReturnType<typeof makeEnv>;

const TOKEN = "tok_amd";
const ARTIFACT = { artifact: "zip-bytes", filename: "web-research-0.1.0.zip" };

async function publish(env: Env, skillMarkdown: string, audit?: string) {
  return worker.fetch(
    skillPublishRequest({ token: TOKEN, skillMarkdown, audit, ...ARTIFACT }),
    env as never
  );
}

interface ErrorBody {
  code?: string;
  message?: string;
}

/** Parse the `{ error: { code, message } }` envelope once — a body reads once. */
async function errorBody(res: Response): Promise<ErrorBody> {
  const payload = (await res.json()) as { error?: ErrorBody };
  return payload.error ?? {};
}

/** Nothing at all may be written when the gate refuses. */
async function assertNothingWritten(env: Env) {
  const listed = await env.bucket.list({ prefix: "skills/" });
  expect(listed.objects.map((o: { key: string }) => o.key)).toEqual([]);
  expect(await env.bucket.head("index.json")).toBeNull();
}

const COMMUNITY = sampleSkill({ security_tier: "community" });

describe("audit report binding", () => {
  it("accepts a correctly bound report", async () => {
    const env = makeEnv();
    const res = await publish(env, COMMUNITY, await allowAudit(COMMUNITY));
    expect(res.status).toBe(201);
  });

  // ── 1. tier ────────────────────────────────────────────────────────────────

  it("refuses a tier the audit did not clear", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { clearedTiers: ["experimental"] })
    );
    expect(res.status).toBe(403);
    expect((await errorBody(res)).code).toBe("audit_tier_not_cleared");
    await assertNothingWritten(env);
  });

  it("names the tier that was actually cleared, so the fix is obvious", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { clearedTiers: ["experimental"] })
    );
    const body = await errorBody(res);
    expect(body.message).toContain("community");
    expect(body.message).toContain("experimental");
  });

  it("refuses a claim that cleared nothing at all", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { clearedTiers: [] })
    );
    expect(res.status).toBe(403);
    expect((await errorBody(res)).message).toContain("none");
  });

  it("refuses the experimental-audit-then-claim-verified replay", async () => {
    // The concrete attack: audit as experimental (advisory, easy ALLOW), then
    // edit the manifest to claim verified and attach the same report.
    const env = makeEnv();
    const experimental = sampleSkill({ security_tier: "experimental" });
    const report = await allowAudit(experimental);
    const verified = sampleSkill({ security_tier: "verified" });

    const res = await publish(env, verified, report);
    expect(res.status).toBeGreaterThanOrEqual(400);
    await assertNothingWritten(env);
  });

  // ── 2. skill identity ──────────────────────────────────────────────────────

  it("refuses a report for a different skill", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { skill: "some-other-skill" })
    );
    expect(res.status).toBe(400);
    expect((await errorBody(res)).code).toBe("audit_skill_mismatch");
    await assertNothingWritten(env);
  });

  // ── 3. version staleness ───────────────────────────────────────────────────

  it("refuses a report for a different version", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { version: "0.0.9" })
    );
    expect(res.status).toBe(428);
    expect((await errorBody(res)).code).toBe("audit_stale");
    await assertNothingWritten(env);
  });

  it("makes a version bump re-earn its verdict", async () => {
    // v0.1.0 publishes fine; the same report cannot carry v0.2.0.
    const env = makeEnv();
    const v1 = sampleSkill({ security_tier: "community", version: "0.1.0" });
    expect((await publish(env, v1, await allowAudit(v1))).status).toBe(201);

    const v2 = sampleSkill({ security_tier: "community", version: "0.2.0" });
    const stale = await allowAudit(v1); // the v0.1.0 report
    const res = await worker.fetch(
      skillPublishRequest({
        token: TOKEN,
        skillMarkdown: v2,
        audit: stale,
        artifact: "zip-bytes",
        filename: "web-research-0.2.0.zip",
      }),
      env as never
    );
    expect(res.status).toBe(428);
    expect((await errorBody(res)).code).toBe("audit_stale");
  });

  // ── 4. manifest bytes ──────────────────────────────────────────────────────

  it("refuses a report produced for a different SKILL.md", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { manifestDigest: `sha256:${"11".repeat(32)}` })
    );
    expect(res.status).toBe(400);
    expect((await errorBody(res)).code).toBe("audit_digest_mismatch");
    await assertNothingWritten(env);
  });

  it("catches a manifest edited after the audit ran", async () => {
    // Same name, version and tier — only the permissions were widened after
    // auditing. The digest is what notices.
    const env = makeEnv();
    const audited = sampleSkill({ security_tier: "community" });
    const report = await allowAudit(audited);
    const edited = audited.replace(
      "network:read:*.brave.com",
      "network:write:*"
    );
    expect(edited).not.toBe(audited);

    const res = await publish(env, edited, report);
    expect(res.status).toBe(400);
    expect((await errorBody(res)).code).toBe("audit_digest_mismatch");
    await assertNothingWritten(env);
  });

  // ── Missing bindings are refused, not skipped ──────────────────────────────

  it.each(["cleared_tiers", "skill", "version", "manifest_digest"])(
    "refuses a gated tier whose report omits %s",
    async (field) => {
      const env = makeEnv();
      const res = await publish(
        env,
        COMMUNITY,
        await allowAudit(COMMUNITY, { omit: [field] })
      );
      expect(res.status).toBe(428);
      const body = await errorBody(res);
      expect(body.code).toBe("audit_required");
      expect(body.message).toContain(field);
    }
  );

  it("tolerates an unbound report at the advisory tier", async () => {
    // experimental does not require an audit at all, so a partial voluntary
    // report must not be worse than attaching none.
    const env = makeEnv();
    const experimental = sampleSkill({ security_tier: "experimental" });
    const res = await publish(
      env,
      experimental,
      await allowAudit(experimental, {
        omit: ["cleared_tiers", "skill", "version", "manifest_digest"],
      })
    );
    expect(res.status).toBe(201);
  });

  // ── The verdict still governs ──────────────────────────────────────────────

  it("still rejects BLOCK even when every binding is correct", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { verdict: "BLOCK" })
    );
    expect(res.status).toBe(403);
    expect((await errorBody(res)).code).toBe("audit_blocked");
    await assertNothingWritten(env);
  });

  it("still holds REVIEW even when every binding is correct", async () => {
    const env = makeEnv();
    const res = await publish(
      env,
      COMMUNITY,
      await allowAudit(COMMUNITY, { verdict: "REVIEW" })
    );
    expect(res.status).toBe(409);
    expect((await errorBody(res)).code).toBe("audit_review_required");
    await assertNothingWritten(env);
  });

  // ── Honesty of the record ──────────────────────────────────────────────────

  it("records the verdict as publisher-asserted, not attested", async () => {
    const env = makeEnv();
    await publish(env, COMMUNITY, await allowAudit(COMMUNITY));
    const index = (await (
      await worker.fetch(new Request("https://hub.amd-gaia.ai/index.json"), env as never)
    ).json()) as { agents: { skill_metadata?: { audit: Record<string, unknown> } }[] };
    const audit = index.agents[0].skill_metadata!.audit;
    expect(audit.attestation).toBe("publisher-asserted");
    expect(audit.manifest_digest).toBe(await manifestDigest(COMMUNITY));
  });
});
