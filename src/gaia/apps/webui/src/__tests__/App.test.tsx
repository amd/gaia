// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * App session-navigation tests (issues #1086 / #1750).
 *
 * Exercises resolveUrlNavTarget — the guard + match used by App.tsx's URL-nav
 * effect — including the short-hash same-session case that caused the #1750
 * session-activation oscillation (a `#<short>` URL for the current session must
 * NOT trigger a switch).
 */

import { describe, it, expect } from 'vitest';
import { resolveUrlNavTarget } from '../utils/sessionNav';
import { getSessionHash } from '../utils/format';

const A = '550e8400-e29b-41d4-a716-446655440000';
const B = '6ba7b810-9dad-11d1-80b4-00c04fd430c8';
const sessions = [{ id: A }, { id: B }];

describe('resolveUrlNavTarget (URL session navigation guard)', () => {
    it('returns null when there is no target', () => {
        expect(resolveUrlNavTarget('', A, sessions)).toBeNull();
        expect(resolveUrlNavTarget(null, A, sessions)).toBeNull();
    });

    it('returns null when already on the target session (full id)', () => {
        expect(resolveUrlNavTarget(A, A, sessions)).toBeNull();
    });

    it('returns null when target is the SHORT HASH of the current session (#1750)', () => {
        // The oscillation bug: the app writes a short hash to the URL, so the
        // guard must recognise it as "already here" and not switch.
        expect(resolveUrlNavTarget(getSessionHash(A), A, sessions)).toBeNull();
    });

    it('switches to a different session by full id', () => {
        expect(resolveUrlNavTarget(B, A, sessions)).toBe(B);
    });

    it('switches to a different session by short hash', () => {
        expect(resolveUrlNavTarget(getSessionHash(B), A, sessions)).toBe(B);
    });

    it('returns null for an unknown target', () => {
        expect(resolveUrlNavTarget('notarealsession', A, sessions)).toBeNull();
    });

    it('navigates when no session is currently active', () => {
        expect(resolveUrlNavTarget(getSessionHash(B), null, sessions)).toBe(B);
    });
});
