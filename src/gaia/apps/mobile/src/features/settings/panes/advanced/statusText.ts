// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate, type MessageKey } from '@webui/i18n/registry';
import type { Translate } from '@webui/i18n/translator';
import type { SystemStatus } from '@webui/types';
import { formatGb } from '@webui/utils/modelLabels';

/** The web's Advanced pane reads these from its error notice, which can't load on a phone. */
const namesSetupCommand = (text: string | null | undefined) => /gaia init/i.test(text ?? '');

export function needsLocalSetup(status: SystemStatus): boolean {
    return !status.lemonade_running && (namesSetupCommand(status.start_command) || namesSetupCommand(status.start_instruction));
}

const CAPABILITIES: Record<string, MessageKey> = {
    cloud: 'settings.advanced.capability.cloud',
    chat: 'settings.advanced.capability.chat',
    vision: 'settings.advanced.capability.vision',
    'tool-calling': 'settings.advanced.capability.toolCalling',
    reasoning: 'settings.advanced.capability.reasoning',
    embeddings: 'settings.advanced.capability.embeddings',
    reranking: 'settings.advanced.capability.reranking',
    coding: 'settings.advanced.capability.coding',
    audio: 'settings.advanced.capability.audio',
    image: 'settings.advanced.capability.image',
    tts: 'settings.advanced.capability.tts',
    stt: 'settings.advanced.capability.stt',
    transcription: 'settings.advanced.capability.transcription',
};

/** Lemonade's labels in words; one it adds later shows as sent. */
export function capabilitiesText(t: Translate, labels: string[]): string {
    return t.compose((say) => labels.map((l) => (CAPABILITIES[l] ? say(CAPABILITIES[l]) : l)).join(say('mem.listSeparator')));
}

export interface StatusFlags {
    cloud: boolean;
    wrongModel: boolean;
    smallContext: boolean;
    notDownloaded: boolean;
}

/** Which model row state the PC is in; a cloud model has nothing to download or load. */
export function statusFlags(status: SystemStatus): StatusFlags {
    const cloud = !!status.default_model_is_cloud;
    const running = !cloud && status.lemonade_running;
    return {
        cloud,
        wrongModel: running && !!status.model_loaded && status.expected_model_loaded === false,
        smallContext: running && !!status.model_loaded && status.context_size_sufficient === false,
        notDownloaded: running && !status.model_loaded && status.model_downloaded === false,
    };
}

/** What a model download needs, for the hint and the low-disk warning. */
export function needText(status: SystemStatus): string {
    return isolate(status.default_model_size_gb ? formatGb(status.default_model_size_gb) : '6 GB', 'ltr');
}
