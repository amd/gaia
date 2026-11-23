// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { translator } from '../../i18n/useT';
import { providerName, providerNotice } from '../providerText';

const ar = translator('ar');

describe('provider text', () => {
    it('translates Local and keeps cloud brand names', () => {
        expect(providerName(ar, { id: 'local', name: 'Local' })).toBe('محلي');
        expect(providerName(ar, { id: 'fireworks', name: 'Fireworks AI' })).toBe('Fireworks AI');
    });

    it("translates the backend's privacy notice", () => {
        const notice = providerNotice(ar, { id: 'amd', privacy_notice: 'Chat history is sent to your configured AMD gateway.' });
        expect(notice).toContain('يُرسَل سجل المحادثة إلى بوابة AMD');
    });

    it('shows a reworded backend notice as sent rather than an out-of-date translation', () => {
        const sent = 'Chat history is sent to Fireworks AI and kept for 30 days.';
        expect(providerNotice(ar, { id: 'fireworks', privacy_notice: sent })).toBe(sent);
    });
});
