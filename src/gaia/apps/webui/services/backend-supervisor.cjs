// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Restarts GAIA's backend when it exits on its own: after a growing wait,
 * at most `maxRestarts` times in `windowMs`, then it stops and waits for the
 * user's Restart GAIA (restartNow). No Electron imports, so it unit-tests.
 *
 * State, as sent to the renderer:
 *   status        "running" | "restarting" | "stopped"
 *   port          the backend's port once running
 *   since         when this outage began (ms epoch), for the banner's timer
 *   attempt       which try this outage is on
 *   nextAttemptAt when the next try starts, or null while one is starting
 *   lastExit      { code, signal } of the last exit
 *   lastError     why the last start failed, if it did
 */

"use strict";

const DEFAULTS = Object.freeze({
  maxRestarts: 3,
  windowMs: 10 * 60_000,
  backoffMs: [1000, 5000, 15000],
});

/**
 * @param {object} deps
 * @param {() => Promise<{port: number}>} deps.start spawns the backend and resolves once it answers
 * @param {(state: object) => void} deps.onState called with every new state
 */
function createBackendSupervisor({
  start,
  onState,
  maxRestarts = DEFAULTS.maxRestarts,
  windowMs = DEFAULTS.windowMs,
  backoffMs = DEFAULTS.backoffMs,
  now = Date.now,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  if (!Number.isInteger(maxRestarts) || maxRestarts < 1) {
    throw new Error(`backend supervisor: maxRestarts must be a positive integer, got ${maxRestarts}`);
  }
  if (!Array.isArray(backoffMs) || backoffMs.length === 0) {
    throw new Error("backend supervisor: backoffMs must list at least one delay");
  }

  // Automatic restarts in the window; a user's Restart GAIA isn't counted.
  let history = [];
  let timer = null;
  let starting = false;
  let stopped = false;
  let state = { status: "running", port: null, since: null, attempt: 0, nextAttemptAt: null, lastExit: null, lastError: null };

  function emit(next) {
    state = { ...state, ...next, maxRestarts };
    onState({ ...state });
  }

  function attemptStart() {
    timer = null;
    starting = true;
    emit({ nextAttemptAt: null });
    let started;
    try {
      started = Promise.resolve(start());
    } catch (err) {
      started = Promise.reject(err);
    }
    started.then(
        ({ port }) => {
          starting = false;
          if (stopped) return;
          emit({ status: "running", port, since: null, attempt: 0, lastError: null });
        },
        (err) => {
          starting = false;
          if (stopped) return;
          schedule({ lastError: err && err.message ? err.message : String(err) });
        }
      );
  }

  function schedule(extra) {
    const t = now();
    history = history.filter((at) => t - at < windowMs);
    const outage = state.status === "running" ? { since: t, attempt: 0 } : {};
    if (history.length >= maxRestarts) {
      emit({ status: "stopped", nextAttemptAt: null, ...outage, ...extra });
      return "stopped";
    }
    const delay = backoffMs[Math.min(history.length, backoffMs.length - 1)];
    history.push(t + delay);
    const attempt = (outage.attempt ?? state.attempt) + 1;
    emit({ ...outage, status: "restarting", attempt, nextAttemptAt: t + delay, ...extra });
    timer = setTimer(attemptStart, delay);
    return "restarting";
  }

  return {
    /** The backend is up on `port` (the first start, outside the supervisor). */
    running(port) {
      emit({ status: "running", port, since: null, attempt: 0, nextAttemptAt: null, lastError: null });
    },

    /** The backend exited on its own. Returns what happens next. */
    crashed({ code = null, signal = null } = {}) {
      if (stopped) return "stopped";
      // An exit during a start is that start's failure, reported by start() itself.
      if (starting) return state.status;
      if (timer) return state.status;
      return schedule({ lastExit: { code, signal } });
    },

    /** The user's Restart GAIA: start now, with a fresh budget. */
    restartNow() {
      if (stopped || starting) return;
      if (timer) {
        clearTimer(timer);
        timer = null;
      }
      history = [];
      if (state.status === "running") return;
      emit({ status: "restarting", attempt: 1 });
      attemptStart();
    },

    /** GAIA is quitting: no more restarts. */
    stop() {
      stopped = true;
      if (timer) clearTimer(timer);
      timer = null;
    },

    getState() {
      return { ...state, maxRestarts };
    },
  };
}

module.exports = { createBackendSupervisor, DEFAULTS };
