// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// First install on a PC that already runs Lemonade: the installer asks, then
// passes the answer to `gaia init` so it does not install a second server.

const { PassThrough } = require("stream");
const {
  lemonadeInitArgs,
  STAGES,
  GAIA_INIT_MESSAGE,
  WAITING_FOR_LEMONADE_CHOICE_MESSAGE,
} = require("../../src/gaia/apps/webui/services/backend-installer.cjs");
const {
  chooseLemonadeOnTerminal,
} = require("../../src/gaia/apps/webui/bin/gaia-ui.cjs");

const BIN = "/home/u/.gaia/venv/bin/gaia";
const STATUS = ["lemonade", "status", "--json"];
const DETECT = ["lemonade", "detect", "--json"];
const UNCHOSEN = { mode: "gaia", saved_mode: null, source: "gaia", base_url: null, saved_base_url: null, api_key_stored: false };
const FOUND = { url: "http://localhost:13305/api/v1", status: "ok", version: "10.3.0", minimum: "10.0.0", message: "" };

/** A fake runCommand answering each `gaia` subcommand with canned JSON. */
function fakeRun(answers) {
  return jest.fn(async (_bin, args) => {
    const key = args.join(" ");
    if (!(key in answers)) throw new Error(`unexpected command: ${key}`);
    const a = answers[key];
    return typeof a === "object" && a !== null && "code" in a
      ? a
      : { code: 0, stdout: `${JSON.stringify(a)}\n`, stderr: "" };
  });
}

function logged() {
  return console.log.mock.calls.map((c) => c.join(" ")).join("\n");
}

beforeEach(() => console.log.mockClear());

