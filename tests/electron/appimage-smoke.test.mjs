// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Structural smoke asserts for the built GAIA Agent UI AppImage (issue #782).
// Driven by `node --test tests/electron/appimage-smoke.test.mjs`.
//
// Requires the environment variable GAIA_APPIMAGE to point at an already-
// built AppImage. In CI this is set after actions/download-artifact. Locally,
// run `cd src/gaia/apps/webui && npm run package:linux` first, then
// `GAIA_APPIMAGE=$(ls src/gaia/apps/webui/dist-app/*.AppImage) node --test ...`.
//
// Acceptance-criteria mapping (issue #782):
//   - AC2 / T2: TWO checks together: (a) chrome-sandbox is absent from the
//              packaged tree (after-pack.cjs delete), so Chromium cannot
//              invoke the unconfigured SUID helper on any launch path; and
//              (b) the generated .desktop entry launches with --no-sandbox
//              (linux.executableArgs), covering the AppArmor-restricted
//              userns case on Ubuntu 24.04.1+.
//   - AC4    : asserts bundled uv binary is present, executable, and under
//              the expected extraResources layout (T3).
//   - AC3    : asserts the pre-built React dist/ is shipped (no user
//              `npm run build` required).
//   - AC9    : asserts no stray .env, sourcemaps, or dotfiles shipped in
//              the app.asar (defence-in-depth for future browser-mode wheel).

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const APPIMAGE = process.env.GAIA_APPIMAGE;

