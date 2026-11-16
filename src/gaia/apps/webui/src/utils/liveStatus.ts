// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Translate } from '../i18n/translator';

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
    return label;
}
