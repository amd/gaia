// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Shared wording for a local model's state, device, size and speed. */

import type { LocalModelState, ModelPlacement, ProviderModel } from '../types';

export const STATE_LABELS: Record<LocalModelState, string> = {
    loaded: 'Loaded',
    downloaded: 'Downloaded, ready to load',
    not_downloaded: 'Not downloaded',
};

/** "GPU · Vulkan" for a loaded model, "GPU · Vulkan (expected)" before it loads. */
export function placementText(p: ModelPlacement | null | undefined): string | null {
    if (!p?.label) return null;
    return p.source === 'expected' ? `${p.label} (expected)` : p.label;
}

export function ctxLabel(tokens: number | null | undefined): string {
    return tokens ? `${Math.round(tokens / 1024)}K` : 'unknown';
}

/** "GPU · Vulkan · 5.6 GB · 38 tok/s on this PC". */
export function modelMetaLine(m: ProviderModel): string {
    const parts = [placementText(m.placement)];
    if (m.size_gb) parts.push(`${m.size_gb} GB`);
    if (m.tokens_per_second) parts.push(`${Math.round(m.tokens_per_second)} tok/s on this PC`);
    return parts.filter(Boolean).join(' · ');
}

/** The model's state, derived from `downloaded` when the backend sent none. */
export function modelState(m: ProviderModel): LocalModelState {
    return m.state ?? (m.downloaded ? 'downloaded' : 'not_downloaded');
}

/** True while a download of *m* is running. */
export function isDownloading(m: ProviderModel): boolean {
    const s = m.download_progress?.state;
    return s === 'starting' || s === 'downloading';
}
