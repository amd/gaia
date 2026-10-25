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
    'Using deepseek-v4p1-flash on Fireworks AI (cloud). Your messages, and anything read from '
    + 'files or memory, are sent there.';

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

    it('a cloud pick shows as cloud once the chat names it', () => {
        // This window's own pick refetches the chat list with it, so the chat names the pick.
        const place = inferencePlace(CLOUD_ACTIVE, {
            inference_remote: true,
            inference_provider_name: 'Fireworks AI',
            effective_model: 'fireworks.deepseek-v4p1-flash',
        });
        expect(place?.remote).toBe(true);
        expect(place?.label).toBe('Fireworks AI');
    });

    it('a backend that does not name the model still counts a cloud pick at once', () => {
        // Older backends send no effective_model, so the pick can't be stale and wins.
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

    it("follows the chat's own location when another window switched models (#5743)", () => {
        // This window still caches the old cloud pick; the chat already resolves to local Qwen.
        const place = inferencePlace(CLOUD_ACTIVE, {
            inference_remote: false,
            inference_provider_name: 'Lemonade on this machine',
            effective_model: 'Qwen3.6-35B-A3B-GGUF',
        });
        expect(place?.remote).toBe(false);
        expect(place?.label).toBe('Local');
    });

    it('claims nothing when the pick is stale and the chat cannot say where it runs', () => {
        const place = inferencePlace(CLOUD_ACTIVE, {
            inference_remote: null,
            effective_model: 'Qwen3.6-35B-A3B-GGUF',
        });
        expect(place).toBeNull();
    });

    it('before the first chat, uses the backend answer for a new chat (#5253)', () => {
        const place = inferencePlace({ ...LOCAL_ACTIVE, inference_remote: false }, undefined);
        expect(place).toEqual({ remote: false, label: 'Local', description: null });
        // A session's own answer still wins, and an unknown one still claims nothing.
        expect(inferencePlace({ ...LOCAL_ACTIVE, inference_remote: false }, { inference_remote: null })).toBeNull();
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
