// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Generated music / sound effects: validation and display helpers for the audio card. */

import type { AgentStep, GeneratedAudio, StreamEvent } from '../types';

const MODEL_LABELS: Record<string, string> = {
    'ACE-Step-Music': 'ACE-Step music',
    'ThinkSound-SFX': 'ThinkSound SFX',
};

/** `C:\...`, `C:/...`, `\\server\share\...` or `/...`. */
const ABSOLUTE_RE = /^(?:[A-Za-z]:[\\/]|\\\\|\/)/;

/**
 * Whether `path` may be handed to the audio route: an absolute `.wav` path
 * with no NUL, no control characters and no `..` segment. The server checks
 * scope again; this keeps a bad payload from ever becoming a request.
 */
export function isPlayableWavPath(path: unknown): path is string {
    if (typeof path !== 'string' || path.length === 0 || path.length > 4096) return false;
    // eslint-disable-next-line no-control-regex
    if (/[\u0000-\u001f]/.test(path)) return false;
    if (!ABSOLUTE_RE.test(path)) return false;
    if (path.split(/[\\/]/).includes('..')) return false;
    return path.toLowerCase().endsWith('.wav');
}

function finiteSeconds(value: unknown): number | null {
    return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

/** `5` -> "0:05", `754` -> "12:34", `3725` -> "1:02:05"; '' when not a real duration. */
export function formatAudioDuration(seconds: unknown): string {
    const value = finiteSeconds(seconds);
    if (value === null) return '';
    const total = Math.round(value);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = String(total % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

export function audioModelLabel(model?: string): string {
    if (!model) return '';
    return MODEL_LABELS[model] ?? model;
}

export function audioKindLabel(kind?: string): string {
    return kind === 'sfx' ? 'Sound effect' : 'Music';
}

export function fileNameOf(path: string): string {
    return path.split(/[\\/]/).pop() || path;
}

/** The best known length: what the player measured, else the file header, else the request. */
export function bestDuration(audio: GeneratedAudio, measured?: number | null): number | null {
    return finiteSeconds(measured) ?? finiteSeconds(audio.durationSeconds) ?? finiteSeconds(audio.durationRequested);
}

/** The live `tool_result.generated_audio` payload, in the step's shape. */
export function toGeneratedAudio(raw: NonNullable<StreamEvent['generated_audio']>): GeneratedAudio {
    return {
        path: raw.path,
        model: raw.model,
        kind: raw.kind,
        format: raw.format,
        durationSeconds: raw.duration_seconds ?? null,
        durationRequested: raw.duration_requested ?? null,
        prompt: raw.prompt ?? null,
    };
}

/** One entry per generated file, in turn order, from successful steps only. */
export function generatedAudioOf(steps: AgentStep[] | undefined): GeneratedAudio[] {
    const seen = new Set<string>();
    const out: GeneratedAudio[] = [];
    for (const step of steps ?? []) {
        const audio = step.generatedAudio;
        if (step.type !== 'tool' || step.success === false || !audio || typeof audio.path !== 'string') continue;
        if (seen.has(audio.path)) continue;
        seen.add(audio.path);
        out.push(audio);
    }
    return out;
}
