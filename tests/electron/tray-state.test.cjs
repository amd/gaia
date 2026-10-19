// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the tray's state machine and menu
 * (src/gaia/apps/webui/services/tray-state.cjs).
 */

const {
  validateActivity,
  deriveTrayView,
  buildMenuTemplate,
  modelLabel,
  menuText,
} = require("../../src/gaia/apps/webui/services/tray-state.cjs");

function activity(overrides = {}) {
  return validateActivity({
    backendConnected: true,
    modelServerRunning: true,
    model: { name: "Gemma-4-E4B-it-GGUF", where: "gpu" },
    waitingOnModel: null,
    running: [],
    needsYou: [],
    recent: [],
    ...overrides,
  });
}

const view = (a, ctx = {}) => deriveTrayView(a, { backendAlive: true, unread: 0, ...ctx });

const actions = () => ({
  openChat: jest.fn(),
  newChat: jest.fn(),
  showWindow: jest.fn(),
  freeMemory: jest.fn(),
  openSettings: jest.fn(),
  quit: jest.fn(),
});

describe("validateActivity", () => {
  test.each([
    [null, /expects an object payload/],
    [[], /expects an object payload/],
    [{ ...activity(), backendConnected: 1 }, /backendConnected must be a boolean/],
    [{ ...activity(), modelServerRunning: "up" }, /modelServerRunning must be a boolean or null/],
    [{ ...activity(), model: { where: "gpu" } }, /model must be null or \{ name, where \}/],
    [{ ...activity(), model: { name: "m", where: 3 } }, /model.where must be a string or null/],
    [{ ...activity(), waitingOnModel: 5 }, /waitingOnModel must be a string or null/],
    [{ ...activity(), running: "a" }, /running must be an array/],
    [{ ...activity(), recent: [{ id: "", title: "t" }] }, /recent\[0\].id must be a non-empty string/],
    [{ ...activity(), recent: [{ id: "a" }] }, /recent\[0\].title must be a string/],
    [
      { ...activity(), needsYou: [{ id: "a", title: "t", reason: "maybe" }] },
      /needsYou\[0\].reason must be one of approval, answer/,
    ],
  ])("rejects %p", (raw, err) => {
    expect(() => validateActivity(raw)).toThrow(err);
  });

  test("drops fields it doesn't know", () => {
    const a = validateActivity({ ...activity(), recent: [{ id: "a", title: "t", extra: 1 }], extra: 2 });
    expect(a.recent).toEqual([{ id: "a", title: "t" }]);
    expect(a).not.toHaveProperty("extra");
  });
});

describe("deriveTrayView state priority", () => {
  const busy = { running: [{ id: "a", title: "A" }] };
  const waiting = { ...busy, waitingOnModel: "Loading Gemma into memory" };
  const needs = { ...waiting, needsYou: [{ id: "a", title: "A", reason: "approval" }] };

  test.each([
    ["idle", {}],
    ["working", busy],
    ["waiting", waiting],
    ["needs-you", needs],
    ["error", { ...needs, backendConnected: false }],
    ["error", { ...needs, modelServerRunning: false }],
  ])("%s", (state, overrides) => {
    expect(view(activity(overrides)).state).toBe(state);
  });

  test("an exited GAIA service wins over everything", () => {
    expect(view(activity(needs), { backendAlive: false }).state).toBe("error");
  });

  test("an unknown model-server state is not an error", () => {
    expect(view(activity({ modelServerRunning: null })).state).toBe("idle");
  });

  test("before the window reports, the tray says it is starting", () => {
    const v = view(null);
    expect(v.state).toBe("idle");
    expect(v.tooltip).toBe("GAIA — starting");
    expect(v.statusLine).toBe("Starting…");
  });
});

describe("tooltip and status line", () => {
  test("idle names the model and where it runs", () => {
    const v = view(activity());
    expect(v.statusLine).toBe("Gemma-4-E4B-it on GPU · idle");
    expect(v.tooltip).toBe("GAIA — idle");
  });

  test("one running chat is named; several are counted", () => {
    expect(view(activity({ running: [{ id: "a", title: "Trip plan" }] })).tooltip).toBe(
      "GAIA — answering in 'Trip plan'"
    );
    expect(
      view(activity({ running: [{ id: "a", title: "A" }, { id: "b", title: "B" }] })).tooltip
    ).toBe("GAIA — answering in 2 chats");
  });

  test("needs you says why for one chat", () => {
    expect(view(activity({ needsYou: [{ id: "a", title: "Trip plan", reason: "approval" }] })).tooltip).toBe(
      "GAIA — 'Trip plan' needs your approval"
    );
    expect(view(activity({ needsYou: [{ id: "a", title: "Trip plan", reason: "answer" }] })).tooltip).toBe(
      "GAIA — 'Trip plan' needs your answer"
    );
  });

  test("waiting for the model repeats what the model is doing", () => {
    const v = view(activity({ running: [{ id: "a", title: "A" }], waitingOnModel: "Loading Gemma into memory" }));
    expect(v.tooltip).toBe("GAIA — loading Gemma into memory");
    expect(v.statusLine).toBe("Gemma-4-E4B-it on GPU · loading Gemma into memory");
  });

  test("errors say what is wrong in both places", () => {
    const v = view(activity({ modelServerRunning: false }));
    expect(v.tooltip).toBe("GAIA — the model server isn't running");
    expect(v.statusLine).toBe("The model server isn't running");
  });

  test("unread notifications are added to the tooltip", () => {
    expect(view(activity(), { unread: 2 }).tooltip).toBe("GAIA — idle · 2 unread notifications");
  });

  test("long titles are shortened and the tooltip fits Windows' 127 characters", () => {
    const title = "x".repeat(300);
    const v = view(activity({ running: [{ id: "a", title }] }), { unread: 99 });
    expect(v.tooltip.length).toBeLessThanOrEqual(127);
    expect(v.tooltip).toContain("…");
  });

  test.each([
    [null, "No model loaded"],
    [{ name: "Gemma-4-E4B-it-GGUF", where: "npu" }, "Gemma-4-E4B-it on NPU"],
    [{ name: "Qwen3-GGUF", where: null }, "Qwen3"],
    [{ name: "big-model", where: "cloud" }, "big-model (cloud)"],
  ])("modelLabel(%p)", (model, label) => {
    expect(modelLabel(model)).toBe(label);
  });
});

