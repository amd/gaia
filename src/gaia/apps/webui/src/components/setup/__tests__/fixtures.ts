// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Plans and statuses shaped like the backend's (/api/setup/plan, /status, /api/gpu-memory). */

import type { GpuMemoryStatus, SetupCatalogEntry, SetupModelProgress, SetupPlan, SetupRunStatus } from '../../../types';

export function entry(over: Partial<SetupCatalogEntry> & { id: string }): SetupCatalogEntry {
    return {
        name: over.id,
        roles: ['chat'],
        purpose: 'Answers your chats',
        if_skipped: null,
        recipe: 'llamacpp',
        size_gb: 1,
        full_size_gb: 1,
        partial: false,
        partial_gb: 0,
        downloaded: false,
        in_lemonade: true,
        recommended: false,
        planned: false,
        gaia_model: true,
        selected: false,
        skipped: false,
        chat_choice: true,
        fits: true,
        need_gb: 2,
        capacity_gb: 96,
        fit_reason: null,
        ...over,
    };
}

export const QWEN = entry({
    id: 'Qwen3.6-35B-A3B-GGUF', name: 'Qwen3.6-35B-A3B', size_gb: 23.3, full_size_gb: 23.3,
    recommended: true, planned: true, selected: true, need_gb: 27.1,
});
export const GEMMA = entry({
    id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma-4-E4B-it', roles: ['vision'], size_gb: 5.97, full_size_gb: 5.97,
    recommended: true, planned: true, selected: true, need_gb: 8.4,
});
export const EMBED = entry({
    id: 'user.embeddinggemma-300m-GGUF', name: 'embeddinggemma-300m', roles: ['embedding'], size_gb: 0.3,
    full_size_gb: 0.3, recommended: true, planned: true, selected: true, chat_choice: false, need_gb: 0.5,
});
export const GPT_OSS = entry({ id: 'gpt-oss-20b-mxfp4-GGUF', name: 'gpt-oss 20B', size_gb: 12.1, full_size_gb: 12.1, need_gb: 14 });
export const FLM = entry({
    id: 'gemma4-it-e2b-FLM', name: 'Gemma 4 E2B (NPU)', recipe: 'flm', downloaded: true, size_gb: 0,
    full_size_gb: 3.2, gaia_model: false, fits: null, need_gb: null, capacity_gb: null,
});

export function plan(catalog: SetupCatalogEntry[], over: Partial<SetupPlan> = {}): SetupPlan {
    const chat = catalog.find((e) => e.planned && e.roles.includes('chat')) ?? catalog.find((e) => e.chat_choice);
    const selected = catalog.filter((e) => e.selected);
    return {
        lemonade: { mode: 'gaia', url: 'http://127.0.0.1:13305/api/v1', reachable: true },
        chat_model: { id: chat?.id ?? 'Gemma-4-E4B-it-GGUF', user_set: false, size_gb: chat?.size_gb ?? null, downloaded: chat?.downloaded ?? null },
        recommended_model: catalog.find((e) => e.recommended && e.roles.includes('chat'))?.id ?? null,
        decided_after_server: false,
        memory_gb: 96,
        memory_source: 'AMD iGPU',
        capacity_gb: 96,
        options: [],
        models: selected.map((e) => ({ id: e.id, role: e.roles.includes('embedding') ? 'embedding' : 'chat', size_gb: e.size_gb })),
        download_gb: Math.round(selected.reduce((s, e) => s + (e.size_gb ?? 0), 0) * 10) / 10,
        disk: { path: 'C:\\Users\\me\\.cache\\huggingface\\hub', free_gb: 412.3, source: 'lemonade' },
        disk_warning: null,
        skipped_models: catalog.filter((e) => e.skipped).map((e) => e.id),
        catalog,
        ...over,
    };
}

/** A big Strix Halo box with nothing downloaded: the default first run. */
export const COLD_BIG = () => plan([QWEN, GEMMA, EMBED, GPT_OSS]);

export function progress(over: Partial<SetupModelProgress> & { id: string }): SetupModelProgress {
    return {
        roles: ['chat'],
        state: 'queued',
        bytes_done: 0,
        bytes_total: 1e9,
        speed_bps: 0,
        eta_s: null,
        error: null,
        error_kind: null,
        can_retry: false,
        retrying_until: null,
        downloaded_before_gb: null,
        ...over,
    };
}

export function runStatus(models: SetupModelProgress[], over: Partial<SetupRunStatus> = {}): SetupRunStatus {
    const counted = models.filter((m) => m.state !== 'skipped');
    const done = counted.reduce((s, m) => s + Math.min(m.bytes_done, m.bytes_total), 0);
    const total = counted.reduce((s, m) => s + m.bytes_total, 0);
    return {
        state: 'running',
        steps: [
            { key: 'server', label: 'Install the local model server', detail: '', status: 'done' },
            { key: 'models', label: 'Download the models', detail: '', status: 'active' },
            { key: 'finish', label: 'Check that the models load', detail: '', status: 'pending' },
        ],
        text: 'Downloading the models',
        models,
        overall: {
            bytes_done: done, bytes_total: total, speed_bps: 0, eta_s: null,
            percent: total ? Math.floor((done * 100) / total) : null,
            failed: counted.filter((m) => m.state === 'failed').length,
        },
        command: 'gaia init --profile gaia',
        log_tail: [],
        skip_chat_model: false,
        chat_model: 'Qwen3.6-35B-A3B-GGUF',
        ...over,
    };
}

/** Strix Halo on Linux with 128 GB: GAIA can apply a larger GPU share. */
export function gpuStatus(over: Partial<GpuMemoryStatus> = {}): GpuMemoryStatus {
    return {
        applies: true,
        show_in_setup: true,
        hidden_reason: null,
        os: 'linux',
        method: 'apply',
        method_reason: '',
        total_gb: 128,
        gpu_gb: 32,
        cpu_gb: 96,
        setting: { name: 'GPU memory limit (GTT)', current_gb: 32, min_gb: 4, max_gb: 112 },
        reserve_gb: 16,
        needs_admin: true,
        needs_restart: true,
        presets: [
            { setting_gb: 48, gpu_gb: 48, unlocks: [] },
            { setting_gb: 64, gpu_gb: 64, unlocks: ['Qwen3.6-35B-A3B-GGUF'] },
            { setting_gb: 96, gpu_gb: 96, unlocks: ['Qwen3.6-35B-A3B-GGUF', 'gpt-oss-120b-mxfp4-GGUF'] },
        ],
        recommended_setting_gb: 64,
        models: [],
        pending: null,
        revert: { available: false, previous_setting_gb: null },
        guided_steps: [
            "Run: echo 'options ttm pages_limit=15625000' | sudo tee /etc/modprobe.d/ttm.conf",
            'Rebuild the boot image. Ubuntu: sudo update-initramfs -u',
            'Restart your PC, then come back here. GAIA reads the new split.',
        ],
        ...over,
    };
}
