// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What the screen says for each voice error code, and the one action that fixes it. */

import type { MessageKey } from '../i18n/registry';
import type { VoiceErrorCode } from './protocol';

/** `download`: fetch the missing speech model. `retry`: start the session again. `settings`: open the OS or site mic settings. */
export type VoiceRecovery = 'download' | 'retry' | 'settings' | 'none';

export const VOICE_ERROR_TEXT: Record<VoiceErrorCode, { title: MessageKey; body: MessageKey; action: VoiceRecovery }> = {
    lemonade_unreachable: { title: 'voice.error.lemonade_unreachable', body: 'voice.error.lemonade_unreachable.body', action: 'retry' },
    model_missing: { title: 'voice.error.model_missing', body: 'voice.error.model_missing.body', action: 'download' },
    mic_denied: { title: 'voice.error.mic_denied', body: 'voice.error.mic_denied.body', action: 'retry' },
    mic_missing: { title: 'voice.error.mic_missing', body: 'voice.error.mic_missing.body', action: 'retry' },
    mic_busy: { title: 'voice.error.mic_busy', body: 'voice.error.mic_busy.body', action: 'retry' },
    remote_lemonade: { title: 'voice.error.remote_lemonade', body: 'voice.error.remote_lemonade.body', action: 'none' },
    cloud_model: { title: 'voice.error.cloud_model', body: 'voice.error.cloud_model.body', action: 'none' },
    tts_failed: { title: 'voice.error.tts_failed', body: 'voice.error.tts_failed.body', action: 'retry' },
    asr_failed: { title: 'voice.error.asr_failed', body: 'voice.error.asr_failed.body', action: 'retry' },
    session_lost: { title: 'voice.error.session_lost', body: 'voice.error.session_lost.body', action: 'retry' },
    bad_request: { title: 'voice.error.bad_request', body: 'voice.error.bad_request.body', action: 'retry' },
};

