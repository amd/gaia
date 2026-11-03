// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Answers for the model-setup and GPU-memory calls Settings > Model makes, for
 * suites that mock `services/api` but aren't about those sections: a model
 * server that isn't answering, no GPU split, nothing downloading.
 */

import type { MockedObject } from 'vitest';
import type * as api from '../services/api';
import type { GpuMemoryStatus, SetupPlan } from '../types';
import { resetSetupDownloads } from '../stores/setupDownloadsStore';

export const NO_SERVER_PLAN: SetupPlan = {
    lemonade: { mode: 'gaia', url: null, reachable: false },
    chat_model: { id: 'Gemma-4-E4B-it-GGUF', user_set: false, size_gb: 5.97, downloaded: null },
    recommended_model: null,
    decided_after_server: true,
    memory_gb: null,
    memory_source: null,
    options: [],
    models: [],
    download_gb: null,
    disk: { path: null, free_gb: null, source: null },
    disk_warning: null,
    skipped_models: [],
    catalog: [],
};

export const NO_GPU_SPLIT: GpuMemoryStatus = {
    applies: false,
    show_in_setup: false,
    hidden_reason: 'This PC’s GPU has its own memory, so there is nothing to split.',
    os: 'windows',
    method: 'unsupported',
    method_reason: 'This PC’s GPU has its own memory, so there is nothing to split.',
};

export function mockSetupApis(mocked: MockedObject<typeof api>): void {
    resetSetupDownloads();
    mocked.getSetupPlan.mockResolvedValue(NO_SERVER_PLAN);
    mocked.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
}
