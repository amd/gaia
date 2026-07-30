// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The Worker half of the cross-language digest contract (#2468).
 *
 * `tests/unit/test_skills_audit_digest_vectors.py` asserts against the same
 * `vectors.json`. Two implementations of one hash is exactly the case where each
 * side is verified only against its own mock — which proves the function was
 * called, never that Python and TypeScript agree byte for byte. If this file and
 * its Python counterpart both pass, they interoperate.
 *
 * See `tests/fixtures/skill_audit_digest/README.md` before changing a digest.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { manifestDigest } from "../src/audit";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, "..", "..", "..", "tests", "fixtures", "skill_audit_digest");

interface Vectors {
  engine: string;
  manifest_digest: {
    skill_md_lf: string;
    skill_md_crlf_normalizes_to_the_same: string;
    empty_string: string;
  };
  content_digest: { tree: string };
}

const vectors: Vectors = JSON.parse(
  readFileSync(join(FIXTURES, "vectors.json"), "utf8")
);

describe("manifestDigest cross-language vectors", () => {
  it("matches the shared vector for the fixture SKILL.md", async () => {
    const text = readFileSync(join(FIXTURES, "tree", "SKILL.md"), "utf8");
    expect(await manifestDigest(text)).toBe(vectors.manifest_digest.skill_md_lf);
  });

  it("normalizes CRLF to LF, so a Windows checkout agrees", async () => {
    const crlf = readFileSync(join(FIXTURES, "skill_md_crlf.txt"), "utf8");
    expect(crlf).toContain("\r\n");
    expect(await manifestDigest(crlf)).toBe(
      vectors.manifest_digest.skill_md_crlf_normalizes_to_the_same
    );
    expect(await manifestDigest(crlf)).toBe(vectors.manifest_digest.skill_md_lf);
  });

  it("pins the algorithm: the empty string is plain sha256", async () => {
    expect(vectors.manifest_digest.empty_string).toBe(
      "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    expect(await manifestDigest("")).toBe(vectors.manifest_digest.empty_string);
  });

  it("produces a different digest for different content", async () => {
    // Guards against the vectors passing for a degenerate reason.
    const text = readFileSync(join(FIXTURES, "tree", "SKILL.md"), "utf8");
    expect(await manifestDigest(text + "\n")).not.toBe(
      vectors.manifest_digest.skill_md_lf
    );
  });

  it("names the engine version the vectors were produced by", () => {
    // A digest change is a wire-format change; the engine version must move.
    expect(vectors.engine).toBe("gaia-skill-audit/0.1.0");
  });
});
