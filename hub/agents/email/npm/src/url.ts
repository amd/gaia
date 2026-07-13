// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Linear-time slash trimming. The regex forms (`/\/+$/`, `/^\/+/`) backtrack
// polynomially on a long run of slashes from an untrusted URL (CodeQL
// js/polynomial-redos); a char scan is O(n) and allocation-free.

const SLASH = 47; // '/'

export function stripTrailingSlashes(s: string): string {
  let end = s.length;
  while (end > 0 && s.charCodeAt(end - 1) === SLASH) end -= 1;
  return s.slice(0, end);
}

export function stripLeadingSlashes(s: string): string {
  let start = 0;
  while (start < s.length && s.charCodeAt(start) === SLASH) start += 1;
  return s.slice(start);
}

export function joinUrl(base: string, file: string): string {
  return `${stripTrailingSlashes(base)}/${stripLeadingSlashes(file)}`;
}
