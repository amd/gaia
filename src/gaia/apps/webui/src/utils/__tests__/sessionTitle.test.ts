// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { shownTitle } from '../sessionTitle';

const ar = translator('ar');
const en = translator('en');

describe('chat titles GAIA writes itself', () => {
    it('reads a scheduled task chat in Arabic, keeping the task name', () => {
        const title = stripIsolates(shownTitle(ar, { title: 'Schedule: Morning brief', kind: 'scheduled' }));
        expect(title.startsWith('مهمة مجدولة: Morning brief')).toBe(true);
    });

    it('reads the goals chat in Arabic', () => {
        expect(stripIsolates(shownTitle(ar, { title: 'Background goals', kind: 'scheduled' }))).toContain('أهداف في الخلفية');
    });

    it('reads as stored in English', () => {
        expect(shownTitle(en, { title: 'Schedule: Morning brief', kind: 'scheduled' })).toBe('Schedule: Morning brief');
        expect(shownTitle(en, { title: 'Background goals', kind: 'scheduled' })).toBe('Background goals');
    });

    it("leaves the user's own chats as they named them", () => {
        expect(shownTitle(ar, { title: 'Background goals', kind: 'chat' })).toBe('Background goals');
        expect(shownTitle(ar, { title: 'Schedule: dentist', kind: 'chat' })).toBe('Schedule: dentist');
    });
});
