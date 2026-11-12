// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { StartCards, cardText } from '../StartCards';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import type { StartCard } from '../../types';

const card = (skill: string, title: string, note: string): StartCard =>
    ({ skill, title, note, prompt: 'p', needs_input: false });

const arText = (c: StartCard) => {
    const { title, note } = cardText(c, translator('ar'));
    return { title: stripIsolates(title), note: stripIsolates(note) };
};

afterEach(() => act(() => useChatStore.getState().setLocale('en')));

describe('new-chat cards in Arabic', () => {
    it('translates the cards the server sends, params included', () => {
        expect(arText(card('research-report', 'Research a topic', 'A cited report from the web'))).toEqual({
            title: 'البحث في موضوع (Research a topic)',
            note: 'تقرير موثّق بالمصادر من الويب (A cited report from the web)',
        });
        expect(arText(card('document-brief', 'Ask about your documents', '3 indexed documents')).note)
            .toBe('المستندات المفهرسة: 3 (3 indexed documents)');
        expect(arText(card('coding', 'Work on code', 'In gaia')).note).toBe('في gaia (In gaia)');
    });

    it('names a hyphenated folder once per half, isolated, with each half kept whole', () => {
        const folder = 'friendly-zhukovsky-a7b072';
        const { note } = cardText(card('coding', 'Work on code', `In ${folder}`), translator('ar'));
        expect(stripIsolates(note)).toBe(`في ${folder} (In ${folder})`);
        expect(note.split(`⁨${folder}⁩`)).toHaveLength(3);

        act(() => useChatStore.getState().setLocale('ar'));
        const { container } = render(
            <StartCards cards={[card('coding', 'Work on code', `In ${folder}`)]} onPick={() => {}} />,
        );
        const parts = [...container.querySelectorAll('.start-card-note .start-card-part')].map((p) => stripIsolates(p.textContent ?? ''));
        expect(parts).toEqual([`في ${folder}`, `(In ${folder})`]);
    });

    it('shows a card as sent when its English is not the one the dictionary knows', () => {
        expect(arText(card('research-report', 'Research anything', 'Reworded'))).toEqual({
            title: 'Research anything', note: 'Reworded',
        });
        expect(arText(card('new-skill', 'Brand new', 'Not translated yet')).title).toBe('Brand new');
    });

    it('leaves English untouched', () => {
        expect(cardText(card('coding', 'Work on code', 'In gaia'), translator('en')))
            .toEqual({ title: 'Work on code', note: 'In gaia' });
    });
});