describe("lemonadeInitArgs", () => {
  test("accepting a running Lemonade passes its URL to gaia init", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: FOUND });
    const choose = jest.fn(async () => "external");

    const args = await lemonadeInitArgs(BIN, choose, run);

    expect(["init", "--profile", "minimal", "--yes", ...args]).toEqual([
      "init", "--profile", "minimal", "--yes", "--lemonade-url", "http://localhost:13305/api/v1",
    ]);
    expect(run.mock.calls.map((c) => [c[0], c[1]])).toEqual([[BIN, STATUS], [BIN, DETECT]]);
    expect(choose).toHaveBeenCalledWith(FOUND);
  });

  test("declining saves GAIA's own", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: FOUND });
    expect(await lemonadeInitArgs(BIN, async () => "gaia", run)).toEqual(["--gaia-lemonade"]);
  });

  test("no answer leaves gaia init unchanged", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: FOUND });
    expect(await lemonadeInitArgs(BIN, async () => null, run)).toEqual([]);
  });

  test("a saved choice is never asked again", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: { ...UNCHOSEN, saved_mode: "gaia" } });
    const choose = jest.fn();
    expect(await lemonadeInitArgs(BIN, choose, run)).toEqual([]);
    expect(run).toHaveBeenCalledTimes(1);
    expect(choose).not.toHaveBeenCalled();
  });

  test("LEMONADE_BASE_URL decides, so nothing is asked", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: { ...UNCHOSEN, mode: "external", source: "env" } });
    const choose = jest.fn();
    expect(await lemonadeInitArgs(BIN, choose, run)).toEqual([]);
    expect(choose).not.toHaveBeenCalled();
  });

  test.each(["needs_key", "too_old"])("a %s server is left to the app's setup screen", async (status) => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: { ...FOUND, status } });
    const choose = jest.fn();
    expect(await lemonadeInitArgs(BIN, choose, run)).toEqual([]);
    expect(choose).not.toHaveBeenCalled();
  });

  test("nothing running: nothing asked", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: null });
    const choose = jest.fn();
    expect(await lemonadeInitArgs(BIN, choose, run)).toEqual([]);
    expect(choose).not.toHaveBeenCalled();
  });

  test("no callback: runs nothing and leaves gaia init unchanged", async () => {
    const run = fakeRun({});
    expect(await lemonadeInitArgs(BIN, undefined, run)).toEqual([]);
    expect(run).not.toHaveBeenCalled();
  });

  test("a failing status command is logged with its output", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: { code: 2, stdout: "", stderr: "invalid choice: 'lemonade'" } });
    const choose = jest.fn();
    expect(await lemonadeInitArgs(BIN, choose, run)).toEqual([]);
    expect(choose).not.toHaveBeenCalled();
    expect(logged()).toMatch(/gaia lemonade status --json exited with code 2: invalid choice/);
    expect(logged()).toMatch(/gaia lemonade connect <url>/);
  });

  test("non-JSON detect output is logged, not guessed at", async () => {
    const run = fakeRun({
      [STATUS.join(" ")]: UNCHOSEN,
      [DETECT.join(" ")]: { code: 0, stdout: "Traceback ...\n", stderr: "" },
    });
    expect(await lemonadeInitArgs(BIN, jest.fn(), run)).toEqual([]);
    expect(logged()).toMatch(/gaia lemonade detect --json printed no JSON/);
  });

  test("the progress window says it is waiting on the user while the question is open", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: FOUND });
    const report = jest.fn();
    const choose = jest.fn(async () => {
      expect(report).toHaveBeenCalledTimes(1);
      return "external";
    });

    await lemonadeInitArgs(BIN, choose, run, report);

    expect(report.mock.calls).toEqual([
      [STAGES.GAIA_INIT, 0, WAITING_FOR_LEMONADE_CHOICE_MESSAGE],
      [STAGES.GAIA_INIT, 0, GAIA_INIT_MESSAGE],
    ]);
    expect(WAITING_FOR_LEMONADE_CHOICE_MESSAGE).toMatch(/^Waiting for your answer/);
  });

  test("the waiting text is cleared even when asking fails", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: FOUND });
    const report = jest.fn();
    const choose = jest.fn(async () => {
      throw new Error("window closed");
    });

    expect(await lemonadeInitArgs(BIN, choose, run, report)).toEqual([]);

    expect(report).toHaveBeenLastCalledWith(STAGES.GAIA_INIT, 0, GAIA_INIT_MESSAGE);
  });

  test("nothing to ask: the progress text is left alone", async () => {
    const run = fakeRun({ [STATUS.join(" ")]: UNCHOSEN, [DETECT.join(" ")]: null });
    const report = jest.fn();

    await lemonadeInitArgs(BIN, jest.fn(), run, report);

    expect(report).not.toHaveBeenCalled();
  });

  test("JSON on the last line wins over earlier log lines", async () => {
    const run = fakeRun({
      [STATUS.join(" ")]: { code: 0, stdout: `some log line\n${JSON.stringify(UNCHOSEN)}\n`, stderr: "" },
      [DETECT.join(" ")]: FOUND,
    });
    expect(await lemonadeInitArgs(BIN, async () => "gaia", run)).toEqual(["--gaia-lemonade"]);
  });
});

describe("chooseLemonadeOnTerminal", () => {
  test("without a terminal it does not ask", async () => {
    const input = new PassThrough();
    input.isTTY = false;
    await expect(chooseLemonadeOnTerminal(FOUND, { input, output: new PassThrough() })).resolves.toBeNull();
  });

  test.each([
    ["y\n", "external"],
    ["yes\n", "external"],
    ["\n", "external"],
    ["n\n", "gaia"],
    ["no\n", "gaia"],
  ])("answer %j means %s", async (typed, expected) => {
    const input = new PassThrough();
    input.isTTY = true;
    const output = new PassThrough();
    let asked = "";
    output.on("data", (d) => { asked += d.toString(); });
    const answer = chooseLemonadeOnTerminal(FOUND, { input, output });
    input.write(typed);
    await expect(answer).resolves.toBe(expected);
    expect(asked).toContain("A Lemonade Server (v10.3.0) is already running at http://localhost:13305/api/v1.");
  });
});