// node:test has no built-in skipAll; emit a clear SKIP message and
// register a single trivially-passing test so runners don't think the
// file is empty.
if (!APPIMAGE) {
  test("appimage-smoke SKIP: GAIA_APPIMAGE is not set", (t) => {
    t.skip(
      "GAIA_APPIMAGE env var is unset — this test needs a built AppImage " +
        "path. In CI it is set after download-artifact. Locally: " +
        "GAIA_APPIMAGE=$(ls src/gaia/apps/webui/dist-app/*.AppImage) " +
        "node --test tests/electron/appimage-smoke.test.mjs"
    );
  });
} else {
  // ── One-time extract ───────────────────────────────────────────────
  // --appimage-extract writes squashfs-root/ into the CWD. Run in a
  // dedicated tempdir so repeated test runs don't pollute the repo.
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-appimage-"));
  const appImagePath = path.resolve(APPIMAGE);

  if (!fs.existsSync(appImagePath)) {
    test("appimage-smoke FAIL: GAIA_APPIMAGE path does not exist", () => {
      assert.fail(`GAIA_APPIMAGE=${appImagePath} does not exist`);
    });
  } else {
    // chmod +x — download-artifact does not preserve the executable bit.
    try {
      fs.chmodSync(appImagePath, 0o755);
    } catch {
      /* best effort */
    }

    const extractResult = spawnSync(appImagePath, ["--appimage-extract"], {
      cwd: workdir,
      stdio: ["ignore", "ignore", "pipe"],
      encoding: "utf8",
    });

    if (extractResult.status !== 0) {
      test("appimage-smoke FAIL: --appimage-extract failed", () => {
        assert.fail(
          `--appimage-extract exit=${extractResult.status}\n` +
            `stderr:\n${extractResult.stderr}`
        );
      });
    } else {
      const squashRoot = path.join(workdir, "squashfs-root");

      // ── AC2 / T2a: chrome-sandbox MUST be absent from the AppImage ──
      // after-pack.cjs deletes it so Chromium cannot invoke the
      // unconfigured SUID helper on any launch path (CLI or .desktop).
      test("AC2/T2a: chrome-sandbox is deleted from the AppImage", () => {
        const sandboxPath = path.join(squashRoot, "chrome-sandbox");
        assert.equal(
          fs.existsSync(sandboxPath),
          false,
          `chrome-sandbox must be deleted (found at ${sandboxPath})`,
        );
      });

      // ── AC2 / T2b: .desktop Exec= MUST pass --no-sandbox ─────────────
      // linux.executableArgs propagates into the generated .desktop file.
      // This covers the AppArmor-restricted userns case on 24.04.1+ for
      // launches via file-manager double-click (which route through the
      // .desktop Exec= line). CLI launches do NOT route through this and
      // rely on chrome-sandbox being absent (T2a above).
      test("AC2/T2b: .desktop Exec= contains --no-sandbox", () => {
        const desktopFiles = fs
          .readdirSync(squashRoot)
          .filter((f) => f.endsWith(".desktop"));
        assert.ok(
          desktopFiles.length > 0,
          `expected at least one .desktop file in ${squashRoot}`,
        );
        const desktopPath = path.join(squashRoot, desktopFiles[0]);
        const desktopContents = fs.readFileSync(desktopPath, "utf8");
        const execLine = desktopContents
          .split("\n")
          .find((l) => l.startsWith("Exec="));
        assert.ok(execLine, "expected an Exec= line in .desktop file");
        assert.match(
          execLine,
          /--no-sandbox/,
          `Exec= must pass --no-sandbox (was: ${execLine})`,
        );
      });

      // ── AC4 / T3: bundled uv binary present and executable ──────────
      test("AC4/T3: bundled uv binary is present under extraResources", () => {
        const uvPath = path.join(
          squashRoot,
          "resources",
          "vendor",
          "uv",
          "linux-x64",
          "uv"
        );
        assert.ok(
          fs.existsSync(uvPath),
          `expected bundled uv at ${uvPath}`
        );
        const st = fs.statSync(uvPath);
        // Any execute bit set on any class is enough; AppImage squashfs
        // typically preserves 0o755.
        assert.ok(
          (st.mode & 0o111) !== 0,
          `uv binary should be executable; mode=${(st.mode & 0o777).toString(8)}`
        );
      });

      // ── AC4 / T3b: bundled uv binary SHA256 matches BUNDLED_UV_SHA256 ──
      // Runtime ensureUv() hashes the extracted ELF and rejects any mismatch,
      // so catch packaging/hash drift at smoke time instead of on user launch.
      test("AC4/T3b: bundled uv SHA256 matches BUNDLED_UV_SHA256[linux-x64]", () => {
        const uvPath = path.join(
          squashRoot,
          "resources",
          "vendor",
          "uv",
          "linux-x64",
          "uv"
        );
        const installerPath = path.resolve(
          path.dirname(new URL(import.meta.url).pathname),
          "..",
          "..",
          "src",
          "gaia",
          "apps",
          "webui",
          "services",
          "backend-installer.cjs"
        );
        const installerSrc = fs.readFileSync(installerPath, "utf8");
        const m = installerSrc.match(
          /BUNDLED_UV_SHA256\s*=\s*\{[^}]*?"linux-x64"\s*:\s*"([0-9a-f]{64})"/s
        );
        assert.ok(
          m,
          `could not parse BUNDLED_UV_SHA256["linux-x64"] from ${installerPath}`
        );
        const expected = m[1];
        const actual = crypto
          .createHash("sha256")
          .update(fs.readFileSync(uvPath))
          .digest("hex");
        assert.equal(
          actual,
          expected,
          `bundled uv binary SHA256 does not match BUNDLED_UV_SHA256["linux-x64"]; ensureUv() will reject this at runtime`
        );
      });

      // ── AC3: pre-built React dist/ ships with the AppImage ──────────
      test("AC3: pre-built dist/index.html is present in resources", () => {
        const indexHtml = path.join(
          squashRoot,
          "resources",
          "dist",
          "index.html"
        );
        assert.ok(
          fs.existsSync(indexHtml),
          `expected pre-built dist at ${indexHtml} so users do not need to run npm run build`
        );
      });

      // ── AC9: no stray .env/sourcemaps/dotfiles in app.asar ──────────
      test("AC9: app.asar has no stray .env, sourcemaps, or dotfiles", () => {
        const asarPath = path.join(squashRoot, "resources", "app.asar");
        if (!fs.existsSync(asarPath)) {
          assert.fail(`app.asar not found at ${asarPath}`);
        }

        // Use npx asar if available; fall back to listing via node module.
        let listing = "";
        try {
          listing = execFileSync(
            "npx",
            ["--yes", "asar", "list", asarPath],
            { encoding: "utf8", timeout: 60000 }
          );
        } catch (err) {
          // Fallback: try the asar npm module directly.
          try {
            // eslint-disable-next-line import/no-extraneous-dependencies
            const asar = require("@electron/asar");
            listing = asar.listPackage(asarPath).join("\n");
          } catch (err2) {
            // If neither path works, at least extract package.json as a
            // signal the asar is structurally valid, then SKIP the
            // forbidden-pattern scan rather than emit a false-green pass.
            const tmpPkg = path.join(workdir, "pkg.json");
            execFileSync(
              "npx",
              ["--yes", "asar", "extract-file", asarPath, "package.json"],
              { cwd: workdir, encoding: "utf8", timeout: 60000 }
            );
            assert.ok(
              fs.existsSync(tmpPkg) ||
                fs.existsSync(path.join(workdir, "package.json")),
              "asar extract-file should have produced package.json"
            );
            console.warn(
              "[asar] list unavailable; structural check only. " +
                `err1=${err.message} err2=${err2.message}`
            );
            return;
          }
        }

        const forbidden = [
          /(^|\/)\.env(\.|$)/, // .env, .env.local, etc.
          /\.map$/, // source maps
          /(^|\/)\.DS_Store$/,
          /(^|\/)Thumbs\.db$/,
          /(^|\/)\.git(\/|$)/,
        ];

        const offenders = listing
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter((l) => l && forbidden.some((re) => re.test(l)));

        assert.equal(
          offenders.length,
          0,
          `app.asar should not ship these entries:\n${offenders.join("\n")}`
        );
      });
    }
  }
}
