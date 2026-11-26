// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

"use strict";

const DEFAULTS = Object.freeze({
  intervalMs: 5000,
  failureThreshold: 3,
});

function createBackendHealthMonitor({
  check,
  onUnhealthy,
  intervalMs = DEFAULTS.intervalMs,
  failureThreshold = DEFAULTS.failureThreshold,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
}) {
  if (typeof check !== "function") {
    throw new Error("backend health monitor: check must be a function");
  }
  if (typeof onUnhealthy !== "function") {
    throw new Error("backend health monitor: onUnhealthy must be a function");
  }
  if (!Number.isInteger(intervalMs) || intervalMs < 1) {
    throw new Error(`backend health monitor: intervalMs must be a positive integer, got ${intervalMs}`);
  }
  if (!Number.isInteger(failureThreshold) || failureThreshold < 1) {
    throw new Error(`backend health monitor: failureThreshold must be a positive integer, got ${failureThreshold}`);
  }

  let timer = null;
  let generation = 0;
  let failures = 0;
  let checking = false;
  let activeController = null;

  async function poll(run) {
    if (checking || timer === null || run !== generation) return;
    checking = true;
    const controller = new AbortController();
    activeController = controller;
    try {
      await check(controller.signal);
      if (run === generation) failures = 0;
    } catch (error) {
      if (run !== generation) return;
      failures += 1;
      if (failures >= failureThreshold) {
        const consecutiveFailures = failures;
        stop();
        onUnhealthy(error, consecutiveFailures);
      }
    } finally {
      if (activeController === controller) activeController = null;
      checking = false;
    }
  }

  function start() {
    if (timer !== null) return;
    const run = ++generation;
    failures = 0;
    timer = setIntervalFn(() => {
      void poll(run);
    }, intervalMs);
    if (timer && typeof timer.unref === "function") timer.unref();
  }

  function stop() {
    generation += 1;
    if (activeController) {
      activeController.abort();
      activeController = null;
    }
    if (timer !== null) {
      clearIntervalFn(timer);
      timer = null;
    }
    failures = 0;
  }

  return { start, stop };
}

module.exports = { createBackendHealthMonitor, DEFAULTS };
