// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getSessionHash, findSessionByHash } from './format';

/**
 * Decide which session (if any) a URL navigation target should switch to.
 *
 * Returns the session id to switch to, or null when no switch should happen:
 * no target, already on that session, or the target matches no loaded session.
 *
 * The "already on it" check compares BOTH the full id and the short hash, so a
 * hash-based URL for the current session (`#<short>`) does not trigger a
 * redundant switch — the bug behind the #1750 session oscillation.
 */
export function resolveUrlNavTarget(
    target: string | null | undefined,
    currentSessionId: string | null,
    sessions: { id: string }[],
): string | null {
    if (!target) return null;
    if (
        currentSessionId &&
        (target === currentSessionId || target === getSessionHash(currentSessionId))
    ) {
        return null;
    }
    if (sessions.some((s) => s.id === target)) return target;
    return findSessionByHash(sessions, target);
}

/** The session a URL names: `?session=<id>` wins over `#<hash>`. */
export function readUrlTarget(location: Pick<Location, 'search' | 'hash'>): string | null {
    const param = new URLSearchParams(location.search).get('session');
    return param || location.hash.replace(/^#/, '') || null;
}
