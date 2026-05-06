// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Structural smoke asserts for the built GAIA Agent UI macOS DMG (issue #941).
// Driven by `node --test tests/electron/dmg-smoke.test.mjs`.
//
// Requires the environment variable GAIA_DMG to point at an already-built
// DMG. In CI this is set after actions/download-artifact. Locally:
//   cd src/gaia/apps/webui && npm run package:mac
//   GAIA_DMG=$(ls src/gaia/apps/webui/dist-app/*.dmg) \
//     node --test tests/electron/dmg-smoke.test.mjs
//
// Acceptance-criteria mapping (issue #941):
//   - AC3 / T5: bundled mac-arm64 uv binary is present, executable, has the
//              SHA256 declared in BUNDLED_UV_SHA256, AND `uv --version` runs.
//              The exec check transitively validates DMG mode-bit
//              preservation — if HFS+/APFS dropped the executable bit,
//              execFileSync fails with EACCES.
//
// Implementation note — node:test scheduling vs. live mounts:
//   Top-level `test()` calls register tests SYNCHRONOUSLY but the runner
//   executes their bodies AFTER the entire module has finished loading.
//   That makes `try { test(...) } finally { hdiutil detach }` a footgun:
//   the detach fires before the test body runs, leaving a broken mount path.
//   To keep the mount alive for the duration of all assertions, the
//   mount-dependent block is wrapped in a single async top-level test that
//   uses subtests via `t.test()`. The `finally { detach() }` then runs only
//   after every subtest completes.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import {
  assertUvBinary,
  backendInstallerPath,
  bundledUvPath,
} from "./_helpers/installer-smoke.mjs";

const DMG = process.env.GAIA_DMG;
const PLATFORM_KEY = "mac-arm64";

if (!DMG) {
  test("dmg-smoke SKIP: GAIA_DMG is not set", (t) => {
    t.skip(
      "GAIA_DMG env var is unset — this test needs a built DMG path. " +
        "In CI it is set after download-artifact. Locally: " +
        "GAIA_DMG=$(ls src/gaia/apps/webui/dist-app/*.dmg) " +
        "node --test tests/electron/dmg-smoke.test.mjs",
    );
  });
} else if (process.platform !== "darwin") {
  // hdiutil is macOS-only. A Linux-runner mistake should fail loudly with
  // a skip message rather than try to attach the DMG.
  test("dmg-smoke SKIP: not running on darwin", (t) => {
    t.skip(
      `DMG smoke requires hdiutil; current platform is ${process.platform}. ` +
        `Run this test on a macOS host (CI uses macos-latest).`,
    );
  });
} else {
  test("AC3/T5: DMG structural smoke (mac-arm64)", async (t) => {
    const dmgPath = path.resolve(DMG);
    assert.ok(fs.existsSync(dmgPath), `GAIA_DMG=${dmgPath} does not exist`);

    const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-dmg-"));
    const mountpoint = path.join(workdir, "mnt");
    fs.mkdirSync(mountpoint, { recursive: true });

    const attachResult = spawnSync(
      "hdiutil",
      [
        "attach",
        "-nobrowse",
        "-readonly",
        "-mountpoint",
        mountpoint,
        dmgPath,
      ],
      { stdio: ["ignore", "ignore", "pipe"], encoding: "utf8" },
    );
    assert.equal(
      attachResult.status,
      0,
      `hdiutil attach exit=${attachResult.status}\n` +
        `stderr:\n${attachResult.stderr}`,
    );

    // Cleanup MUST run regardless of subtest outcome — a left-mounted
    // volume causes "Resource busy" failures on the next CI job that
    // shares the runner. -force is mandatory: Spotlight can briefly
    // hold a busy mount even after the test finishes. Errors are
    // logged but NEVER thrown — surfacing detach failures must not
    // mask earlier assertion failures from the subtests.
    try {
      // ── Locate the .app bundle inside the mounted DMG ───────────────
      // hdiutil attach returns synchronously when the volume is
      // registered, but the kernel VFS layer can lag 1–2 ticks before
      // readdir sees the contents. Without this loop, an unguarded
      // path.join(mountpoint, undefined, …) throws an unactionable
      // TypeError. 5×100ms is empirically enough on macos-latest.
      let entries = [];
      let appEntry;
      for (let i = 0; i < 5; i++) {
        entries = fs.readdirSync(mountpoint);
        appEntry = entries.find((n) => n.endsWith(".app"));
        if (appEntry) break;
        await sleep(100);
      }

      await t.test("AC3/T5a: DMG contains a .app bundle", () => {
        assert.ok(
          appEntry,
          `no .app bundle found inside DMG at ${mountpoint}; entries: ${entries.join(", ")}`,
        );
      });

      if (appEntry) {
        const resourcesDir = path.join(
          mountpoint,
          appEntry,
          "Contents",
          "Resources",
        );
        const uvPath = bundledUvPath(resourcesDir, PLATFORM_KEY);
        const installerPath = backendInstallerPath(import.meta.url);

        // ── AC3/T5b: existence + mode bit + SHA256 against pin ────────
        await t.test(
          "AC3/T5b: bundled mac-arm64 uv is present, executable, sha-pinned",
          () => {
            assertUvBinary(uvPath, PLATFORM_KEY, installerPath);
          },
        );

        // ── AC3/T5c: `uv --version` actually runs ─────────────────────
        // Catches Gatekeeper-quarantine and stale-code-sign failure
        // modes that pure existence/SHA checks miss. Also transitively
        // validates DMG mode-bit preservation: if APFS dropped the
        // executable bit, execFileSync fails with EACCES.
        await t.test("AC3/T5c: bundled mac-arm64 uv --version runs", () => {
          const out = execFileSync(uvPath, ["--version"], {
            timeout: 10_000,
            encoding: "utf8",
          });
          assert.match(
            out,
            /^uv \d+\.\d+\.\d+/,
            `unexpected uv --version output: ${out}`,
          );
        });
      }
    } finally {
      const r = spawnSync(
        "hdiutil",
        ["detach", "-force", mountpoint],
        { stdio: ["ignore", "ignore", "pipe"], encoding: "utf8" },
      );
      if (r.status !== 0) {
        // eslint-disable-next-line no-console
        console.error(
          `[dmg-smoke] hdiutil detach exit=${r.status}\n` +
            `stderr:\n${r.stderr}`,
        );
      }
      try {
        fs.rmSync(workdir, { recursive: true, force: true });
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error(`[dmg-smoke] workdir cleanup failed: ${e.message}`);
      }
    }
  });
}
