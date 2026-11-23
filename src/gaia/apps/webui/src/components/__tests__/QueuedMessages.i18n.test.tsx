// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The queue above the composer speaks the chosen language, not English only. */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { QueuedMessages } from '../QueuedMessages';
import { useChatStore } from '../../stores/chatStore';
import { stripIsolates } from '../../i18n/registry';
import type { QueuedMessage } from '../../utils/messageQueue';

const items: QueuedMessage[] = [
    { id: 1, text: 'first', state: 'queued' },
    { id: 2, text: 'second', state: 'failed', error: 'offline' },
];

beforeEach(() => useChatStore.setState({ locale: 'ar' }));
afterEach(() => useChatStore.setState({ locale: 'en' }));

describe('queued messages in Arabic', () => {
    it('words every state and button in Arabic', () => {
        render(<QueuedMessages items={items} onRemove={() => {}} onRetry={() => {}} onSteer={() => {}} />);
        const shown = stripIsolates(document.body.textContent ?? '');
        for (const phrase of ['في قائمة الانتظار. تُرسَل عند انتهاء هذا الرد.', 'لم تُرسَل. offline', 'أضِفها إلى هذا الرد', 'إعادة الإرسال'])
            expect(shown).toContain(phrase);
        const labels = [...document.querySelectorAll('[aria-label]')].map((el) => stripIsolates(el.getAttribute('aria-label') ?? ''));
        for (const phrase of ['رسائل تنتظر الإرسال', 'إعادة إرسال: second', 'إزالة الرسالة المنتظرة: first'])
            expect(labels.some((label) => label.includes(phrase)), phrase).toBe(true);
    });
});
