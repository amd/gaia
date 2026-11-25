// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The queue above the composer speaks the chosen language, not English only. */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { QueuedMessages } from '../QueuedMessages';
import { useChatStore } from '../../stores/chatStore';
import { stripIsolates } from '../../i18n/registry';
import { INTERRUPTED_ERROR, retiredAgentError, STEER_REFUSED_NOTE, STOPPED_ERROR, type QueuedMessage } from '../../utils/messageQueue';

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

    it('words the note on a message the running reply refused in Arabic', () => {
        render(<QueuedMessages items={[{ id: 3, text: 'third', state: 'queued', note: STEER_REFUSED_NOTE }]} onRemove={() => {}} onRetry={() => {}} />);
        expect(stripIsolates(document.body.textContent ?? '')).toContain('تعذّرت إضافتها إلى هذا الرد؛ ستُرسَل عند انتهائه.');
    });

    it('words the reason a message was not sent in Arabic, not English', () => {
        render(<QueuedMessages
            items={[
                { id: 4, text: 'a', state: 'failed', error: STOPPED_ERROR },
                { id: 5, text: 'b', state: 'failed', error: retiredAgentError('doc') },
                { id: 6, text: 'c', state: 'failed', error: INTERRUPTED_ERROR },
            ]}
            onRemove={() => {}}
            onRetry={() => {}}
        />);
        const shown = stripIsolates(document.body.textContent ?? '');
        for (const phrase of [
            'لم تُرسَل. أُوقِف الإرسال قبل أن يؤكد GAIA استلامه لهذه الرسالة.',
            'لم يعد وكيل "doc" الخاص بهذه المحادثة متاحًا',
            'أُغلق GAIA أثناء إرسال هذه الرسالة',
        ])
            expect(shown).toContain(phrase);
        // English shows once, as the bracketed gloss after the whole line.
        expect(shown).toContain('(Not sent. Stopped before GAIA confirmed it got this message.)');
        expect(shown).not.toContain('(Stopped before');
    });
});
