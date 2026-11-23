// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { enMessages } from '../i18n/locales/en';
import { ref, type MessageKey, type Param } from '../i18n/registry';
import type { Translate } from '../i18n/useT';
import { placeInLine } from './chatAlerts';

let toolActions: Map<string, MessageKey> | null = null;

/** The tool label a backend narration opens with ("Writing file"), from its English. */
function toolAction(text: string): MessageKey | undefined {
    if (!toolActions) {
        toolActions = new Map();
        for (const [key, english] of Object.entries(enMessages)) {
            if (/^tool\..+\.active$/.test(key) && !english.includes('{') && !toolActions.has(english)) {
                toolActions.set(english, key as MessageKey);
            }
        }
    }
    return toolActions.get(text);
}

/** "Writing file: notes.txt" in each language; a narration GAIA has no label for stays as sent. */
function narrationParam(narration: string): Param {
    const whole = toolAction(narration);
    if (whole) return ref(whole);
    const m = /^(.+?): (.+)$/s.exec(narration);
    const key = m ? toolAction(m[1]) : undefined;
    return m && key ? ref('live.toolAction', { action: ref(key), detail: m[2] }) : narration;
}

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

const SKILL_RELOAD_REASONS = new Map<string, Parameters<Translate>[0]>([
    ['its files could not be found', 'live.skillReloadMissing'],
    ['the current model does not offer the tools it needs', 'live.skillReloadModel'],
    ['the current settings do not permit it to load', 'live.skillReloadPermission'],
    ['it failed to load with the current settings', 'live.skillReloadFailed'],
]);

/** Why a chat has no context measurement, in the chosen language; an unknown reason stays as sent. */
export function localizeContextReason(t: Translate, reason: string): string {
    return reason === "Not available yet: gaiacpp doesn't report what fills its context."
        ? t('ctx.nativeUnavailable')
        : reason;
}

/**
 * `label` in the chosen language when it is a status line GAIA knows; any other
 * line (a tool's progress, a retry notice) is shown as the backend wrote it.
 */
export function localizeLiveStatus(t: Translate, label: string): string {
    const key = EXACT[label];
    if (key) return t(key);
    const skill = /^Skill '([a-z0-9-]+)' was not restored after settings changed: ([^.]+)\. Reload it if needed\.$/.exec(label);
    if (skill) {
        const reason = SKILL_RELOAD_REASONS.get(skill[2]);
        if (reason) return t(reason, { skill: skill[1] });
    }
    let m = /^(Full access|Accept edits) is on, so GAIA didn't ask first\. (.+)$/s.exec(label);
    if (m) return t(m[1] === 'Full access' ? 'live.fullAccessAuto' : 'live.acceptEditsAuto', { action: narrationParam(m[2]) });
    m = /^Reasoning — ([\d,]+) words so far$/.exec(label);
    if (m) return t('live.reasoningWords', { n: m[1] });
    m = /^Connection lost\. Reconnecting \(attempt (\d+) of (\d+)\)…$/.exec(label);
    if (m) return t('live.reconnectingStream', { attempt: m[1], max: m[2] });
    // A model id has no spaces, so "Downloading the embedding model …" is not one of these.
    m = /^Loading (\S+) into memory$/.exec(label);
    if (m) return t('live.loadingModel', { model: m[1] });
    m = /^Reading page (\d+) of (\d+)…$/.exec(label);
    if (m) return t('live.readingPage', { page: m[1], total: m[2] });
    // `_queued_status_event` in _chat_helpers.py.
    m = /^Waiting for the GPU — (\d+)(?:st|nd|rd|th) in line$/.exec(label);
    if (m) return placeInLine(t, 'live.queued', Number(m[1]));
    m = /^Downloading the speech model, one time \(([\d.]+) GB\)$/.exec(label);
    if (m) return t('live.speechModelStart', { size: m[1] });
    m = /^Downloading the speech model, one time — (\d+)% of ([\d.]+) GB$/.exec(label);
    if (m) return t('live.speechModelPercent', { percent: m[1], size: m[2] });
    m = /^Downloading the speech model, one time — (\d+)% of ([\d.]+) GB, then ([\d.]+) GB for the NPU$/.exec(label);
    if (m) return t('live.speechModelPercentNpu', { percent: m[1], size: m[2], npu: m[3] });
    m = /^Downloading the speech model's NPU part \(([\d.]+) GB\), no progress shown$/.exec(label);
    if (m) return t('live.speechModelNpu', { size: m[1] });
    if (label === "Downloading the speech model's NPU part, no progress shown") return t('live.speechModelNpuNoSize');
    if (label === 'Loading the speech model') return t('live.speechModelLoading');
    m = /^Downloaded the speech model \(([\d.]+) GB\)$/.exec(label);
    if (m) return t('live.speechModelDone', { size: m[1] });
    // Lemonade's catalog doesn't always give a size.
    if (label === 'Downloading the speech model, one time') return t('live.speechModelStartNoSize');
    m = /^Downloading the speech model, one time — (\d+)%$/.exec(label);
    if (m) return t('live.speechModelPercentNoSize', { percent: m[1] });
    if (label === 'Downloaded the speech model') return t('live.speechModelDoneNoSize');
    m = /^Downloading (\S+)$/.exec(label);
    if (m) return t('live.downloadingModel', { model: m[1] });
    m = /^Undo may not work for this turn: (\S+) doesn't record file changes yet\.$/.exec(label);
    if (m) return t('turnChanges.nativeNoUndo', { name: m[1] });
    return label;
}
