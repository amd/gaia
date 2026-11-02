// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { bracketParts, glossParts, stripIsolates, translate, withHint } from '../registry';

const newChat = translate('ar', 'sidebar.newChat');
const documents = translate('ar', 'sidebar.documents');

const glosses = (root: Element) => [...root.querySelectorAll('.i18n-gloss')].map((g) => stripIsolates(g.textContent ?? ''));

afterEach(() => vi.restoreAllMocks());

describe('glossParts', () => {
    it('finds nothing in text without a bracketed label', () => {
        expect(glossParts('New chat')).toBeNull();
        expect(glossParts(translate('en', 'sidebar.newChat'))).toBeNull();
    });

    it('splits a label, isolates and space intact, so joining it back loses nothing', () => {
        const parts = glossParts(newChat)!;
        expect(parts.map((p) => p.gloss)).toEqual([false, true]);
        expect(stripIsolates(parts[1].text)).toBe('(New chat)');
        expect(parts[1].text.startsWith('⁦(')).toBe(true);
        expect(parts.map((p) => p.text).join('')).toBe(newChat);
    });

    it('splits several labels and keeps the text around them', () => {
        const text = `قبل ${newChat} · ${documents}: 3`;
        const parts = glossParts(text)!;
        expect(parts.filter((p) => p.gloss).map((p) => stripIsolates(p.text))).toEqual(['(New chat)', '(Documents)']);
        expect(parts.map((p) => p.text).join('')).toBe(text);
    });

    it('reads past a name isolated inside the English', () => {
        const label = translate('ar', 'composer.remove', { name: 'a.pdf' });
        const parts = glossParts(`${label}!`)!;
        expect(stripIsolates(parts[1].text)).toBe('(Remove a.pdf)');
        expect(parts[2]).toEqual({ text: '!', gloss: false });
    });

    it('takes a hint added after a label as its own gloss', () => {
        const parts = glossParts(withHint('ar', newChat, 'Ctrl+N'))!;
        expect(parts.filter((p) => p.gloss).map((p) => stripIsolates(p.text))).toEqual(['(New chat)', '(Ctrl+N)']);
    });

    it('reads the English half bracketParts hands a renderer', () => {
        const [, english] = bracketParts(newChat);
        expect(glossParts(english)).toEqual([{ text: english, gloss: true }]);
    });

    it('leaves an unclosed isolate alone rather than guess where it ends', () => {
        expect(glossParts('⁩ ⁦(never closed')).toBeNull();
    });
});

describe('JSX runtime', () => {
    it('wraps a label once, so a flex parent still sees one item', () => {
        const { container } = render(<button>{newChat}</button>);
        const button = container.querySelector('button')!;
        expect(button.children).toHaveLength(1);
        expect(button.children[0].tagName).toBe('I18N-LABEL');
        expect(button.textContent).toBe(newChat);
        expect(glosses(button)).toEqual(['(New chat)']);
    });

    it('marks the gloss as English', () => {
        const { container } = render(<p>{newChat}</p>);
        expect(container.querySelector('.i18n-gloss')).toHaveAttribute('lang', 'en');
    });

    it('splits labels among other children', () => {
        const { container } = render(<p>{newChat}: <b>3</b> {documents}</p>);
        expect(glosses(container)).toEqual(['(New chat)', '(Documents)']);
        expect(container.querySelector('b')).toHaveTextContent('3');
        expect(container.textContent).toBe(`${newChat}: 3 ${documents}`);
    });

    it('splits a label with text around it, and two labels in one string', () => {
        const { container } = render(<p>{`قبل ${newChat} · ${documents}`}</p>);
        expect(container.querySelectorAll('i18n-label')).toHaveLength(1);
        expect(glosses(container)).toEqual(['(New chat)', '(Documents)']);
    });

    it('splits inside mapped arrays without a React key warning', () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        const { container } = render(<ul>{[newChat, documents].map((l) => <li key={l}>{[l, ' ', l]}</li>)}</ul>);
        expect(glosses(container)).toHaveLength(4);
        expect(error).not.toHaveBeenCalled();
    });

    it('swaps a label for plain text when the language changes', () => {
        const { container, rerender } = render(<p>{newChat}</p>);
        rerender(<p>{translate('en', 'sidebar.newChat')}</p>);
        expect(container.innerHTML).toBe('<p>New chat</p>');
    });

    it('leaves hosts alone that cannot hold an element', () => {
        const html = renderToStaticMarkup(
            <>
                <select><option>{newChat}</option></select>
                <textarea defaultValue="" />
                <svg><title>{newChat}</title><text>{newChat}</text></svg>
                <style>{newChat}</style>
            </>,
        );
        expect(html).not.toContain('i18n');
    });

    it('leaves attributes alone', () => {
        const { container } = render(<button aria-label={newChat} title={documents} />);
        const button = container.querySelector('button')!;
        expect(button).toHaveAttribute('aria-label', newChat);
        expect(button).toHaveAttribute('title', documents);
    });

    it('passes a component its string untouched, and splits where a host renders it', () => {
        const seen: unknown[] = [];
        function Card({ children }: { children: string }) {
            seen.push(children);
            return <h2>{children}</h2>;
        }
        const { container } = render(<Card>{newChat}</Card>);
        expect(seen[0]).toBe(newChat);
        expect(glosses(container)).toEqual(['(New chat)']);
    });

    it('does not touch English text', () => {
        const { container } = render(<p>{translate('en', 'sidebar.newChat')}</p>);
        expect(container.innerHTML).toBe('<p>New chat</p>');
    });
});
