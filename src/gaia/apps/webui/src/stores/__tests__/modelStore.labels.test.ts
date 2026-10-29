// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Two picker rows must never read the same, or a person can pick the wrong model. */

import { describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import { distinctModelLabels } from '../modelStore';

describe('distinctModelLabels', () => {
    it('shows enough of the id to tell apart models that share a short name', () => {
        expect(distinctModelLabels([{ id: 'fireworks.auto' }, { id: 'fireworks.firerouter/auto' }])).toEqual({
            'fireworks.auto': 'auto',
            'fireworks.firerouter/auto': 'firerouter/auto',
        });
    });

    it('goes deeper only as far as needed', () => {
        expect(distinctModelLabels([
            { id: 'fireworks.accounts/a/models/x' },
            { id: 'fireworks.accounts/b/models/x' },
        ])).toEqual({
            'fireworks.accounts/a/models/x': 'a/models/x',
            'fireworks.accounts/b/models/x': 'b/models/x',
        });
    });

    it('leaves unique names and friendly display names alone', () => {
        expect(distinctModelLabels([
            { id: 'fireworks.accounts/fireworks/models/glm-5p3-flash' },
            { id: 'fireworks.deepseek-v4p1-flash' },
            { id: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B' },
        ])).toEqual({
            'fireworks.accounts/fireworks/models/glm-5p3-flash': 'glm-5p3-flash',
            'fireworks.deepseek-v4p1-flash': 'deepseek-v4p1-flash',
            'Gemma-4-E4B-it-GGUF': 'Gemma 4 E4B',
        });
    });

    it('falls back to the ids when display names collide', () => {
        expect(distinctModelLabels([
            { id: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B' },
            { id: 'user.Gemma-4-E4B-it-GGUF-Q8', display_name: 'Gemma 4 E4B' },
        ])).toEqual({
            'Gemma-4-E4B-it-GGUF': 'Gemma-4-E4B-it-GGUF',
            'user.Gemma-4-E4B-it-GGUF-Q8': 'user.Gemma-4-E4B-it-GGUF-Q8',
        });
    });
});
