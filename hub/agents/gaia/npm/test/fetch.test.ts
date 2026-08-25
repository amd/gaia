// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The integrity gate: SHA-256 verify passes, tampered downloads fail, placeholder
 * hashes are blocked, and the outgoing request is the URL we claim it is.
 *
 * Mocks prove "we called it", not "the call is valid" (CLAUDE.md) — so the fake
 * fetch asserts the *shape* of the request (exact URL = baseUrl + "/" + filename),
 * not merely that something was fetched.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { IntegrityError, PlatformError } from "../src/errors.js";
import {
  INSTALLED_SENTINEL_NAME,
  fetchAll,
  fetchBinary,
  verifySha256,
} from "../src/fetch.js";
import {
  SIDECAR_BASE,
  SIDECAR_VERSION,
  TUI_BASE,
  makeLock,
  writeLockFile,
} from "./helpers.js";

const SIDECAR_BYTES = Buffer.from("#!/fake-frozen-gaia-agent\n");
const TUI_BYTES = Buffer.from("#!/fake-gaia-tui\n");
const sha = (b: Buffer): string => crypto.createHash("sha256").update(b).digest("hex");
const SIDECAR_SHA = sha(SIDECAR_BYTES);
const TUI_SHA = sha(TUI_BYTES);

let tmp: string;
const urls: string[] = [];

/** A fetch stub that records every requested URL and serves per-artifact bytes. */
function recordingFetch(
  bodies: Record<string, Buffer>,
  status = 200,
): typeof fetch {
  return (async (url: string) => {
    urls.push(String(url));
    const name = String(url).split("/").pop() ?? "";
    const body = bodies[name];
    if (status !== 200 || body === undefined) {
      return new Response(null, { status: status === 200 ? 404 : status });
    }
    return new Response(new Uint8Array(body), { status: 200 });
  }) as unknown as typeof fetch;
}

/** A lock whose per-component SHAs are the real hashes of the fake artifacts. */
async function realShaLock(): Promise<string> {
  const lock = makeLock();
  for (const e of Object.values(lock.components["sidecar"]!.platforms)) e.sha256 = SIDECAR_SHA;
  for (const e of Object.values(lock.components["tui"]!.platforms)) e.sha256 = TUI_SHA;
  return writeLockFile(tmp, lock);
}

// Keyed by the artifact name each LANE publishes: ours for the sidecar,
// terminal-hub's (`gaia-<platform>`) for the TUI.
const BODIES = {
  "gaia-agent-linux-x64": SIDECAR_BYTES,
  "gaia-linux-x64": TUI_BYTES,
};

beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "gaia-fetch-"));
  urls.length = 0;
});
afterEach(async () => {
  await fsp.rm(tmp, { recursive: true, force: true });
});

describe("verifySha256", () => {
  it("returns the hash when it matches", () => {
    expect(verifySha256(SIDECAR_BYTES, SIDECAR_SHA, "x")).toBe(SIDECAR_SHA);
  });

  it("is case-insensitive on the expected hash", () => {
    expect(verifySha256(SIDECAR_BYTES, SIDECAR_SHA.toUpperCase(), "x")).toBe(SIDECAR_SHA);
  });

  it("throws IntegrityError naming expected vs actual when it does not match", () => {
    try {
      verifySha256(SIDECAR_BYTES, "deadbeef", "sidecar linux-x64");
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(IntegrityError);
      expect((e as Error).message).toContain("deadbeef");
      expect((e as Error).message).toContain(SIDECAR_SHA);
    }
  });
});

