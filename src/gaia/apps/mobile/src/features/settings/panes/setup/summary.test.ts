// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ActiveModel, SetupPlan } from '@/api/client.types';

import { chatModelName } from './summary';

const plan = {
    chat_model: { id: 'gemma' },
    catalog: [{ id: 'gemma', name: 'Gemma 4 E4B' }],
} as unknown as SetupPlan;

describe('chatModelName', () => {
    it('names the active model, else the plan\'s chat model', () => {
        const cloud = { model: 'kimi-k2', label: null, display_name: 'Kimi K2', remote: true } as unknown as ActiveModel;
        expect(chatModelName(cloud, plan)).toBe('Kimi K2');
        expect(chatModelName({ ...cloud, label: 'My pick' }, plan)).toBe('My pick');
        expect(chatModelName(undefined, plan)).toBe('Gemma 4 E4B');
        expect(chatModelName({ ...cloud, model: null }, plan)).toBe('Gemma 4 E4B');
        expect(chatModelName(undefined, undefined)).toBeNull();
    });
});
