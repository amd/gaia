// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * systemStore polling — issue #2007.
 *
 * The store must surface REAL metrics from the Electron IPC bridge
 * (window.gaiaAPI.system.getMetrics) and fail loudly — never silently
 * poll a source that returns nothing.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useSystemStore } from '../systemStore';
import type { SystemMetrics } from '../../types/agent';

const sampleMetrics = (overrides: Partial<SystemMetrics> = {}): SystemMetrics => ({
    cpuPercent: 37.5,
    memoryUsedGB: 12.4,
    memoryTotalGB: 32,
    diskUsedGB: 210.7,
    diskTotalGB: 512,
    networkUp: true,
    processes: [
        { pid: 1234, name: 'browser', cpuPercent: 2.1, memoryMB: 384.2, uptime: 3600 },
    ],
    timestamp: 1750000000000,
    ...overrides,
});

function resetStore() {
    useSystemStore.getState().stopPolling();
    useSystemStore.setState({
        metrics: null,
        metricsHistory: [],
        isPolling: false,
        lastError: null,
    });
}

describe('systemStore polling (issue #2007)', () => {
    beforeEach(() => {
        resetStore();
    });

    afterEach(() => {
        useSystemStore.getState().stopPolling();
        delete (window as { gaiaAPI?: unknown }).gaiaAPI;
        vi.restoreAllMocks();
    });

    it('populates real metrics from the IPC bridge on startPolling', async () => {
        const metrics = sampleMetrics();
        const getMetrics = vi.fn().mockResolvedValue(metrics);
        (window as { gaiaAPI?: unknown }).gaiaAPI = { system: { getMetrics } };

        useSystemStore.getState().startPolling();

        await vi.waitFor(() => {
            expect(useSystemStore.getState().metrics).not.toBeNull();
        });

        const state = useSystemStore.getState();
        expect(getMetrics).toHaveBeenCalled();
        expect(state.metrics).toEqual(metrics);
        expect(state.metrics?.cpuPercent).toBe(37.5);
        expect(state.metricsHistory).toHaveLength(1);
        expect(state.lastError).toBeNull();
        expect(state.isPolling).toBe(true);
    });

    it('fails loudly and stops polling when the IPC call rejects', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const getMetrics = vi
            .fn()
            .mockRejectedValue(new Error('statfs failed for /home: EIO'));
        (window as { gaiaAPI?: unknown }).gaiaAPI = { system: { getMetrics } };

        useSystemStore.getState().startPolling();

        await vi.waitFor(() => {
            expect(useSystemStore.getState().lastError).not.toBeNull();
        });

        const state = useSystemStore.getState();
        expect(state.lastError).toContain('statfs failed for /home: EIO');
        expect(state.metrics).toBeNull();
        expect(state.isPolling).toBe(false);
        expect(consoleError).toHaveBeenCalled();
    });

    it('fails loudly when the Electron API is not available (no silent null)', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        // No window.gaiaAPI at all — browser mode.

        useSystemStore.getState().startPolling();

        await vi.waitFor(() => {
            expect(useSystemStore.getState().lastError).not.toBeNull();
        });

        const state = useSystemStore.getState();
        // Actionable: names what is missing and where metrics come from.
        expect(state.lastError).toMatch(/gaiaAPI\.system/);
        expect(state.isPolling).toBe(false);
        expect(consoleError).toHaveBeenCalled();
    });

    it('clears lastError once a later poll succeeds', async () => {
        const getMetrics = vi.fn().mockResolvedValue(sampleMetrics());
        (window as { gaiaAPI?: unknown }).gaiaAPI = { system: { getMetrics } };
        useSystemStore.setState({ lastError: 'previous failure' });

        useSystemStore.getState().startPolling();

        await vi.waitFor(() => {
            expect(useSystemStore.getState().metrics).not.toBeNull();
        });
        expect(useSystemStore.getState().lastError).toBeNull();
    });
});