describe("fetchBinary", () => {
  it("downloads, verifies, writes and (POSIX) chmods the sidecar", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(res.cached).toBe(false);
    expect(res.sha256).toBe(SIDECAR_SHA);
    expect(res.binaryPath).toBe(path.join(outDir, "gaia-agent"));
    expect(fs.readFileSync(res.binaryPath)).toEqual(SIDECAR_BYTES);
    if (process.platform !== "win32") {
      expect(fs.statSync(res.binaryPath).mode & 0o100).toBe(0o100);
    }
  });

  it("requests exactly the component's own baseUrl + '/' + filename", async () => {
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    // The call SHAPE, not just that fetch happened: a wrong join (double slash,
    // missing segment, wrong artifact name) is a 404 against the real hub. The
    // TUI must come from the terminal-hub lane at ITS version, never ours.
    expect(urls).toEqual([`${TUI_BASE}/gaia-linux-x64`]);
    expect(urls[0]).not.toContain("/agents/gaia/");
  });

  it("still fetches the sidecar from the gaia lane", async () => {
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "sidecar",
      outDir: path.join(tmp, "sidecar"),
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(urls).toEqual([`${SIDECAR_BASE}/gaia-agent-linux-x64`]);
  });

  it("honours a --base-url override with a trailing slash without doubling it", async () => {
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "linux-x64",
      lockPath,
      baseUrl: "https://mirror.test/gaia/0.1.0/",
      fetchImpl: recordingFetch(BODIES),
    });
    expect(urls).toEqual(["https://mirror.test/gaia/0.1.0/gaia-linux-x64"]);
  });

  it("resolves the TUI to terminal-hub's win-x64 artifact on a win32-x64 host", async () => {
    const lockPath = await realShaLock();
    // The win32↔win rename is the one thing that 404s if it is wrong, on the
    // most common host we ship to. Serving ONLY the terminal-hub name proves the
    // request used it — the old `gaia-tui-win32-x64.exe` would 404 here.
    const res = await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "win32-x64",
      lockPath,
      fetchImpl: recordingFetch({ "gaia-win-x64.exe": TUI_BYTES }),
    });
    expect(urls).toEqual([`${TUI_BASE}/gaia-win-x64.exe`]);
    expect(res.sha256).toBe(TUI_SHA);
    // Installed under OUR name: a cached file called `gaia` would shadow the shim.
    expect(path.basename(res.binaryPath)).toBe("gaia-tui.exe");
  });

  it("FAILS LOUDLY (IntegrityError) on a tampered download and leaves nothing behind", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    const tampered = Buffer.concat([SIDECAR_BYTES, Buffer.from("EVIL")]);
    await expect(
      fetchBinary({
        component: "sidecar",
        outDir,
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch({ "gaia-agent-linux-x64": tampered }),
      }),
    ).rejects.toBeInstanceOf(IntegrityError);
    expect(fs.existsSync(path.join(outDir, "gaia-agent"))).toBe(false);
  });

  it("BLOCKS the fetch when the lock still holds a placeholder hash", async () => {
    // This is the shipped state of binaries.lock.json until CI regenerates it.
    const lockPath = await writeLockFile(tmp, makeLock("PENDING-replace-with-real-sha256"));
    for (const component of ["sidecar", "tui"] as const) {
      const p = fetchBinary({
        component,
        outDir: path.join(tmp, component),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      });
      await expect(p).rejects.toBeInstanceOf(PlatformError);
      await expect(p).rejects.toThrow(/placeholder sha256/);
    }
    // Blocked BEFORE any network call — an unverifiable artifact is never fetched.
    expect(urls).toEqual([]);
  });

  it("blocks an all-zero placeholder hash too", async () => {
    const lockPath = await writeLockFile(tmp, makeLock("0".repeat(64)));
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });

  it("fails loudly when the SIDECAR has no build for an arm64 Linux host", async () => {
    const lockPath = await realShaLock();
    // The TUI resolves on linux-arm64 ...
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-arm64",
        lockPath,
        fetchImpl: recordingFetch({ "gaia-linux-arm64": TUI_BYTES }),
      }),
    ).resolves.toMatchObject({ platformKey: "linux-arm64" });
    // ... the sidecar does not, and must say so by name.
    const p = fetchBinary({
      component: "sidecar",
      outDir: path.join(tmp, "sidecar"),
      platformKey: "linux-arm64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    await expect(p).rejects.toBeInstanceOf(PlatformError);
    await expect(p).rejects.toThrow(/linux-arm64/);
  });

  it("reuses a cached binary whose hash already matches", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    // A fetch that would THROW proves the cache short-circuits before the network.
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: (async () => {
        throw new Error("should not download on cache hit");
      }) as unknown as typeof fetch,
    });
    expect(res.cached).toBe(true);
  });

  it("re-downloads a cached binary whose bytes no longer match the lock", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    await fsp.mkdir(outDir, { recursive: true });
    await fsp.writeFile(path.join(outDir, "gaia-agent"), "stale contents");
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(res.cached).toBe(false);
    expect(fs.readFileSync(res.binaryPath)).toEqual(SIDECAR_BYTES);
  });

  it("surfaces a download HTTP error", async () => {
    const lockPath = await realShaLock();
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES, 503),
      }),
    ).rejects.toThrow(/HTTP 503/);
  });

  it("rejects an unknown component at the type boundary", async () => {
    const lockPath = await realShaLock();
    await expect(
      fetchBinary({
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component: "installer" as any,
        outDir: path.join(tmp, "x"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });
});

describe("fetchAll", () => {
  it("fetches BOTH components and verifies each against its own hash", async () => {
    const lockPath = await realShaLock();
    const { sidecar, tui } = await fetchAll({
      lockPath,
      platformKey: "linux-x64",
      sidecarDir: path.join(tmp, "agents", "gaia"),
      tuiDir: path.join(tmp, "cache"),
      fetchImpl: recordingFetch(BODIES),
    });
    expect(sidecar.sha256).toBe(SIDECAR_SHA);
    expect(tui.sha256).toBe(TUI_SHA);
    // Two different lanes, in order — the sidecar from ours, the TUI from
    // terminal-hub's. A single shared baseUrl could not produce this.
    expect(urls).toEqual([
      `${SIDECAR_BASE}/gaia-agent-linux-x64`,
      `${TUI_BASE}/gaia-linux-x64`,
    ]);
    expect(fs.existsSync(sidecar.binaryPath)).toBe(true);
    expect(fs.existsSync(tui.binaryPath)).toBe(true);
  });

  it("aborts on the FIRST integrity failure and never installs the second binary", async () => {
    const lockPath = await realShaLock();
    const tuiDir = path.join(tmp, "cache");
    await expect(
      fetchAll({
        lockPath,
        platformKey: "linux-x64",
        sidecarDir: path.join(tmp, "agents", "gaia"),
        tuiDir,
        fetchImpl: recordingFetch({
          "gaia-agent-linux-x64": Buffer.from("tampered"),
          "gaia-linux-x64": TUI_BYTES,
        }),
      }),
    ).rejects.toBeInstanceOf(IntegrityError);
    expect(fs.existsSync(path.join(tuiDir, "gaia-tui"))).toBe(false);
  });
});

describe("the .installed sentinel", () => {
  /** Read the sentinel the daemon and the TUI both key "installed" on. */
  function readSentinel(outDir: string): Record<string, unknown> {
    const raw = fs.readFileSync(path.join(outDir, INSTALLED_SENTINEL_NAME), "utf8");
    return JSON.parse(raw) as Record<string, unknown>;
  }

  async function installSidecar(outDir: string): Promise<string> {
    const lockPath = await realShaLock();
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    return res.sha256;
  }

  it("is written on a fresh install, in the shape the daemon requires", async () => {
    // Staging a verified binary is only half an install: without this file the
    // daemon sees nothing installed and the TUI runs the REST sidecar over
    // stdio, filling the chat with uvicorn's startup log.
    const outDir = path.join(tmp, "agents", "gaia");
    await installSidecar(outDir);
    const s = readSentinel(outDir);
    // gaia/daemon/sidecars/fetch.py::_hub_installed_binary rejects the install
    // unless all three of these hold.
    expect(s["artifact_kind"]).toBe("binary");
    expect(s["executable"]).toBe("gaia-agent");
    expect(s["artifact_sha256"]).toBe(SIDECAR_SHA);
    expect(s["id"]).toBe("gaia");
    expect(s["language"]).toBe("python");
    expect(s["version"]).toBe(SIDECAR_VERSION);
    expect(s["path"]).toBe(outDir);
    expect(Number.isNaN(Date.parse(String(s["installed_at"])))).toBe(false);
  });

  it("records the sha the daemon will re-hash the binary against", async () => {
    // A wrong hash here is an IntegrityError at daemon start, not a no-op.
    const outDir = path.join(tmp, "agents", "gaia");
    await installSidecar(outDir);
    const onDisk = crypto
      .createHash("sha256")
      .update(fs.readFileSync(path.join(outDir, "gaia-agent")))
      .digest("hex");
    expect(readSentinel(outDir)["artifact_sha256"]).toBe(onDisk);
  });

  it("keeps `executable` a bare filename", () => {
    // installer.read_sentinel discards a sentinel whose executable contains a
    // separator, which silently un-installs the agent.
    const outDir = path.join(tmp, "agents", "gaia");
    return installSidecar(outDir).then(() => {
      const exe = String(readSentinel(outDir)["executable"]);
      expect(exe).not.toContain("/");
      expect(exe).not.toContain("\\");
      expect(path.basename(exe)).toBe(exe);
    });
  });

  it("is written on a CACHE HIT too, repairing an older sentinel-less install", async () => {
    // Users who already ran an earlier npx have a verified binary and no
    // sentinel; if we only wrote on download they would stay broken forever.
    const outDir = path.join(tmp, "agents", "gaia");
    await installSidecar(outDir);
    fs.rmSync(path.join(outDir, INSTALLED_SENTINEL_NAME));

    const lockPath = await realShaLock();
    urls.length = 0;
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(res.cached).toBe(true);
    expect(urls).toEqual([]); // proves it really was the cache path
    expect(readSentinel(outDir)["artifact_sha256"]).toBe(SIDECAR_SHA);
  });

  it("is NOT written for the tui, which is not a hub agent", async () => {
    const outDir = path.join(tmp, "cache");
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "tui",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(fs.existsSync(path.join(outDir, INSTALLED_SENTINEL_NAME))).toBe(false);
  });

  it("fetchAll leaves one for the sidecar and none for the tui", async () => {
    const lockPath = await realShaLock();
    const sidecarDir = path.join(tmp, "agents", "gaia");
    const tuiDir = path.join(tmp, "cache");
    await fetchAll({
      lockPath,
      platformKey: "linux-x64",
      sidecarDir,
      tuiDir,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(fs.existsSync(path.join(sidecarDir, INSTALLED_SENTINEL_NAME))).toBe(true);
    expect(fs.existsSync(path.join(tuiDir, INSTALLED_SENTINEL_NAME))).toBe(false);
  });
});
