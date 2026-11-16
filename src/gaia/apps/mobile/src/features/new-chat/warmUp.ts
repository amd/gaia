// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import { ref } from '@webui/i18n/registry';
import type { WarmUpStatus } from '@webui/types';

import type { Translate } from '@/i18n/useT';

export interface WarmUpProgress {
    seconds: number;
    model: string | null;
    device: WarmUpStatus['device'];
}

const DEVICE_KEYS: Record<string, MessageKey> = { gpu: 'device.gpu', npu: 'device.npu', cpu: 'device.cpu' };

// Null when nothing is warming up, so the line is hidden.
export function warmUpProgress(status: WarmUpStatus | null | undefined): WarmUpProgress | null {
    if (status?.state !== 'warming') return null;
    return {
        seconds: Math.floor(status.elapsed_seconds ?? 0),
        model: status.model ?? null,
        device: status.device ?? null,
    };
}

// Mirrors webui's shortModelName, whose module pulls in the chat store.
export function shortModelName(model: string): string {
    const bare = /^(fireworks|amd)\./.test(model) ? model.slice(model.indexOf('.') + 1) : model;
    return bare.slice(bare.lastIndexOf('/') + 1);
}

export function warmUpLine(
    { seconds, model, device }: WarmUpProgress,
    t: Translate,
    modelName: (model: string) => string,
): string {
    if (!model) return t('newChat.warmup.ready', { s: seconds });
    const name = modelName(model);
    const where = device ? DEVICE_KEYS[device] : undefined;
    return where
        ? t('newChat.warmup.loadingOn', { model: name, where: ref(where), s: seconds })
        : t('newChat.warmup.loading', { model: name, s: seconds });
}
