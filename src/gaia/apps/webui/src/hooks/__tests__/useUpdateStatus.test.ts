// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUpdateStatus, INITIAL_STATE } from '../useUpdateStatus';
import type { UpdateState } from '../useUpdateStatus';

// ── Bridge stub ─────────────────────────────────────────────────────────────

function makeUpdaterBridge(overrides: Partial<{
    getStatus: () => Promise<UpdateState>;
    check: () => Promise<UpdateState>;
    onStatusChange: (cb: (s: UpdateState) => void) => () => void;
    listReleases: () => Promise<unknown>;
    installVersion: (tag: string) => Promise<void>;
    resumeUpdates: () => Promise<UpdateState>;
}> = {}) {
    return {
        getStatus: vi.fn(async () => ({
            status: 'idle' as const,
            version: null,
            progress: 0,
            releaseNotes: null,
            error: null,
            currentVersion: '0.21.0',
            pinnedVersion: null,
        })),
        check: vi.fn(async () => ({
            status: 'checking' as const,
            version: null,
            progress: 0,
            releaseNotes: null,
            error: null,
            currentVersion: '0.21.0',
            pinnedVersion: null,
        })),
        onStatusChange: vi.fn((_cb: (s: UpdateState) => void) => () => {}),
        listReleases: vi.fn(async () => []),
        installVersion: vi.fn(async (_tag: string) => {}),
        resumeUpdates: vi.fn(async () => ({
            status: 'idle' as const,
            version: null,
            progress: 0,
            releaseNotes: null,
            error: null,
            currentVersion: '0.21.0',
            pinnedVersion: null,
        })),
        ...overrides,
    };
}

beforeEach(() => {
    vi.stubGlobal('gaiaUpdater', undefined);
});

afterEach(() => {
    vi.unstubAllGlobals();
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe('useUpdateStatus — no bridge', () => {
    it('returns INITIAL_STATE when window.gaiaUpdater is absent', () => {
        const { result } = renderHook(() => useUpdateStatus());
        expect(result.current.status).toBe('idle');
        expect(result.current.version).toBeNull();
    });

    it('includes currentVersion and pinnedVersion in state', () => {
        const { result } = renderHook(() => useUpdateStatus());
        expect(result.current).toHaveProperty('currentVersion');
        expect(result.current).toHaveProperty('pinnedVersion');
    });
});

describe('useUpdateStatus — with bridge', () => {
    it('fetches initial status and surfaces currentVersion', async () => {
        const bridge = makeUpdaterBridge();
        vi.stubGlobal('gaiaUpdater', bridge);

        const { result } = renderHook(() => useUpdateStatus());

        await act(async () => {
            await new Promise((r) => setTimeout(r, 0));
        });

        expect(bridge.getStatus).toHaveBeenCalled();
        expect(result.current.currentVersion).toBe('0.21.0');
        expect(result.current.pinnedVersion).toBeNull();
    });

    it('subscribes to onStatusChange and reflects pushed updates', async () => {
        let captured: ((s: UpdateState) => void) | null = null;
        const bridge = makeUpdaterBridge({
            onStatusChange: vi.fn((cb) => {
                captured = cb;
                return () => {};
            }),
        });
        vi.stubGlobal('gaiaUpdater', bridge);

        const { result } = renderHook(() => useUpdateStatus());

        await act(async () => {
            await new Promise((r) => setTimeout(r, 0));
        });

        const newState: UpdateState = {
            status: 'available',
            version: '0.22.0',
            progress: 0,
            releaseNotes: null,
            error: null,
            currentVersion: '0.21.0',
            pinnedVersion: null,
        };

        await act(async () => {
            captured!(newState);
        });

        expect(result.current.status).toBe('available');
        expect(result.current.version).toBe('0.22.0');
    });
});

describe('INITIAL_STATE', () => {
    it('has currentVersion and pinnedVersion fields', () => {
        expect(INITIAL_STATE).toHaveProperty('currentVersion');
        expect(INITIAL_STATE).toHaveProperty('pinnedVersion');
        expect(INITIAL_STATE.currentVersion).toBeNull();
        expect(INITIAL_STATE.pinnedVersion).toBeNull();
    });
});