describe("Free memory availability", () => {
  test("enabled with a local model and nothing running", () => {
    expect(view(activity()).canFreeMemory).toBe(true);
  });

  test.each([
    [{ running: [{ id: "a", title: "A" }] }, "an answer is running"],
    [{ running: [{ id: "a", title: "A" }, { id: "b", title: "B" }] }, "answers are running"],
    [{ model: null }, "no model loaded"],
    [{ model: { name: "m", where: "cloud" } }, "the model runs in the cloud"],
  ])("disabled for %p", (overrides, hint) => {
    const v = view(activity(overrides));
    expect(v.canFreeMemory).toBe(false);
    expect(v.freeMemoryHint).toBe(hint);
  });
});

describe("buildMenuTemplate", () => {
  const labels = (t) => t.map((i) => i.label || i.type);

  test("idle: status, New chat, Show, Free memory, Settings, Quit", () => {
    expect(labels(buildMenuTemplate(view(activity()), actions()))).toEqual([
      "Gemma-4-E4B-it on GPU · idle",
      "separator",
      "New chat",
      "separator",
      "Show GAIA",
      "Free memory…",
      "Settings…",
      "separator",
      "Quit GAIA",
    ]);
  });

  test("the status line and section headings are not clickable", () => {
    const t = buildMenuTemplate(
      view(activity({ needsYou: [{ id: "n", title: "N", reason: "approval" }], recent: [{ id: "r", title: "R" }] })),
      actions()
    );
    for (const label of ["Gemma-4-E4B-it on GPU · 'N' needs your approval", "Needs you", "Recent"]) {
      expect(t.find((i) => i.label === label).enabled).toBe(false);
    }
  });

  test("Needs you comes first and isn't repeated under Recent", () => {
    const a = actions();
    const t = buildMenuTemplate(
      view(
        activity({
          running: [{ id: "r1", title: "Draft" }],
          needsYou: [{ id: "n1", title: "Trip plan", reason: "approval" }],
          recent: [
            { id: "n1", title: "Trip plan" },
            { id: "r1", title: "Draft" },
            { id: "r2", title: "Old" },
          ],
        })
      ),
      a
    );
    expect(labels(t).slice(2, 10)).toEqual([
      "Needs you",
      "Trip plan — approve",
      "separator",
      "New chat",
      "Recent",
      "Draft — answering",
      "Old",
      "separator",
    ]);
    t.find((i) => i.label === "Trip plan — approve").click();
    expect(a.openChat).toHaveBeenCalledWith("n1");
    t.find((i) => i.label === "Old").click();
    expect(a.openChat).toHaveBeenCalledWith("r2");
  });

  test("at most five recent chats", () => {
    const recent = Array.from({ length: 9 }, (_, i) => ({ id: `s${i}`, title: `Chat ${i}` }));
    const t = buildMenuTemplate(view(activity({ recent })), actions());
    expect(t.filter((i) => /^Chat /.test(i.label))).toHaveLength(5);
  });

  test("every action item calls its action", () => {
    const a = actions();
    const t = buildMenuTemplate(view(activity()), a);
    const click = (l) => t.find((i) => i.label === l).click();
    click("New chat");
    click("Show GAIA");
    click("Free memory…");
    click("Settings…");
    click("Quit GAIA");
    expect(a.newChat).toHaveBeenCalled();
    expect(a.showWindow).toHaveBeenCalled();
    expect(a.freeMemory).toHaveBeenCalled();
    expect(a.openSettings).toHaveBeenCalled();
    expect(a.quit).toHaveBeenCalled();
  });

  test("Free memory is disabled with the reason while an answer runs", () => {
    const t = buildMenuTemplate(view(activity({ running: [{ id: "a", title: "A" }] })), actions());
    const item = t.find((i) => String(i.label).startsWith("Free memory"));
    expect(item.label).toBe("Free memory — an answer is running");
    expect(item.enabled).toBe(false);
  });

  test("an error leaves only the status line and the app actions", () => {
    const t = buildMenuTemplate(view(activity({ backendConnected: false, recent: [{ id: "r", title: "R" }] })), actions());
    expect(labels(t)[0]).toBe("Can't reach the GAIA service");
    expect(labels(t)).not.toContain("Recent");
  });

  test.each([
    ["an error", () => view(activity({ backendConnected: false }))],
    ["startup", () => view(null)],
  ])("Free memory says why it is off during %s", (_name, make) => {
    const item = buildMenuTemplate(make(), actions()).find((i) => String(i.label).startsWith("Free memory"));
    expect(item.label).toBe("Free memory — GAIA isn't ready");
    expect(item.enabled).toBe(false);
  });

  test("an ampersand in a title shows as itself", () => {
    expect(menuText("R&D notes")).toBe("R&&D notes");
    const t = buildMenuTemplate(view(activity({ recent: [{ id: "r", title: "R&D notes" }] })), actions());
    expect(labels(t)).toContain("R&&D notes");
  });
});
