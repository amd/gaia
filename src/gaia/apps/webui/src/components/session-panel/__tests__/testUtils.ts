// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { vi } from 'vitest';
import * as api from '../../../services/api';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import { resetSessionEnvironmentCache } from '../../../hooks/useSessionEnvironment';
import type { SessionChanges, SessionEnvironment, SessionGit } from '../../../types/sessionPanel';

export const SID = 'session-1';

export const ENV: SessionEnvironment = {
    session_id: SID,
    folders: [
        { path: 'C:\\Users\\me\\project', kind: 'folder', source: 'added', removable: true },
        { path: 'C:\\Users\\me\\.gaia\\documents', kind: 'folder', source: 'gaia_documents', removable: true },
        { path: 'C:\\Users\\me\\report.pdf', kind: 'file', source: 'document', removable: false },
    ],
    removed: [],
    internet: true,
    working_folder: null,
    enforced: true,
    internet_enforced: true,
};

export const NO_CHANGES: SessionChanges = {
    files: [],
    totals: { files: 0, additions: 0, deletions: 0, uncounted: 0 },
    turns: 0,
    pending: false,
    untracked: [],
    incomplete: false,
};

export const NO_GIT: SessionGit = { available: true, repos: [] };

/** Stand-ins for every call the panel makes, each returning a quiet default. */
export function stubPanelApi() {
    const m = vi.mocked(api);
    m.getEnvironment.mockResolvedValue(ENV);
    m.getOutputs.mockResolvedValue({ session_id: SID, items: [] });
    m.getSessionChanges.mockResolvedValue(NO_CHANGES);
    m.getSessionGit.mockResolvedValue(NO_GIT);
    m.getLocalModelStatus.mockResolvedValue({
        state: 'loaded',
        active_model: 'Gemma-4-E4B-it-GGUF',
        active_display_name: 'Gemma 4 E4B',
        active_remote: false,
        model: 'Gemma-4-E4B-it-GGUF',
        display_name: 'Gemma 4 E4B',
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'lemonade', warning: null },
        ctx_size: 65536,
        memory: { source: 'lemonade', total_gb: 64, free_gb: 40.5, model_gb: 6.2 },
        memory_error: null,
        error: null,
        base_url: null,
        owner: 'gaia',
        busy: false,
        restarting: false,
        restart_error: null,
    } as never);
    m.getTunnelStatus.mockResolvedValue({
        active: false, url: null, token: null, startedAt: null, error: null, publicIp: null, device: null,
    });
    m.getPermissions.mockResolvedValue({ session_id: SID, mode: 'ask', grants: [] });
    m.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' } as never);
    m.getChatPerformance.mockResolvedValue({
        latest_message_id: null,
        turns: [],
        totals: {
            turns: 0, wall_seconds: 0, model_seconds: 0, gaia_seconds: 0, tokens_in: 0, tokens_out: 0,
            tokens_cached: 0, tool_calls: 0, loads: 0, load_seconds: 0, usd: 0, unpriced_turns: 0,
        },
        now: { provider: 'local', model: 'm', loaded: true, device: 'gpu', backend: 'vulkan', ctx_size: 65536, weights_gb: null, kv_cache_gb: null, free_gb: null, error: null },
    } as never);
    return m;
}

export function resetPanelState() {
    window.localStorage.clear();
    useSessionPanelStore.getState().reload();
    resetSessionEnvironmentCache();
}

export function stubWindowWidth(narrow: boolean) {
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            matches: narrow && query.includes('max-width'),
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }),
    });
}
