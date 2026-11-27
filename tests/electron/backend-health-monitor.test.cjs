// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { createBackendHealthMonitor } = require("../../src/gaia/apps/webui/services/backend-health-monitor.cjs");

function harness({ check, failureThreshold = 3 } = {}) {
  let timer;
  const onUnhealthy = jest.fn();
  const clearIntervalFn = jest.fn((handle) => {
    handle.cleared = true;
  });
  const monitor = createBackendHealthMonitor({
    check: check || jest.fn().mockResolvedValue(undefined),
    onUnhealthy,
    intervalMs: 25,
    failureThreshold,
    setIntervalFn: jest.fn((fn, intervalMs) => {
      timer = { fn, intervalMs, unref: jest.fn(), cleared: false };
      return timer;
    }),
    clearIntervalFn,
  });
  const tick = async () => {
    timer.fn();
    await new Promise(setImmediate);
  };
  return { monitor, onUnhealthy, clearIntervalFn, tick, get timer() { return timer; } };
}

test("it needs consecutive failures and a successful probe resets the count", async () => {
  const check = jest
    .fn()
    .mockRejectedValueOnce(new Error("first outage"))
    .mockResolvedValueOnce(undefined)
    .mockRejectedValue(new Error("health endpoint unavailable"));
  const h = harness({ check, failureThreshold: 2 });
  h.monitor.start();

  await h.tick();
  await h.tick();
  await h.tick();
  expect(h.onUnhealthy).not.toHaveBeenCalled();
  await h.tick();

  expect(h.onUnhealthy).toHaveBeenCalledTimes(1);
  expect(h.onUnhealthy).toHaveBeenCalledWith(expect.objectContaining({ message: "health endpoint unavailable" }), 2);
  expect(h.timer.cleared).toBe(true);
});

test("it does not run overlapping probes", async () => {
  let resolveCheck;
  const check = jest.fn(() => new Promise((resolve) => { resolveCheck = resolve; }));
  const h = harness({ check });
  h.monitor.start();
  h.timer.fn();
  h.timer.fn();
  expect(check).toHaveBeenCalledTimes(1);

  resolveCheck();
  await new Promise(setImmediate);
  await h.tick();
  expect(check).toHaveBeenCalledTimes(2);
  h.monitor.stop();
});

test("stopping invalidates a probe that is still in flight", async () => {
  let rejectCheck;
  let signal;
  const check = jest.fn((probeSignal) => new Promise((_resolve, reject) => {
    signal = probeSignal;
    rejectCheck = reject;
    probeSignal.addEventListener("abort", () => reject(new Error("probe aborted")), { once: true });
  }));
  const h = harness({ check, failureThreshold: 1 });
  h.monitor.start();
  h.timer.fn();
  h.monitor.stop();
  rejectCheck(new Error("backend is gone"));
  await new Promise(setImmediate);

  expect(signal.aborted).toBe(true);
  expect(h.onUnhealthy).not.toHaveBeenCalled();
  expect(h.clearIntervalFn).toHaveBeenCalledWith(h.timer);
});

test("start is idempotent and invalid monitor settings are rejected", () => {
  const h = harness();
  h.monitor.start();
  const timer = h.timer;
  h.monitor.start();
  expect(h.timer).toBe(timer);
  expect(timer.intervalMs).toBe(25);

  expect(() => createBackendHealthMonitor({ check: () => {}, onUnhealthy: () => {}, intervalMs: 0 })).toThrow(/intervalMs/);
  expect(() => createBackendHealthMonitor({ check: () => {}, onUnhealthy: () => {}, failureThreshold: 0 })).toThrow(/failureThreshold/);
  h.monitor.stop();
});
