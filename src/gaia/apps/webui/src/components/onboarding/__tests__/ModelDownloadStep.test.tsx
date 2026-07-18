// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { ModelDownloadStep } from '../ModelDownloadStep';
import type { DownloadProgress, SystemStatus } from '../../../types';

vi.mock('../../../services/api', () => ({
    getSystemStatus: vi.fn(),
    downloadModel: vi.fn(),
}));

import * as api from '../../../services/api';

const MODEL = 'Gemma-4-E4B-it-GGUF';
const POLL_MS = 2000;

function status(over: Partial<SystemStatus>): SystemStatus {
    return {
        lemonade_running: true,
        model_loaded: null,
        embedding_model_loaded: false,
        disk_space_gb: 500,
        memory_available_gb: 32,
        initialized: false,
        version: '0.0.0-test',
        lemonade_version: null,
        model_size_gb: null,
        model_device: null,
        model_context_size: null,
        model_labels: null,
        gpu_name: null,
        gpu_vram_gb: null,
        tokens_per_second: null,
        time_to_first_token: null,
        processor_name: null,
        device_supported: true,
        context_size_sufficient: true,
        model_downloaded: null,
        default_model_name: MODEL,
        default_model_size_gb: 6,
        lemonade_url: null,
        expected_model_loaded: false,
        download_progress: null,
        ...over,
    };
}

function downloading(percent: number): DownloadProgress {
    return {
        state: 'downloading',
        model_name: MODEL,
        percent,
        file: 'model.gguf',
        file_index: 0,
        total_files: 1,
        downloaded_bytes: percent * 1e7,
        total_bytes: 1e9,
        message: null,
    };
}

// Flush the mount poll's promise chain (getSystemStatus -> setState -> .then).
async function flushMicrotasks() {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
});

afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
});

describe('ModelDownloadStep', () => {
    // Regression (#2204): a wizard that remounts mid-download (refresh / Back nav)
    // must resume polling instead of freezing on the first progress frame.
    it('resumes polling on remount when a download is already in flight', async () => {
        let calls = 0;
        vi.mocked(api.getSystemStatus).mockImplementation(async () => {
            calls += 1;
            if (calls >= 3) return status({ download_progress: { ...downloading(100), state: 'complete' } });
            return status({ download_progress: downloading(calls * 20) });
        });
        const onGuardChange = vi.fn();

        // Fresh mount — no Download click.
        render(<ModelDownloadStep modelName={MODEL} onGuardChange={onGuardChange} />);

        // Mount poll sees an in-progress download and renders the bar.
        await flushMicrotasks();
        expect(api.getSystemStatus).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId('download-progress')).toBeInTheDocument();

        // The interval keeps polling (the frozen bug never got here).
        await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
        expect(api.getSystemStatus).toHaveBeenCalledTimes(2);

        // Once the backend reports complete, the step reflects completion and unblocks Next.
        await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
        expect(api.getSystemStatus).toHaveBeenCalledTimes(3);
        expect(screen.getByTestId('onboarding-download-done')).toBeInTheDocument();
        expect(onGuardChange).toHaveBeenLastCalledWith(false);
    });

    it('does not start polling on mount when no download is active', async () => {
        vi.mocked(api.getSystemStatus).mockResolvedValue(status({ download_progress: null }));

        render(<ModelDownloadStep modelName={MODEL} onGuardChange={vi.fn()} />);

        await flushMicrotasks();
        expect(api.getSystemStatus).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId('download-start')).toBeInTheDocument();

        // No interval was armed — advancing time triggers no further polls.
        await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
        expect(api.getSystemStatus).toHaveBeenCalledTimes(1);
    });
});
