// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api');

import * as api from '../api';
import { installFlagshipAgent } from '../flagshipInstall';
import { isFlagshipMissingError } from '../../utils/newTask';

const mocked = vi.mocked(api);
const status = (over: Partial<api.AgentInstallStatus>): api.AgentInstallStatus => ({
    id: 'gaia', status: 'queued', phase: 'queued', percent: 0, version: null, error: null, ...over,
});

afterEach(() => { vi.clearAllMocks(); });

describe('installFlagshipAgent', () => {
    it('starts the install and polls until it completes', async () => {
        mocked.installAgent.mockResolvedValue({ id: 'gaia', status: 'queued' });
        mocked.getAgentInstallStatus
            .mockResolvedValueOnce(status({ status: 'running', percent: 50 }))
            .mockResolvedValueOnce(status({ status: 'completed', percent: 100 }));
        const progress = vi.fn();
        await installFlagshipAgent({ onProgress: progress, pollMs: 0 });
        expect(mocked.installAgent).toHaveBeenCalledExactlyOnceWith('gaia');
        expect(progress.mock.calls.map(([s]) => s.percent)).toEqual([50, 100]);
    });

    it('waits on an install this backend is already running (409) instead of failing', async () => {
        mocked.installAgent.mockRejectedValue(Object.assign(new Error('already in progress'), { httpStatus: 409 }));
        mocked.getAgentInstallStatus
            .mockResolvedValueOnce(status({ status: 'running', percent: 40 }))
            .mockResolvedValueOnce(status({ status: 'completed', percent: 100 }));
        await expect(installFlagshipAgent({ pollMs: 0 })).resolves.toBeUndefined();
    });

    it('a 409 this backend has no status for (another process installing) reports the 409', async () => {
        mocked.installAgent.mockRejectedValue(
            Object.assign(new Error('in progress in another GAIA process'), { httpStatus: 409 }),
        );
        mocked.getAgentInstallStatus.mockRejectedValue(
            Object.assign(new Error("No install status for 'gaia'."), { httpStatus: 404 }),
        );
        await expect(installFlagshipAgent({ pollMs: 0 })).rejects.toThrow('in progress in another GAIA process');
    });

    it.each(['completed', 'failed'])('a 409 with a stale %s status (core too old) reports the 409', async (stale) => {
        mocked.installAgent.mockRejectedValue(Object.assign(new Error('needs a newer GAIA'), { httpStatus: 409 }));
        mocked.getAgentInstallStatus.mockResolvedValue(status({ status: stale }));
        await expect(installFlagshipAgent({ pollMs: 0 })).rejects.toThrow('needs a newer GAIA');
        expect(mocked.getAgentInstallStatus).toHaveBeenCalledOnce();
    });

    it('fails with the backend reason', async () => {
        mocked.installAgent.mockResolvedValue({ id: 'gaia', status: 'queued' });
        mocked.getAgentInstallStatus.mockResolvedValue(status({ status: 'failed', error: 'no build for this machine' }));
        await expect(installFlagshipAgent({ pollMs: 0 })).rejects.toThrow('no build for this machine');
    });

    it('a refused start (e.g. hub unreachable, 502) is reported, not polled', async () => {
        mocked.installAgent.mockRejectedValue(Object.assign(new Error('hub unreachable'), { httpStatus: 502 }));
        await expect(installFlagshipAgent({ pollMs: 0 })).rejects.toThrow('hub unreachable');
        expect(mocked.getAgentInstallStatus).not.toHaveBeenCalled();
    });
});

describe('isFlagshipMissingError', () => {
    it("matches the backend's 422 for the flagship only", () => {
        const err = (httpStatus: number, detail: string) => Object.assign(new Error(detail), { httpStatus, detail });
        expect(isFlagshipMissingError(err(422, "Unknown agent_type 'gaia'. Registered agent ids: chat."))).toBe(true);
        expect(isFlagshipMissingError(err(422, "Unknown agent_type 'doc'."))).toBe(false);
        expect(isFlagshipMissingError(err(500, "Unknown agent_type 'gaia'."))).toBe(false);
        expect(isFlagshipMissingError(new TypeError('Failed to fetch'))).toBe(false);
        expect(isFlagshipMissingError(null)).toBe(false);
    });
});
