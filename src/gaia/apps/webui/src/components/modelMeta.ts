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

/** A context window: 65536 is "64K", as Settings lists it; any other size in thousands ("200K", "1M"). */
export function formatWindow(tokens: number): string {
    if (tokens >= 1024 && tokens % 1024 === 0 && tokens < 1024 * 1024) return ctxLabel(tokens);
    if (tokens < 1000) return String(tokens);
    if (tokens < 1_000_000) return `${(tokens / 1000).toFixed(tokens < 10_000 ? 1 : 0)}K`;
    return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
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
