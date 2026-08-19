// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * SKILL.md grammar, parsed directly (#2467).
 *
 * `skill-publish.test.ts` drives the same validator over HTTP and covers the
 * top-level fields. This file covers what a multipart round-trip cannot reach
 * or would only reach one branch at a time: the typed `metadata.gaia.*`
 * sub-structures, and the encoding tolerances (the form decoder normalizes CRLF
 * before the validator ever sees it, so only a direct call exercises that path).
 */

import { describe, expect, it } from "vitest";

import { HttpError } from "../src/http";
import { parseSkillManifest } from "../src/skill-manifest";
import { sampleSkill } from "./fake-r2";

/** Build a SKILL.md whose `metadata.gaia` block is the given indented YAML. */
function withGaia(gaiaBlock: string[]): string {
  return sampleSkill({
    frontMatter: [
      "name: web-research",
      "description: Search the web",
      "version: 0.1.0",
      "metadata:",
      "  gaia:",
      ...gaiaBlock,
    ].join("\n"),
  });
}

/** Assert the parse fails as a 400 invalid_skill_manifest naming `field`. */
function expectRejected(markdown: string, field: string): void {
  let thrown: unknown;
  try {
    parseSkillManifest(markdown);
  } catch (e) {
    thrown = e;
  }
  expect(thrown).toBeInstanceOf(HttpError);
  const err = thrown as HttpError;
  expect(err.status).toBe(400);
  expect(err.code).toBe("invalid_skill_manifest");
  expect(err.message).toContain(field);
}

describe("parseSkillManifest — encoding tolerance", () => {
  it("parses a CRLF-authored SKILL.md identically to the LF one", () => {
    const lf = sampleSkill();
    const { manifest, body } = parseSkillManifest(lf.replace(/\n/g, "\r\n"));

    expect(manifest).toEqual(parseSkillManifest(lf).manifest);
    // A stray \r would ship into the catalog's rendered body.
    expect(body).not.toContain("\r");
    expect(body).toBe(parseSkillManifest(lf).body);
  });

  it("strips a UTF-8 BOM before the opening --- delimiter", () => {
    // Without the strip the `^---` match fails and the whole file is rejected
    // as "no front matter" — a Windows-authored SKILL.md would be unpublishable.
    const { manifest } = parseSkillManifest(`﻿${sampleSkill()}`);
    expect(manifest.name).toBe("web-research");
    expect(manifest.version).toBe("0.1.0");
  });

  it("returns the body with the front matter and its trailing blank lines removed", () => {
    const { body } = parseSkillManifest(sampleSkill());
    expect(body).toBe("# Web Research\n\nSearch the web, then summarise.\n");
    expect(body).not.toContain("security_tier");
  });
});

describe("parseSkillManifest — malformed YAML", () => {
  it("rejects front matter that is not parseable YAML", () => {
    // Unclosed flow mapping: a syntax error, not a shape error, so it must be
    // reported as such rather than falling through to a missing-field message.
    const markdown = sampleSkill({
      frontMatter: "name: web-research\ndescription: [unclosed\nversion: 0.1.0",
    });
    expectRejected(markdown, "not valid YAML");
  });
});

describe("parseSkillManifest — metadata.gaia type violations", () => {
  // Every one of these is a distinct `bad()` branch that only a direct call
  // reaches. Each must name the offending field path so the publisher can fix
  // it without guessing.
  const cases: Array<[string, string, string]> = [
    [
      "metadata that is not a mapping",
      sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "metadata: nope",
        ].join("\n"),
      }),
      "metadata must be a mapping",
    ],
    [
      "metadata.gaia that is not a mapping",
      sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "metadata:",
          "  gaia: nope",
        ].join("\n"),
      }),
      "metadata.gaia must be a mapping",
    ],
    ["tools that is not a list", withGaia(["    tools: nope"]), "metadata.gaia.tools must be a list"],
    [
      "a tools entry that is not a mapping",
      withGaia(["    tools:", "      - nope"]),
      "metadata.gaia.tools[0]",
    ],
    [
      "a tools entry whose description is not a string",
      withGaia(["    tools:", "      - name: search_web", "        description: [a, b]"]),
      "metadata.gaia.tools[0].description",
    ],
    [
      "tools_required that is not a list of strings",
      withGaia(["    tools_required: [1, 2]"]),
      "metadata.gaia.tools_required",
    ],
    [
      "requirements that is not a mapping",
      withGaia(["    requirements: nope"]),
      "metadata.gaia.requirements must be a mapping",
    ],
    [
      "requirements.hardware that is not a mapping",
      withGaia(["    requirements:", "      hardware: nope"]),
      "metadata.gaia.requirements.hardware",
    ],
    [
      "requirements.model that is not a string",
      withGaia(["    requirements:", "      model: [a]"]),
      "metadata.gaia.requirements.model",
    ],
    [
      "requirements.dependencies that is not a list",
      withGaia(["    requirements:", "      dependencies: requests>=2.31"]),
      "metadata.gaia.requirements.dependencies",
    ],
    [
      "requirements.env_vars that is not a list of strings",
      withGaia(["    requirements:", "      env_vars: [1]"]),
      "metadata.gaia.requirements.env_vars",
    ],
    [
      "a license that is not a string",
      sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "license: [MIT]",
        ].join("\n"),
      }),
      "license must be a string",
    ],
  ];

  it.each(cases)("rejects %s", (_label, markdown, field) => {
    expectRejected(markdown, field);
  });
});

describe("parseSkillManifest — instruction-only defaults", () => {
  it("emits the full zeroed requirements shape when metadata.gaia is absent", () => {
    // Consumers read `requirements.*` unconditionally, so every key must be
    // present even for a skill that declares none of them.
    const { manifest } = parseSkillManifest(sampleSkill({ omitGaia: true }));
    expect(manifest.requirements).toEqual({
      model: "",
      context: "",
      python: "",
      dependencies: [],
      node_dependencies: [],
      env_vars: [],
      hardware: { npu: "", gpu_vram: "" },
    });
    expect(manifest.tools).toEqual([]);
    expect(manifest.tools_required).toEqual([]);
    expect(manifest.permissions).toEqual([]);
    // Off-state safe floor: the least-privileged tier, never a guess upward.
    expect(manifest.security_tier).toBe("experimental");
  });

  it("defaults an empty metadata.gaia block to the same floor", () => {
    const { manifest } = parseSkillManifest(
      sampleSkill({
        frontMatter: [
          "name: web-research",
          "description: Search the web",
          "version: 0.1.0",
          "metadata:",
          "  gaia: {}",
        ].join("\n"),
      })
    );
    expect(manifest.security_tier).toBe("experimental");
    expect(manifest.tools).toEqual([]);
    expect(manifest.requirements.hardware).toEqual({ npu: "", gpu_vram: "" });
  });
});
