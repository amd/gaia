// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** First-run setup (`/api/setup/*`, the same `gaia init` the TUI runs) and the GPU memory split (`/api/gpu-memory`). */

import type { SetupCheck, SetupRunStatus, SetupPlan, StartScreen, PreflightReport, SetupConfirm, GpuMemoryStatus } from '../types';
import { ApiError, apiFetch } from './api';

/** The confirmation a setup choice's 409 asks for, or null for any other error. */
export function setupConfirmFrom(err: unknown): SetupConfirm | null {
    if (!(err instanceof ApiError) || err.httpStatus !== 409 || !err.body) return null;
    const { code, acknowledge } = err.body;
    if ((code === 'no_fit' || code === 'low_disk') && typeof acknowledge === 'string') {
        return err.body as unknown as SetupConfirm;
    }
    return null;
}

// -- First-run setup (same `gaia init` the TUI runs) ----------------------------

/** Hardware report (memory, disk, GPU, NPU) shown on the setup screen. */
export async function getOnboardingPreflight(): Promise<PreflightReport> {
    return apiFetch<PreflightReport>('GET', '/onboarding/preflight');
}

export async function checkSetup(skipChatModel = false): Promise<SetupCheck> {
    return apiFetch('GET', `/setup/check?skip_chat_model=${skipChatModel}`);
}

export interface SetupRunOptions {
    /** Download past a too-small model drive (the user confirmed the 409). */
    acknowledgeLowDisk?: boolean;
    /** Wait out a dropped connection for up to two minutes. Off unless the user ticks it. */
    keepTrying?: boolean;
    /** Models the user ticked beside the plan's. */
    extraModels?: string[];
    /** Only start the model server, so models are chosen against it. */
    serverOnly?: boolean;
}

/** `gaiaLemonade` sets up, and saves, GAIA's own Lemonade Server instead of the user's. */
export async function startSetup(skipChatModel: boolean, gaiaLemonade = false, options: SetupRunOptions = {}): Promise<SetupRunStatus> {
    return apiFetch('POST', '/setup/run', {
        skip_chat_model: skipChatModel,
        gaia_lemonade: gaiaLemonade,
        acknowledge_low_disk: !!options.acknowledgeLowDisk,
        keep_trying: !!options.keepTrying,
        extra_models: options.extraModels ?? [],
        server_only: !!options.serverOnly,
    });
}

/** Stop the run, or one model's download; finished and partial files are kept. */
export async function cancelSetup(modelId?: string): Promise<{ cancelled: boolean }> {
    return apiFetch('POST', '/setup/cancel', modelId ? { model_id: modelId } : undefined);
}

/** Skip one of setup's models, or take it back; returns the new plan. */
export async function skipSetupModel(modelId: string, skipped: boolean): Promise<SetupPlan> {
    return apiFetch('POST', '/setup/models/skip', { model_id: modelId, skipped });
}

/** Download one model now (retry, resume, or one skipped at setup); follow it with getSetupStatus. */
export async function downloadSetupModel(
    modelId: string,
    options: { acknowledgeLowDisk?: boolean; keepTrying?: boolean } = {},
): Promise<SetupRunStatus> {
    return apiFetch('POST', '/setup/models/download', {
        model_id: modelId,
        acknowledge_low_disk: !!options.acknowledgeLowDisk,
        keep_trying: !!options.keepTrying,
    });
}

/** Delete a downloaded model at the user's request; it joins the skipped models. */
export async function removeSetupModel(modelId: string): Promise<SetupPlan & { freed_gb: number }> {
    return apiFetch('POST', '/setup/models/remove', { model_id: modelId });
}

export async function getSetupStatus(): Promise<SetupRunStatus> {
    return apiFetch('GET', '/setup/status');
}

/** The Lemonade, chat model and download setup will use. Loads nothing. */
export async function getSetupPlan(): Promise<SetupPlan> {
    return apiFetch('GET', '/setup/plan');
}

/** Pick setup's chat model before it starts; returns the new plan. A 409 `no_fit` asks to confirm. */
export async function chooseSetupChatModel(modelId: string, acknowledgeNoFit = false): Promise<SetupPlan> {
    return apiFetch('POST', '/setup/chat-model', { model_id: modelId, acknowledge_no_fit: acknowledgeNoFit });
}

// -- GPU memory split (integrated GPUs) -------------------------------------------

/** The split now, what a bigger share unlocks, and how to change it; `settingGb` renders its steps. */
export async function getGpuMemory(settingGb?: number): Promise<GpuMemoryStatus> {
    return apiFetch('GET', settingGb != null ? `/gpu-memory?setting_gb=${settingGb}` : '/gpu-memory');
}

/** Set the GPU's share after admin permission; takes effect after a restart. */
export async function applyGpuMemory(settingGb: number): Promise<GpuMemoryStatus> {
    return apiFetch('POST', '/gpu-memory/apply', { setting_gb: settingGb, confirm: true });
}

/** Put back the setting GAIA replaced; takes effect after a restart. */
export async function revertGpuMemory(): Promise<GpuMemoryStatus> {
    return apiFetch('POST', '/gpu-memory/revert', { confirm: true });
}

/** Remember a value the user sets by hand, so it is checked after the restart. */
export async function recordGpuMemoryPending(settingGb: number): Promise<GpuMemoryStatus> {
    return apiFetch('POST', '/gpu-memory/pending', { setting_gb: settingGb });
}

/** Stop reporting the pending change ("Keep current"); changes nothing on the PC. */
export async function dismissGpuMemoryPending(): Promise<GpuMemoryStatus> {
    return apiFetch('DELETE', '/gpu-memory/pending');
}

/** A new chat's task cards and working folder. */
export async function getStartScreen(): Promise<StartScreen> {
    return apiFetch('GET', '/onboarding/start');
}
