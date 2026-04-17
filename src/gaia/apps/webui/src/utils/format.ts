// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Shared formatting utilities for the GAIA web UI. */

/** Format bytes as human-readable size (e.g., "3.2 MB"). */
export function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/** Format a duration in seconds as human-readable uptime (e.g., "2h 15m", "3d 4h"). */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `${min}m`;
  const hrs = Math.floor(min / 60);
  const remMin = min % 60;
  if (hrs < 24) return `${hrs}h ${remMin}m`;
  const days = Math.floor(hrs / 24);
  return `${days}d ${hrs % 24}h`;
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

/** Format a timestamp as HH:MM:SS (24-hour, no ms). */
export function formatTimeHMS(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}
