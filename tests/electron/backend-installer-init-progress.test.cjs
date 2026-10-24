// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The installer shows `gaia init`'s model download as it runs. It used to sit
// at 70% for the whole ~30 GB download, which looks the same as a hang (#5581).

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);
const {
  parseInitEventLine,
  createInitProgress,
  computeOverallPercent,
  STAGES,
} = require(installerPath);

const GB = 1e9;
const line = (event) => JSON.stringify(event);
const PLAN = {
  event: "plan",
  models: [
    { id: "Qwen3.6-35B-A3B-GGUF", bytes_total: 24 * GB, downloaded: false },
    { id: "Gemma-4-E4B-it-GGUF", bytes_total: 6 * GB, downloaded: false },
    { id: "user.embeddinggemma-300m-GGUF", bytes_total: 0, downloaded: true },
  ],
  bytes_total: 30 * GB,
  disk_free_bytes: 400 * GB,
};

function tracker() {
  const reports = [];
  const logFn = jest.fn();
  const progress = createInitProgress((p, m) => reports.push([p, m]), logFn);
  return { progress, reports, logFn, last: () => reports[reports.length - 1] };
}

describe("parseInitEventLine", () => {
  test("human output is not an event", () => {
    expect(parseInitEventLine("Step 1/5: Starting Lemonade Server...")).toBeNull();
    expect(parseInitEventLine("")).toBeNull();
  });

  test("an event line is parsed", () => {
    expect(parseInitEventLine(`  ${line({ event: "done", ok: true })}\r`)).toEqual({
      event: { event: "done", ok: true },
    });
  });

  test("a broken or event-less object is reported, not thrown", () => {
    expect(parseInitEventLine('{"event": "progress", "bytes_done": ')).toHaveProperty("error");
    expect(parseInitEventLine('{"model": "m"}')).toEqual({ error: 'no "event" field' });
  });
});

describe("createInitProgress", () => {
  test("the plan moves the bar and names the download size", () => {
    const { progress, last } = tracker();
    progress.onLine(line(PLAN));
    expect(last()).toEqual([5, "Downloading 30.0 GB of models"]);
  });

  test("download progress fills the slice by bytes across every model", () => {
    const { progress, last } = tracker();
    progress.onLine(line(PLAN));
    progress.onLine(
      line({
        event: "progress",
        model: "Qwen3.6-35B-A3B-GGUF",
        index: 1,
        count: 3,
        bytes_done: 12 * GB,
        bytes_total: 24 * GB,
        speed_bps: 50e6,
      })
    );
    // 12 of 30 GB: 5 + 90 * 0.4.
    expect(last()[0]).toBe(41);
    // 18 GB left at 50 MB/s is 6 minutes.
    expect(last()[1]).toBe(
      "Downloading Qwen3.6-35B-A3B-GGUF (1 of 3): 12.0 GB of 24.0 GB at 50.0 MB/s, about 6 min left"
    );

    progress.onLine(line({ event: "model_done", model: "Qwen3.6-35B-A3B-GGUF", downloaded: true }));
    expect(last()).toEqual([77, "Qwen3.6-35B-A3B-GGUF ready"]);

    progress.onLine(
      line({
        event: "progress",
        model: "Gemma-4-E4B-it-GGUF",
        index: 2,
        count: 3,
        bytes_done: 3 * GB,
        bytes_total: 6 * GB,
        speed_bps: 0,
      })
    );
    expect(last()).toEqual([86, "Downloading Gemma-4-E4B-it-GGUF (2 of 3): 3.0 GB of 6.0 GB"]);

    progress.onLine(line({ event: "done", ok: true }));
    expect(last()).toEqual([95, "Models downloaded, finishing setup"]);
  });

  test("hours-long downloads say hours", () => {
    const { progress, last } = tracker();
    progress.onLine(line(PLAN));
    progress.onLine(
      line({
        event: "progress",
        model: "Qwen3.6-35B-A3B-GGUF",
        index: 1,
        count: 3,
        bytes_done: 0,
        bytes_total: 24 * GB,
        speed_bps: 5e6,
      })
    );
    expect(last()[1]).toMatch(/at 5.0 MB\/s, about 1 h 40 min left$/);
    progress.onLine(
      line({
        event: "progress",
        model: "Qwen3.6-35B-A3B-GGUF",
        index: 1,
        count: 3,
        bytes_done: 24 * GB,
        bytes_total: 24 * GB,
        speed_bps: 500e6,
      })
    );
    expect(last()[1]).toMatch(/at 500.0 MB\/s, less than a minute left$/);
  });

  test("the bar never moves back", () => {
    const { progress, reports } = tracker();
    progress.onLine(line(PLAN));
    const step = { event: "progress", model: "Gemma-4-E4B-it-GGUF", index: 2, count: 3 };
    progress.onLine(line({ ...step, bytes_done: 6 * GB, bytes_total: 6 * GB }));
    // A larger real size than planned would shrink the share; the bar holds.
    progress.onLine(line({ ...step, bytes_done: 6 * GB, bytes_total: 12 * GB }));
    const percents = reports.map(([p]) => p);
    expect(percents).toEqual([...percents].sort((a, b) => a - b));
  });

  test("steps and errors update the text without moving the bar", () => {
    const { progress, last } = tracker();
    progress.onLine(line({ event: "step", step: 1, total: 5, message: "Starting Lemonade Server..." }));
    expect(last()).toEqual([0, "Step 1 of 5: Starting Lemonade Server..."]);
    progress.onLine(line({ event: "error", message: "Not enough disk space: the models need about 30.0 GB" }));
    expect(last()).toEqual([0, "Not enough disk space: the models need about 30.0 GB"]);
  });

  test("unknown events and human lines are ignored; broken lines are logged", () => {
    const { progress, reports, logFn } = tracker();
    expect(progress.onLine(line({ event: "something-new" }))).toBe(false);
    expect(progress.onLine("   ✓ Downloaded Gemma-4-E4B-it-GGUF")).toBe(false);
    expect(progress.onLine('{"event": "progr')).toBe(false);
    expect(reports).toEqual([]);
    expect(logFn).toHaveBeenCalledTimes(1);
    expect(logFn.mock.calls[0][0]).toMatch(/^Malformed gaia init progress line/);
  });

  test("only the frequent progress lines are kept out of the log", () => {
    const { progress } = tracker();
    expect(progress.onLine(line(PLAN))).toBe(false);
    expect(
      progress.onLine(line({ event: "progress", model: "m", index: 1, count: 1, bytes_done: 1, bytes_total: 2 }))
    ).toBe(true);
  });

  test("the slice maps onto the overall bar after the package install", () => {
    expect(computeOverallPercent(STAGES.GAIA_INIT, 0)).toBe(70);
    expect(computeOverallPercent(STAGES.GAIA_INIT, 50)).toBe(84);
    expect(computeOverallPercent(STAGES.GAIA_INIT, 100)).toBe(98);
  });
});

