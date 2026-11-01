// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { enMessages } from '../locales/en';
import { LOCALES, getLocale, localeOptionLabel, ref, stripIsolates, translate, type MessageKey } from '../registry';
import { PALETTES } from '../../utils/palette';
import { translator, useT } from '../useT';
import { readLocale, useChatStore } from '../../stores/chatStore';
import { readsAs } from '../../test/readsAs';
import { GeneralSettings } from '../../components/settings/GeneralSettings';

const EN_KEYS = Object.keys(enMessages).sort();
const ar =(key: MessageKey, params?: Parameters<typeof translate>[2]) => stripIsolates(translate('ar', key, params));
const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
});

describe('locale registry', () => {
    it.each(LOCALES.map((l) => [l.meta.code, l] as const))('%s has exactly the English keys, each translated', (_code, locale) => {
        expect(Object.keys(locale.messages).sort()).toEqual(EN_KEYS);
        for (const key of EN_KEYS as MessageKey[]) {
            expect(locale.messages[key].trim(), key).not.toBe('');
            // A dropped or misspelled {param} would render raw or throw at runtime.
            expect(placeholders(locale.messages[key]), key).toEqual(placeholders(enMessages[key]));
        }
    });

    it('registers each code once, with metadata Settings can list', () => {
        const codes = LOCALES.map((l) => l.meta.code);
        expect(new Set(codes).size).toBe(codes.length);
        expect(LOCALES.map((l) => stripIsolates(localeOptionLabel(l.meta)))).toEqual(['English', 'العربية (Arabic)']);
        expect(getLocale('ar').meta.dir).toBe('rtl');
    });

    it('refuses an unregistered code instead of guessing', () => {
        expect(() => getLocale('xx')).toThrow(/Unknown UI locale "xx"/);
    });
});

describe('translate', () => {
    it('renders English as-is', () => {
        expect(translate('en', 'sidebar.newChat')).toBe('New chat');
        expect(translate('en', 'composer.remove', { name: 'a.pdf' })).toBe('Remove a.pdf');
    });

    it('renders Arabic with the English in brackets', () => {
        expect(ar('sidebar.newChat')).toBe('محادثة جديدة (New chat)');
        expect(ar('sidebar.documents')).toBe('المستندات (Documents)');
        expect(ar('composer.send')).toBe('إرسال (Send)');
    });

    it('isolates each half, so RTL text cannot reorder the English brackets and punctuation', () => {
        // RLI <arabic> PDI, then LRI (<english>) PDI.
        expect(translate('ar', 'sidebar.empty')).toBe('\u2067لا توجد محادثات بعد.\u2069 \u2066(No chats yet.)\u2069');
    });

    it('fills params in both halves', () => {
        expect(ar('composer.remove', { name: 'تقرير.pdf' })).toBe('إزالة تقرير.pdf (Remove تقرير.pdf)');
    });

    it('resolves a message param in each language, without nested brackets', () => {
        expect(ar('tool.failed', { label: ref('tool.semantic_search.active') }))
            .toBe('تعذّر: البحث في المستندات (Searching documents failed)');
        expect(translate('en', 'tool.failed', { label: ref('tool.semantic_search.active') }))
            .toBe('Searching documents failed');
    });

    it('fails loudly when a param is missing', () => {
        expect(() => translate('en', 'composer.remove')).toThrow(/needs a \{name\} param/);
    });

    it('picks the plural form by count', () => {
        const t = translator('en');
        expect(t.plural('docs.count', 1)).toBe('1 document');
        expect(t.plural('docs.count', 3)).toBe('3 documents');
        expect(stripIsolates(translator('ar').plural('docs.count', 3))).toBe('المستندات: 3 (3 documents)');
    });
});

function Label({ k }: { k: MessageKey }) {
    const t = useT();
    return <span>{t(k)}</span>;
}

describe('useT and the language setting', () => {
    it('re-renders in the chosen language', () => {
        render(<Label k="sidebar.newChat" />);
        expect(screen.getByText('New chat')).toBeInTheDocument();
        act(() => useChatStore.getState().setLocale('ar'));
        expect(screen.getByText(readsAs('محادثة جديدة (New chat)'))).toBeInTheDocument();
    });

    it('flips the document to right-to-left, and back', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        expect(document.documentElement.dir).toBe('rtl');
        expect(document.documentElement.lang).toBe('ar');
        act(() => useChatStore.getState().setLocale('en'));
        expect(document.documentElement.dir).toBe('ltr');
        expect(document.documentElement.lang).toBe('en');
    });

    it('remembers the choice across reloads', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        expect(localStorage.getItem('gaia-chat-locale')).toBe('ar');
        expect(readLocale()).toBe('ar');
    });

    it('reads a saved code that is no longer registered as English', () => {
        localStorage.setItem('gaia-chat-locale', 'xx');
        expect(readLocale()).toBe('en');
    });

    it('is a Settings > General option listing every registered locale', async () => {
        render(<GeneralSettings />);
        const select = screen.getByLabelText('Language') as HTMLSelectElement;
        expect([...select.options].map((o) => stripIsolates(o.textContent ?? ''))).toEqual(['English', 'العربية (Arabic)']);
        await userEvent.selectOptions(select, 'ar');
        expect(useChatStore.getState().locale).toBe('ar');
        expect(screen.getByLabelText(readsAs('اللغة (Language)'))).toHaveValue('ar');
        expect(screen.getByRole('heading', { name: readsAs('عام (General)') })).toBeInTheDocument();
        expect(screen.getByText(readsAs('رمادي دافئ (Warm gray)'))).toBeInTheDocument();
    });

    it('names every palette in English exactly as the palette list does', () => {
        for (const p of PALETTES) expect(translate('en', `palette.${p.id}` as MessageKey)).toBe(p.name);
    });
});
