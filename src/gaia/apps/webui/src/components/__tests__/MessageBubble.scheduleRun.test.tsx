// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A scheduled run's divider reads as a plain label, not the raw stored marker.

import { afterEach, describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import { stripIsolates } from '../../i18n/registry';
import { resetUiLocale, setUiLocale } from '../../test/dates';
import type { Message } from '../../types';

const marker: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'system',
    content: '[schedule-run] Run #3 · 2026-11-12 09:00 UTC',
    created_at: '2026-11-12T09:00:00.000Z',
    rag_sources: null,
};

afterEach(resetUiLocale);

describe('MessageBubble scheduled-run divider', () => {
    it('shows a plain label in English', () => {
        const { container } = render(<MessageBubble message={marker} />);
        expect(container.textContent).toBe('Scheduled run 3 · 2026-11-12 09:00 UTC');
        expect(container.textContent).not.toContain('[schedule-run]');
    });

    it('shows the label in Arabic', () => {
        setUiLocale('ar');
        const { container } = render(<MessageBubble message={marker} />);
        expect(stripIsolates(container.textContent ?? '')).toContain('التشغيل المجدول 3');
        expect(container.textContent).not.toContain('[schedule-run]');
    });
});