/** The installer with `gaia init` printing `initChunks`; every other command prints nothing. */
function loadInstaller(initChunks) {
  const files = new Map([
    [
      "/fixture/.gaia/electron-install-state.json",
      JSON.stringify({ state: "installing", stage: "gaia-init", version: "0.25.0" }),
    ],
  ]);
  const logged = [];
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      if (argv[0] === "init") initChunks.forEach((c) => child.stdout.emit("data", Buffer.from(c)));
      child.stdout.emit("end");
      child.emit("exit", 0);
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => String(file).includes("/venv") || files.has(String(file)),
    readFileSync: (file, enc) =>
      files.has(String(file)) ? files.get(String(file)) : fs.readFileSync(file, enc),
    writeFileSync: (file, data) => files.set(String(file), String(data)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: 1e9, bsize: 4096 }),
    createWriteStream: () => ({ write: (s) => logged.push(s), end: jest.fn(), on: jest.fn() }),
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: {} },
    setTimeout,
    clearTimeout,
    queueMicrotask,
    URL,
    Buffer,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return path.posix;
      if (name === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (name === "child_process") {
        return {
          spawn,
          execSync: jest.fn(),
          spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.25.0" })),
        };
      }
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, {
    filename: installerPath,
  });
  return { installer: context.module.exports, spawn, logged };
}

describe("runGaiaInit", () => {
  test("asks for JSON progress and shows it, even split across reads", async () => {
    const progressLine = line({
      event: "progress",
      model: "Gemma-4-E4B-it-GGUF",
      index: 1,
      count: 1,
      bytes_done: 3 * GB,
      bytes_total: 6 * GB,
      speed_bps: 0,
    });
    const out =
      "Step 3/5: Downloading models for 'gaia' profile...\n" +
      `${line({ ...PLAN, models: [PLAN.models[1]], bytes_total: 6 * GB })}\n` +
      `${progressLine}\n`;
    // Cut mid-line, as a pipe read can.
    const cut = out.indexOf("bytes_done");
    const { installer, spawn, logged } = loadInstaller([out.slice(0, cut), out.slice(cut)]);
    const onProgress = jest.fn();

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false, onProgress });

    const init = spawn.mock.calls.find(([, argv]) => argv[0] === "init");
    expect(init[1]).toContain("--progress-json");
    const messages = onProgress.mock.calls.map(([, , m]) => m);
    expect(messages).toContain("Downloading 6.0 GB of models");
    expect(messages).toContain("Downloading Gemma-4-E4B-it-GGUF (1 of 1): 3.0 GB of 6.0 GB");
    const log = logged.join("");
    expect(log).toContain("Step 3/5: Downloading models");
    expect(log).toContain('"event":"plan"');
    expect(log).not.toContain('"event":"progress"');
  });

  test("reads an event that follows an in-place progress bar's redraws", async () => {
    const step = line({ event: "step", step: 2, total: 5, message: "Downloading models..." });
    const { installer, logged } = loadInstaller([
      `\r   [====------] 40% (40.0 MB/100.0 MB)\r   [==========] 100% (100.0 MB/100.0 MB)\r${step}\n`,
    ]);
    const onProgress = jest.fn();

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false, onProgress });

    const messages = onProgress.mock.calls.map(([, , m]) => m);
    expect(messages).toContain("Step 2 of 5: Downloading models...");
    // The bar is human output, so the line still reaches the log.
    expect(logged.join("")).toContain("100% (100.0 MB/100.0 MB)");
  });
});
