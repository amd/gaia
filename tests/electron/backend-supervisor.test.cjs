// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The backend supervisor restarts GAIA's backend when it exits on its own:
 * after a growing wait, at most a few times in a window, and then it stops
 * and waits for the user's Restart GAIA.
 */

const { createBackendSupervisor } = require("../../src/gaia/apps/webui/services/backend-supervisor.cjs");

function harness(opts = {}) {
  let clock = 1_000_000;
  const timers = [];
  const states = [];
  const starts = [];
  const sup = createBackendSupervisor({
    start: jest.fn(
      () =>
        new Promise((resolve, reject) => {
          starts.push({ resolve, reject });
        })
    ),
    onState: (s) => states.push(s),
    now: () => clock,
    setTimer: (fn, ms) => {
      const t = { fn, at: clock + ms, cleared: false };
      timers.push(t);
      return t;
    },
    clearTimer: (t) => {
      if (t) t.cleared = true;
    },
    ...opts,
  });
  const advance = (ms) => {
    clock += ms;
    for (const t of timers) {
      if (!t.cleared && !t.fired && t.at <= clock) {
        t.fired = true;
        t.fn();
      }
    }
  };
  const pending = () => timers.filter((t) => !t.cleared && !t.fired);
  const settle = () => new Promise((r) => setImmediate(r));
  return { sup, states, starts, advance, pending, settle, last: () => states[states.length - 1] };
}

test("a crash schedules a restart after the first backoff and says so", () => {
  const h = harness({ backoffMs: [1000, 4000] });
  h.sup.running(5000);
  expect(h.sup.crashed({ code: 1, signal: null })).toBe("restarting");
  expect(h.last()).toMatchObject({ status: "restarting", attempt: 1, lastExit: { code: 1, signal: null } });
  expect(h.last().nextAttemptAt - h.last().since).toBe(1000);
  expect(h.starts).toHaveLength(0);
  h.advance(999);
  expect(h.starts).toHaveLength(0);
  h.advance(1);
  expect(h.starts).toHaveLength(1);
});

test("a successful restart reports running on the new port", async () => {
  const h = harness();
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(60_000);
  h.starts[0].resolve({ port: 6123 });
  await h.settle();
  expect(h.last()).toMatchObject({ status: "running", port: 6123 });
  expect(h.sup.getState().port).toBe(6123);
});

test("a failed start counts as a crash and waits longer before the next try", async () => {
  const h = harness({ backoffMs: [1000, 4000, 15000], maxRestarts: 3 });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  const since = h.last().since;
  h.advance(1000);
  h.starts[0].reject(new Error("backend exited with code 1 before it answered"));
  await h.settle();
  expect(h.last()).toMatchObject({ status: "restarting", attempt: 2, since });
  expect(h.last().lastError).toMatch(/before it answered/);
  h.advance(3999);
  expect(h.starts).toHaveLength(1);
  h.advance(1);
  expect(h.starts).toHaveLength(2);
});

test("restarts stop once the cap is hit inside the window", async () => {
  const h = harness({ maxRestarts: 2, windowMs: 600_000, backoffMs: [10] });
  h.sup.running(5000);
  for (let i = 0; i < 2; i++) {
    h.sup.crashed({ code: 1 });
    h.advance(10);
    h.starts[i].resolve({ port: 6000 + i });
    await h.settle();
    expect(h.last().status).toBe("running");
  }
  expect(h.sup.crashed({ code: 1 })).toBe("stopped");
  expect(h.last()).toMatchObject({ status: "stopped", lastExit: { code: 1 } });
  h.advance(600_000);
  expect(h.starts).toHaveLength(2);
});

