// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Voice readiness, its speech models and its settings (`gaia/ui/routers/voice.py`). */

import { apiFetch } from './api';

export interface VoiceModel {
    id: string;
    purpose: 'transcription' | 'tts' | string;
    downloaded: boolean;
    size_gb: number | null;
}

export type TurnTaking = 'hands_free' | 'push_to_talk';

export interface VoiceStatus {
    ready: boolean;
    error: string | null;
    error_code: string | null;
    language: string;
    asr_model: string;
    tts_model: string;
    /** False for any language but English: replies are shown, not spoken. */
    speaks: boolean;
    models: VoiceModel[];
    turn_taking: TurnTaking;
    silence_ms: number;
    status_updates: boolean;
    captions: boolean;
    /** Hands-free only: answer when addressed by name, or just after GAIA spoke. */
    wake_word?: boolean;
    /** How loud GAIA speaks, 0-100. */
    volume?: number;
}

export interface VoiceSettings {
    /** null follows the app's language. */
    language: string | null;
    asr_model: string;
    turn_taking: TurnTaking;
    silence_ms: number;
    status_updates: boolean;
    captions: boolean;
    wake_word: boolean;
    volume: number;
}

/** `language: ''` goes back to following the app's language. */
export type VoiceSettingsUpdate = Partial<Omit<VoiceSettings, 'language'>> & { language?: string };

export const SILENCE_MS_MIN = 300;
export const SILENCE_MS_MAX = 1200;

export function getVoiceStatus(appLanguage: string): Promise<VoiceStatus> {
    return apiFetch<VoiceStatus>('GET', `/voice/status?lang=${encodeURIComponent(appLanguage)}`);
}

export function downloadVoiceModel(model: string): Promise<{ model: string; downloaded: boolean }> {
    return apiFetch('POST', '/voice/models/download', { model });
}

export function getVoiceSettings(): Promise<VoiceSettings> {
    return apiFetch<VoiceSettings>('GET', '/voice/settings');
}

export function updateVoiceSettings(change: VoiceSettingsUpdate): Promise<VoiceSettings> {
    return apiFetch<VoiceSettings>('PUT', '/voice/settings', change);
}
