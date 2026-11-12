// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ref, type MessageKey } from '../i18n/registry';
import type { Translate } from '../i18n/useT';

/** Live status lines the backend and ChatView write in English, matched whole. */
const EXACT: Record<string, Parameters<Translate>[0]> = {
    'Thinking...': 'msg.thinking',
    'Reading your request': 'live.readingRequest',
    'Reading the tool results': 'live.readingResults',
    'Reasoning': 'live.reasoning',
    'Reconnecting to the model server…': 'live.reconnectingServer',
    'Waiting for GAIA to start this answer…': 'live.waitingStart',
    'The model server stopped. GAIA is restarting it…': 'live.serverRestarting',
    'Starting the model server…': 'live.serverStarting',
};

/** Why a turn waits for the model, as the backend's `describe_wait` words it. */
const QUEUE_REASONS: Record<string, MessageKey> = {
    'another chat or task is using it': 'live.queueReason.other',
    'another request from this app is using it': 'live.queueReason.sameApp',
    'a higher-priority request goes first': 'live.queueReason.priority',
};

/**
 * `label` in the chosen language when it is a status line GAIA knows; any other
 * line (a tool's progress, a retry notice) is shown as the backend wrote it.
 */
export function localizeLiveStatus(t: Translate, label: string): string {
    const key = EXACT[label];
    if (key) return t(key);
    let m = /^Reasoning — ([\d,]+) words so far$/.exec(label);
    if (m) return t('live.reasoningWords', { n: m[1] });
    m = /^Connection lost\. Reconnecting \(attempt (\d+) of (\d+)\)…$/.exec(label);
    if (m) return t('live.reconnectingStream', { attempt: m[1], max: m[2] });
    // A model id has no spaces, so "Downloading the embedding model …" is not one of these.
    m = /^Loading (\S+) into memory$/.exec(label);
    if (m) return t('live.loadingModel', { model: m[1] });
    m = /^Reading page (\d+) of (\d+)…$/.exec(label);
    if (m) return t('live.readingPage', { page: m[1], total: m[2] });
    m = /^Downloading (\S+)$/.exec(label);
    if (m) return t('live.downloadingModel', { model: m[1] });
    m = /^Waiting for the model — (.+?)(?:, #(\d+) of (\d+) in line)? \((\d+)s\)$/.exec(label);
    const reason = m && QUEUE_REASONS[m[1]];
    if (m && reason) {
        return m[2]
            ? t('live.modelQueueInLine', { reason: ref(reason), position: m[2], total: m[3], s: m[4] })
            : t('live.modelQueue', { reason: ref(reason), s: m[4] });
    }
    m = /^Got the model after waiting (\d+)s$/.exec(label);
    if (m) return t('live.modelGranted', { s: m[1] });
    return label;
}
