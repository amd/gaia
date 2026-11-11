// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The voice socket's messages; mirrors `gaia.voice.protocol`. */

/** Stable codes from `gaia.voice.errors.ERROR_CODES`; each has a translated message. */
export const VOICE_ERROR_CODES = [
    'lemonade_unreachable',
    'model_missing',
    'mic_denied',
    'mic_missing',
    'mic_busy',
    'remote_lemonade',
    'cloud_model',
    'tts_failed',
    'asr_failed',
    'session_lost',
    'bad_request',
] as const;

export type VoiceErrorCode = (typeof VOICE_ERROR_CODES)[number];

export function isVoiceErrorCode(code: unknown): code is VoiceErrorCode {
    return typeof code === 'string' && (VOICE_ERROR_CODES as readonly string[]).includes(code);
}

export type VoicePurpose = 'dictation' | 'voice';
export type SpeakKind = 'spoken' | 'status';

export type ServerEvent =
    | { type: 'ready'; purpose?: VoicePurpose; asr_model?: string; speech: boolean }
    | { type: 'speech_started' }
    | { type: 'speech_stopped' }
    | { type: 'partial'; text: string }
    | { type: 'final'; text: string }
    | { type: 'level'; source: 'mic' | 'speech'; value: number }
    | { type: 'speaking'; kind: SpeakKind }
    | { type: 'spoken_piece'; kind: SpeakKind; text: string }
    | { type: 'speaking_done' }
    | { type: 'speech_stopped_by_user' }
    /** A sound came in over GAIA's voice: lower it (on) or bring it back. */
    | { type: 'duck'; on: boolean }
    /** The user took the floor: GAIA's speech has been dropped. */
    | { type: 'barge_in' }
    /** What was said so far sounds unfinished; more is being waited for. */
    | { type: 'holding'; text: string }
    /** A sound that was not speech (typing, a cough); nothing was sent. */
    | { type: 'ignored'; reason: string }
    | { type: 'timing'; heard_ms: number; think_ms: number; voice_ms: number; total_ms: number }
    | { type: 'flush' }
    | { type: 'limit'; seconds?: number }
    | { type: 'error'; code: VoiceErrorCode; message: string; detail?: string }
    | { type: 'closed' };

export type ClientMessage =
    | { type: 'start'; purpose: VoicePurpose; language: string }
    | { type: 'speak'; text: string; kind: SpeakKind }
    | { type: 'speak_end' }
    | { type: 'interrupt' }
    | { type: 'commit' }
    | { type: 'expect'; on: boolean }
    | { type: 'stop' };

/** A voice failure the screen shows: a stable code, plus what the server or browser said. */
export interface VoiceFailure {
    code: VoiceErrorCode;
    /** The model id for `model_missing`, or the raw cause, kept for the log and the details line. */
    detail?: string;
}
