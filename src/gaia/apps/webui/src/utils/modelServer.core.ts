// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Model server wording shared by the web UI and the phone app; no DOM, no stores. */

import type { MessageKey } from '../i18n/registry';
import type { Translate } from '../i18n/translator';
import type { LoadedModelInfo, ModelStatus } from '../types/api.gen';

const BACKEND_LABELS: Record<string, string> = {
    vulkan: 'Vulkan',
    rocm: 'ROCm',
    metal: 'Metal',
    cpu: 'CPU',
    npu: 'NPU',
    remote: 'Remote (cloud)',
};

export const OWNER_LABELS: Record<string, MessageKey> = {
    gaia: 'settings.server.ownerGaia',
    user: 'settings.server.ownerUser',
    remote: 'settings.server.ownerRemote',
};

export function backendLabel(backend: string | null | undefined): string {
    if (!backend) return 'unknown';
    return BACKEND_LABELS[backend] ?? backend;
}

/** "Vulkan", "Vulkan (Lemonade's default)", "Remote (cloud)", or "unknown". */
export function runsOn(model: LoadedModelInfo | null | undefined): string {
    if (!model) return 'unknown';
    const label = backendLabel(model.backend);
    return model.backend_source === 'lemonade_default' ? `${label} (Lemonade's default)` : label;
}

export function ctxLabel(ctx: number | null | undefined): string {
    return ctx ? `${Math.round(ctx / 1024)}K tokens` : 'unknown';
}

const MODEL_KIND_LABELS: Record<string, MessageKey> = {
    asr: 'settings.server.kind.speechRecognition',
    embedding: 'settings.server.kind.embedding',
    image: 'settings.server.kind.image',
    llm: 'settings.server.kind.language',
    rerank: 'settings.server.kind.reranker',
    reranking: 'settings.server.kind.reranker',
    stt: 'settings.server.kind.speechRecognition',
    transcription: 'settings.server.kind.speechRecognition',
    tts: 'settings.server.kind.speech',
    vision: 'settings.server.kind.vision',
    '3d': 'settings.server.kind.threeD',
};

export function modelKindLabel(model: LoadedModelInfo, t: Translate): string {
    if (!model.model_type) return t('settings.server.loadedModel');
    const type = model.model_type.toLowerCase();
    const key = MODEL_KIND_LABELS[type];
    return key ? t(key) : t('settings.server.kindOther', { kind: `${type.charAt(0).toUpperCase()}${type.slice(1)}` });
}

/** "Name · Vulkan · 64K tokens"; `displayName` turns a model id into what the person sees. */
export function loadedModelDetail(model: LoadedModelInfo, t: Translate, displayName: (id: string) => string): string {
    const name = model.name ? displayName(model.name) : t.bare('settings.server.unknownModel');
    const detail = [name, runsOn(model)];
    if (model.backend !== 'remote' && model.ctx_size) detail.push(ctxLabel(model.ctx_size));
    return detail.join(' · ');
}

export type CustomModelNote = { kind: 'unlisted' | 'unchecked'; id: string };

/** The warning a saved custom ID carries: the model server doesn't list it, or couldn't be asked. */
export function customModelNote(id: string, status: ModelStatus | null | undefined): CustomModelNote | null {
    if (!id || !status || status.found) return null;
    return { kind: status.checked ? 'unlisted' : 'unchecked', id };
}
