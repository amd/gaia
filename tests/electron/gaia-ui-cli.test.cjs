// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// `gaia-ui --help` must point an RC user at the RC channel and print the pip
// spelling of the backend version, or following it downgrades them.

const path = require("path");
const { execFileSync } = require("child_process");

const CLI = path.join(__dirname, "../../src/gaia/apps/webui/bin/gaia-ui.cjs");
const { npmDistTag, helpText } = require(CLI);

describe("gaia-ui help for release candidates", () => {
  test("a prerelease points at the next dist-tag, a final at latest", () => {
    expect(npmDistTag("0.25.0-rc.2")).toBe("next");
    expect(npmDistTag("0.25.0")).toBe("latest");
  });

  test("an RC's help prints @next and the PEP 440 pip pin", () => {
    const text = helpText("0.25.0-rc.2");
    expect(text).toContain("npm install -g @amd-gaia/agent-ui@next");
    expect(text).toContain("amd-gaia[ui]==0.25.0rc2");
    expect(text).not.toContain("@latest");
    expect(text).not.toContain("0.25.0-rc.2)");
  });

  test("a final release's help is unchanged", () => {
    const text = helpText("0.25.0");
    expect(text).toContain("npm install -g @amd-gaia/agent-ui@latest");
    expect(text).toContain("amd-gaia[ui]==0.25.0)");
  });

  test("requiring the CLI does not start it, running it still does", () => {
    const out = execFileSync(process.execPath, [CLI, "--help"], {
      encoding: "utf8",
    });
    expect(out).toContain("Usage: gaia-ui [options]");
  });
});