test("old restarts age out of the window", async () => {
  const h = harness({ maxRestarts: 1, windowMs: 60_000, backoffMs: [10] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(10);
  h.starts[0].resolve({ port: 6000 });
  await h.settle();
  h.advance(60_001);
  expect(h.sup.crashed({ code: 1 })).toBe("restarting");
});

test("Restart GAIA after the cap starts at once with a fresh budget", async () => {
  const h = harness({ maxRestarts: 1, backoffMs: [10] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(10);
  h.starts[0].reject(new Error("no"));
  await h.settle();
  expect(h.last().status).toBe("stopped");
  h.sup.restartNow();
  expect(h.starts).toHaveLength(2);
  expect(h.last()).toMatchObject({ status: "restarting", attempt: 1, nextAttemptAt: null });
  h.starts[1].reject(new Error("still no"));
  await h.settle();
  // The fresh budget allows one more automatic try.
  expect(h.last()).toMatchObject({ status: "restarting", attempt: 2 });
});

test("Restart GAIA while waiting skips the wait; while starting it does nothing", () => {
  const h = harness({ backoffMs: [30_000] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.sup.restartNow();
  expect(h.starts).toHaveLength(1);
  expect(h.pending()).toHaveLength(0);
  h.sup.restartNow();
  expect(h.starts).toHaveLength(1);
});

test("an exit reported while a restart is starting is left to that start", () => {
  const h = harness({ backoffMs: [10] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(10);
  expect(h.sup.crashed({ code: 1 })).toBe("restarting");
  expect(h.pending()).toHaveLength(0);
  expect(h.starts).toHaveLength(1);
});

test("stop() cancels a pending restart and ignores later crashes", () => {
  const h = harness({ backoffMs: [1000] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.sup.stop();
  h.advance(5000);
  expect(h.starts).toHaveLength(0);
  expect(h.sup.crashed({ code: 1 })).toBe("stopped");
  expect(h.starts).toHaveLength(0);
});

test("a start that resolves after stop() does not report running", async () => {
  const h = harness({ backoffMs: [10] });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(10);
  h.sup.stop();
  const before = h.states.length;
  h.starts[0].resolve({ port: 7000 });
  await h.settle();
  expect(h.states).toHaveLength(before);
});

test("rejects options that would restart forever", () => {
  expect(() => createBackendSupervisor({ start: () => {}, onState: () => {}, maxRestarts: 0 })).toThrow(/maxRestarts/);
  expect(() => createBackendSupervisor({ start: () => {}, onState: () => {}, backoffMs: [] })).toThrow(/backoffMs/);
});

test("restarts that keep failing call onStopped once, with the last exit", async () => {
  const onStopped = jest.fn();
  const h = harness({ maxRestarts: 3, backoffMs: [10], onStopped });
  h.sup.running(5000);
  h.sup.crashed({ code: 3221225477 });
  for (let i = 0; i < 3; i++) {
    h.advance(10);
    // The restarted child dies before answering, then its start rejects.
    h.sup.crashed({ code: 1 });
    h.starts[i].reject(new Error(`try ${i + 1} exited before it answered`));
    await h.settle();
  }
  expect(h.starts).toHaveLength(3);
  expect(h.last()).toMatchObject({ status: "stopped", lastExit: { code: 1 }, lastError: "try 3 exited before it answered" });
  expect(onStopped).toHaveBeenCalledTimes(1);
  expect(onStopped.mock.calls[0][0]).toMatchObject({ status: "stopped", lastExit: { code: 1 } });

  // A stray late exit while stopped doesn't show the dialog again.
  expect(h.sup.crashed({ code: 1 })).toBe("stopped");
  expect(onStopped).toHaveBeenCalledTimes(1);
});

test("a crash that hits the cap calls onStopped too, and Restart GAIA re-arms it", async () => {
  const onStopped = jest.fn();
  const h = harness({ maxRestarts: 1, backoffMs: [10], onStopped });
  h.sup.running(5000);
  h.sup.crashed({ code: 1 });
  h.advance(10);
  h.starts[0].resolve({ port: 6000 });
  await h.settle();
  expect(h.sup.crashed({ code: 2 })).toBe("stopped");
  expect(onStopped).toHaveBeenCalledTimes(1);

  h.sup.restartNow();
  h.starts[1].reject(new Error("no"));
  await h.settle();
  h.advance(10);
  h.starts[2].reject(new Error("still no"));
  await h.settle();
  expect(h.last().status).toBe("stopped");
  expect(onStopped).toHaveBeenCalledTimes(2);
});
