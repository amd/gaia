// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What the voice screen shows. The controller in `voice/controller.ts` writes it. */

import { create } from 'zustand';
import type { VoiceStatus } from '../services/voiceApi';
import type { SpeakKind, VoiceFailure } from '../voice/protocol';

/** `ptt_idle`: push-to-talk, waiting for the button or Space. */
export type VoicePhase = 'warming' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'ptt_idle';

export type VoiceLogKind = 'user' | 'status' | 'tool' | 'note' | 'answer';

export interface VoiceLogRow {
    id: number;
    kind: VoiceLogKind;
    /** What you said, the status line, the note, or the answer's markdown. */
    text: string;
    /** For a tool row: the tool's id, and the plain line for it. */
    tool?: string;
    label?: string;
    result?: string;
    /** A picture the tool made, shown under its row. */
    image?: string;
    done?: boolean;
    ok?: boolean;
}

export interface VoiceCaption {
    kind: SpeakKind;
    text: string;
    /** Words already heard; Infinity when the text is shown, not spoken. */
    lit: number;
}

export interface VoiceState {
    open: boolean;
    sessionId: string | null;
    phase: VoicePhase;
    error: VoiceFailure | null;
    status: VoiceStatus | null;
    /** The speech model being downloaded, and why the last download failed. */
    downloading: string | null;
    downloadError: string | null;
    captions: boolean;
    /** Asked to stay quiet: nothing is sent until someone says "Hey GAIA". */
    standby: boolean;
    /** Your words, live while `heardPartial`. */
    heard: string;
    heardPartial: boolean;
    caption: VoiceCaption | null;
    log: VoiceLogRow[];
    /** Tool calls this voice session ran. */
    tools: number;
    pushToTalk: boolean;
    holding: boolean;
    turnRunning: boolean;
    /** The phone-only privacy notice, until dismissed once. */
    phoneNotice: boolean;
}

export const VOICE_IDLE: VoiceState = {
    open: false,
    sessionId: null,
    phase: 'warming',
    error: null,
    status: null,
    downloading: null,
    downloadError: null,
    captions: true,
    heard: '',
    standby: false,
    heardPartial: false,
    caption: null,
    log: [],
    tools: 0,
    pushToTalk: false,
    holding: false,
    turnRunning: false,
    phoneNotice: false,
};

export const useVoiceStore = create<VoiceState>(() => ({ ...VOICE_IDLE }));
