// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Minimal namespaced logger. Debug output is OFF by default and enabled via the
 * `DEBUG` env var (substring match on `agent-email`, or `*`). Everything goes to
 * stderr so it never corrupts machine-readable stdout (e.g. a `--print-openapi`
 * pipe or a CLI that emits JSON).
 */

function debugEnabled(): boolean {
  // Guard against environments where `process` is not defined (e.g. browsers
  // that don't polyfill it). `globalThis.process` is undefined in a raw browser
  // context; bundlers that inject a process shim make this safe automatically,
  // but we can't rely on that for the browser-safe ./client entry.
  const d =
    (typeof process !== "undefined" && process.env?.["DEBUG"]) || "";
  return d === "*" || d.includes("agent-email");
}

export interface Logger {
  debug(msg: string, ...rest: unknown[]): void;
  info(msg: string, ...rest: unknown[]): void;
  warn(msg: string, ...rest: unknown[]): void;
  error(msg: string, ...rest: unknown[]): void;
}

export function createLogger(namespace: string): Logger {
  const tag = `[agent-email:${namespace}]`;
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
