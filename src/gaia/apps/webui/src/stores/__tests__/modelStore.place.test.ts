// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The UI said "100% local & private" while turns were sent to Fireworks. It may
 * claim local only when the backend says this chat stays on this PC.
 */

import { describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import { inferencePlace, selectInferenceLocation } from '../modelStore';
import type { ActiveModel, Session } from '../../types';

const LOCAL_ACTIVE: ActiveModel = {
    provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
};
const CLOUD_ACTIVE: ActiveModel = {
    provider: 'fireworks', model: 'fireworks.deepseek-v4p1-flash', label: 'DeepSeek', remote: true, is_default: false,
};
const FIREWORKS_SENTENCE =
    'Chat inference runs on Fireworks AI via Lemonade — a cloud provider — using model '
    + '`fireworks.deepseek-v4p1-flash`. Everything in this conversation is sent there to be answered.';

describe('inferencePlace', () => {
    it('claims local only when the backend says the chat stays here', () => {
        const place = inferencePlace(LOCAL_ACTIVE, {
            inference_remote: false, inference_provider_name: 'Lemonade on this machine',
        });
        expect(place).toEqual({ remote: false, label: 'Local', description: null });
    });

    it('names the cloud provider and carries the backend sentence', () => {
        const place = inferencePlace(LOCAL_ACTIVE, {
            inference_remote: true,
            inference_provider_name: 'Fireworks AI',
            inference_description: FIREWORKS_SENTENCE,
        });
        expect(place).toEqual({ remote: true, label: 'Fireworks AI', description: FIREWORKS_SENTENCE });
    });

    it('treats a cloud pick as cloud before the session list catches up', () => {
        const place = inferencePlace(CLOUD_ACTIVE, { inference_remote: false });
        expect(place?.remote).toBe(true);
        expect(place?.label).toBe('Fireworks AI');
    });

    it.each([
        ['an unresolvable location', { inference_remote: null }],
        ['a backend that predates the field', {}],
        ['no session loaded yet', undefined],
    ])('makes no claim for %s', (_label, location) => {
        expect(inferencePlace(LOCAL_ACTIVE, location)).toBeNull();
        expect(inferencePlace(null, location)).toBeNull();
    });
});

describe('selectInferenceLocation', () => {
    const s = (id: string, remote: boolean) =>
        ({ id, inference_remote: remote } as unknown as Session);

    it('prefers the open session, then the most recent one', () => {
        const sessions = [s('a', true), s('b', false)];
        expect(selectInferenceLocation(sessions, 'b')?.inference_remote).toBe(false);
        expect(selectInferenceLocation(sessions, null)?.inference_remote).toBe(true);
        expect(selectInferenceLocation([], null)).toBeUndefined();
    });
});
