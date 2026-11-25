// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Answers for the model-setup and GPU-memory calls Settings > Model makes, for
 * suites that mock `services/api` but aren't about those sections: a model
 * server that isn't answering, no GPU split, nothing downloading.
 */

import type { MockedObject } from 'vitest';
import type * as api from '../services/api';
import type { AgentDownload, AgentPrograms, GpuMemoryStatus, Settings, SetupPlan } from '../types';
import type { ModelServerInfo } from '../types/api.gen';
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

/** A model server that names no backends, so Customize's backend choice has nothing to offer. */
export const NO_BACKENDS = { reachable: true, available_backends: [], preferred_backend: null, default_backend: null } as unknown as ModelServerInfo;

/** Settings as a fresh install reads them: the Python agent, the native one installed too. */
export const SETTINGS: Settings = {
    custom_model: null, model_status: null, context_size: null, dynamic_tools: false, dynamic_tools_locked: false,
    agent_mode: 'goal_driven', network_policy: 'cloud_allowed', flagship_agent: 'gaiapy', gaiacpp_available: true,
};

export const NO_AGENT_DOWNLOAD: AgentDownload = { state: 'idle', bytes_done: 0, bytes_total: null, error: null };

/** Both agent programs on this PC, the Python one in use. */
export const AGENT_PROGRAMS: AgentPrograms = {
    current: 'gaiapy',
    programs: [
        { id: 'gaiapy', installed: true, size_bytes: 412_000_000, can_download: false, unavailable_reason: null },
        { id: 'gaiacpp', installed: true, size_bytes: 6_800_000, can_download: false, unavailable_reason: null },
    ],
    download: NO_AGENT_DOWNLOAD,
};

/** gaiacpp isn't on this PC: `reason` null means it can be downloaded, else why it can't. */
export function withoutGaiacpp(reason: string | null = null): AgentPrograms {
    return {
        ...AGENT_PROGRAMS,
        programs: AGENT_PROGRAMS.programs.map((p) => (p.id === 'gaiacpp'
            ? { ...p, installed: false, can_download: reason === null, unavailable_reason: reason, size_bytes: reason === null ? p.size_bytes : null }
            : p)),
    };
}

export function mockSetupApis(mocked: MockedObject<typeof api>): void {
    resetSetupDownloads();
    mocked.getSetupPlan.mockResolvedValue(NO_SERVER_PLAN);
    mocked.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mocked.getModelServer.mockResolvedValue(NO_BACKENDS);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.getSettings.mockResolvedValue(SETTINGS);
    mocked.getAgentPrograms.mockResolvedValue(AGENT_PROGRAMS);
}
