// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Model choice, download progress and the GPU memory split (/api/setup/*, /api/gpu-memory). */

export type SetupModelState = 'queued' | 'downloading' | 'verifying' | 'ready' | 'failed' | 'skipped' | 'cancelled';

/** One model's download in GET /api/setup/status. Sizes in bytes; speed in bytes/s. */
export interface SetupModelProgress {
    id: string;
    roles: SetupRole[];
    state: SetupModelState;
    bytes_done: number;
    bytes_total: number;
    speed_bps: number;
    eta_s: number | null;
    error: string | null;
    error_kind: 'network' | 'disk_full' | 'lemonade' | 'verify' | null;
    can_retry: boolean;
    /** Unix seconds while a keep-trying wait runs; the state stays `downloading`. */
    retrying_until: number | null;
    downloaded_before_gb: number | null;
}

export interface SetupOverall {
    bytes_done: number;
    bytes_total: number;
    speed_bps: number;
    eta_s: number | null;
    percent: number | null;
    failed: number;
}

export type SetupRole = 'chat' | 'vision' | 'embedding';

/** One model setup can pull or use (GET /api/setup/plan `catalog`). Every `*_gb` is decimal GB. */
export interface SetupCatalogEntry {
    id: string;
    name: string;
    roles: SetupRole[];
    purpose: string;
    if_skipped: string | null;
    recipe: string | null;
    /** GB left to download: 0 when downloaded, null when unknown. */
    size_gb: number | null;
    full_size_gb: number | null;
    partial: boolean;
    partial_gb: number;
    /** null when the server isn't reachable. */
    downloaded: boolean | null;
    in_lemonade: boolean;
    recommended: boolean;
    planned: boolean;
    /** GAIA's own model, not one only this Lemonade has. */
    gaia_model: boolean;
    selected: boolean;
    skipped: boolean;
    chat_choice: boolean;
    /** null when this PC's memory couldn't be read: no invented numbers. */
    fits: boolean | null;
    need_gb: number | null;
    capacity_gb: number | null;
    fit_reason: string | null;
}

/** A 409 from a setup choice that asks to be confirmed, then sent again with `acknowledge` true. */
export interface SetupConfirm {
    code: 'no_fit' | 'low_disk';
    message: string;
    acknowledge: 'acknowledge_no_fit' | 'acknowledge_low_disk';
    model_id?: string;
    need_gb?: number | null;
    capacity_gb?: number | null;
    free_gb?: number | null;
    free_up_gb?: number | null;
    path?: string | null;
    drop?: string[];
}

// ── GPU memory split (/api/gpu-memory) ─────────────────────────────────

export interface GpuMemoryPending {
    setting_gb: number;
    previous_setting_gb: number | null;
    source: 'apply' | 'revert' | 'guided' | null;
    recorded_at: string | null;
    state: 'restart_required' | 'took_effect' | 'not_applied';
}

export interface GpuMemoryPreset {
    setting_gb: number;
    gpu_gb: number;
    unlocks: string[];
}

/** GET /api/gpu-memory. Without `applies` only the first six fields are sent. */
export interface GpuMemoryStatus {
    applies: boolean;
    show_in_setup: boolean;
    hidden_reason: string | null;
    os: 'linux' | 'windows' | 'darwin' | string;
    method: 'apply' | 'guided' | 'unsupported';
    method_reason: string;
    total_gb?: number;
    gpu_gb?: number;
    cpu_gb?: number;
    setting?: { name: string; current_gb: number; min_gb: number; max_gb: number };
    reserve_gb?: number;
    needs_admin?: boolean;
    needs_restart?: boolean;
    presets?: GpuMemoryPreset[];
    recommended_setting_gb?: number | null;
    models?: Array<{ id: string; size_gb: number; need_gb: number; default: boolean; fits_now: boolean; fits_at_setting_gb: number | null }>;
    pending?: GpuMemoryPending | null;
    revert?: { available: boolean; previous_setting_gb: number | null };
    guided_steps?: string[];
}
