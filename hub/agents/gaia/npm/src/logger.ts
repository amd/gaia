// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Minimal namespaced logger. Debug output is OFF by default and enabled via the
 * `DEBUG` env var (substring match on `gaia`, or `*`). Everything goes to stderr
 * so it never corrupts stdout — which the TUI owns once it is exec'd.
 */

function debugEnabled(): boolean {
  const d = (typeof process !== "undefined" && process.env?.["DEBUG"]) || "";
  return d === "*" || d.includes("gaia");
}

export interface Logger {
  debug(msg: string, ...rest: unknown[]): void;
  info(msg: string, ...rest: unknown[]): void;
  warn(msg: string, ...rest: unknown[]): void;
  error(msg: string, ...rest: unknown[]): void;
}

export function createLogger(namespace: string): Logger {
  const tag = `[gaia:${namespace}]`;
  return {
    debug(msg, ...rest) {
      if (debugEnabled()) console.error(`${tag} ${msg}`, ...rest);
    },
    info(msg, ...rest) {
      console.error(`${tag} ${msg}`, ...rest);
    },
    warn(msg, ...rest) {
      console.error(`${tag} WARN ${msg}`, ...rest);
    },
    error(msg, ...rest) {
      console.error(`${tag} ERROR ${msg}`, ...rest);
    },
  };
}
