// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Shared formatting utilities for the GAIA web UI. */

/** A long file name cut in the middle, so its end and extension stay: "a_very_long_fi…ed_by_dana.txt". */
export function middleTruncate(name: string, max = 32): string {
  // Code points, so an emoji is never split into a lone surrogate.
  const chars = Array.from(name);
  if (chars.length <= max) return name;
  const ext = Array.from(/\.[^.\s]{1,8}$/.exec(name)?.[0] ?? '').length;
  const tail = Math.min(Math.max(Math.ceil(max * 0.45), ext + 4), max - 2);
  return `${chars.slice(0, max - tail - 1).join('')}…${chars.slice(-tail).join('')}`;
}

/** Format bytes as human-readable size (e.g., "3.2 MB"). */
export function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/**
 * Get a short hash for a session ID (for linking/troubleshooting).
 * Strips hyphens from the UUID and returns the first 7 characters.
 * Example: "550e8400-e29b-41d4-..." → "550e840"
 */
export function getSessionHash(sessionId: string): string {
  return sessionId.replace(/-/g, '').slice(0, 7);
}

/**
 * Find a session by its short hash (first 7 hex chars of UUID).
 * Returns the matching session ID or null if not found.
 */
export function findSessionByHash(sessions: { id: string }[], hash: string): string | null {
  const normalizedHash = hash.replace(/^#/, '').toLowerCase();
  if (!normalizedHash || normalizedHash.length < 4) return null;
  const match = sessions.find((s) => s.id.replace(/-/g, '').toLowerCase().startsWith(normalizedHash));
  return match ? match.id : null;
}
