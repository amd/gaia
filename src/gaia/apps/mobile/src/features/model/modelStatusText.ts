// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import type { Translate } from '@webui/i18n/translator';

import type { LocalModelStatus } from '@/api/client.types';

export type ModelDot = LocalModelStatus['state'];

export const STATE_TEXT: Record<ModelDot, MessageKey> = {
    loaded: 'modelStatus.state.loaded',
    warming: 'modelStatus.state.warming',
    idle: 'modelStatus.state.idle',
    stopped: 'modelStatus.state.stopped',
};

const PILL_ARIA: Record<ModelDot, MessageKey> = {
    loaded: 'modelStatus.pillAria.loaded',
    warming: 'modelStatus.pillAria.warming',
    idle: 'modelStatus.pillAria.idle',
    stopped: 'modelStatus.pillAria.stopped',
};

export interface PillText {
    /** The place and model, "Local · Gemma 4 E4B"; "Model" until the PC has answered. */
    text: string;
    /** The model alone, or null. */
    name: string | null;
    remote: boolean;
    /** What a screen reader says: the text and, for a local model, its state. */
    aria: string;
}

export function pillText(status: LocalModelStatus | undefined, t: Translate): PillText {
    const remote = !!status?.active_remote;
    const name = (remote ? status?.active_display_name : (status?.display_name ?? status?.active_display_name)) ?? null;
    const place = !remote && name ? t('mobileApp.model.onYourPc') : null;
    const text = [place, name].filter(Boolean).join(' · ') || t('modelStatus.fallbackName');
    return {
        text,
        name,
        remote,
        aria: remote || !status ? text : t(PILL_ARIA[status.state], { text }),
    };
}

/** Sizes go in as string params, so an rtl line keeps each one whole. */
export function memoryText(s: LocalModelStatus, t: Translate): string | null {
    const m = s.memory;
    if (!m) return s.memory_error;
    if (!m.model_gb && m.free_gb == null)
        return t('modelStatus.memoryTotal', {
            size: `${m.total_gb} GB`,
            source: m.source,
        });
    return t.compose((say) =>
        [
            m.model_gb ? say('modelStatus.memoryUsed', { size: `~${m.model_gb} GB` }) : null,
            m.free_gb != null ? say('modelStatus.memoryFree', { size: `${m.free_gb} GB` }) : null,
        ]
            .filter(Boolean)
            .join(' · '),
    );
}

export interface RestartControl {
    /** The button is shown at all: GAIA never starts a server it doesn't run. */
    shown: boolean;
    disabled: boolean;
    /** Press asks first (a Lemonade GAIA doesn't own). */
    confirms: boolean;
    hint: MessageKey;
}

export function restartControl(s: LocalModelStatus, restarting: boolean): RestartControl {
    const notOurs = s.owner !== 'gaia';
    const hint: MessageKey = s.busy
        ? s.busy_reason === 'warm_up'
            ? 'modelStatus.busyWarmUp'
            : 'modelStatus.busyRequest'
        : s.state === 'stopped'
          ? 'modelStatus.startServer'
          : 'modelStatus.reload';
    return {
        shown: !(notOurs && s.state === 'stopped'),
        disabled: restarting || s.restarting || s.busy || (s.active_remote && s.state !== 'loaded'),
        confirms: notOurs,
        hint,
    };
}
