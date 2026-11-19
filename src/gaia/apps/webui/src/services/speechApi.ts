// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Read aloud: speech settings, the voice model, and text-to-speech (`/api/speech*`). */

import { ApiError, UI_HEADER, apiFetch, getFriendlyError, parseErrorDetail, parseErrorStatus } from './api';
import { getApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';

export interface Voice {
    id: string;
    name: string;
    accent: 'american' | 'british';
    gender: 'female' | 'male';
    quality: string;
}

export interface SpeechSettings {
    voice: string;
    /** Percent of normal speed. */
    speed: number;
    /** Read every finished reply aloud. */
    auto: boolean;
    min_speed: number;
    max_speed: number;
    voices: Voice[];
}

export async function getSpeechSettings(): Promise<SpeechSettings> {
    return apiFetch<SpeechSettings>('GET', '/speech/settings');
}

export async function updateSpeechSettings(
    data: Partial<Pick<SpeechSettings, 'voice' | 'speed' | 'auto'>>,
): Promise<SpeechSettings> {
    return apiFetch<SpeechSettings>('PUT', '/speech/settings', data);
}

/** Whether Lemonade has the voice model on disk; the first read downloads it if not. */
export interface SpeechModel {
    downloaded: boolean;
    size_gb: number | null;
}

export async function getSpeechModel(): Promise<SpeechModel> {
    return apiFetch<SpeechModel>('GET', '/speech/model');
}

/** Longest text one `/speech` request takes; mirrors MAX_SPEECH_CHARS. */
export const MAX_SPEECH_CHARS = 1000;

/** Speak *text* as WAV audio, in the saved voice unless one is given. */
export async function speak(
    text: string,
    opts: { voice?: string; speed?: number; signal?: AbortSignal } = {},
): Promise<Blob> {
    const url = `${getApiBase()}/speech`;
    const t = log.api.time();
    log.api.info(`POST ${url}`, { chars: text.length, voice: opts.voice });
    const res = await fetch(url, {
        method: 'POST',
        headers: { ...UI_HEADER, 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: opts.voice, speed: opts.speed }),
        signal: opts.signal,
    });
    if (!res.ok) {
        const errorText = await res.text().catch(() => '');
        log.api.error(`POST ${url} - HTTP ${res.status}`, { errorText });
        const detail = parseErrorDetail(errorText);
        throw new ApiError(getFriendlyError(res.status, detail), res.status, detail, parseErrorStatus(errorText));
    }
    const audio = await res.blob();
    log.api.timed(`POST ${url} -> ${res.status} (${audio.size} bytes)`, t);
    return audio;
}

