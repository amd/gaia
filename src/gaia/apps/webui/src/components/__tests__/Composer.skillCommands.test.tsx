// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from '../Composer';
import { placeSkillMenu } from '../SkillCommandMenu';
import { useAttachments } from '../../hooks/useAttachments';
import * as api from '../../services/api';
import type { SkillInfo } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function skill(name: string, description: string): SkillInfo {
    return { name, description, version: null, tier: 'local', tools: [], path: '' } as unknown as SkillInfo;
}

const SKILLS = [
    skill('pdf', 'Make and edit PDF files. Use when the user asks for a PDF.'),
    skill('pptx', 'Build slide decks.'),
    skill('web-research', 'Research a topic on the web.'),
];

function Harness({ onSubmit = vi.fn() }) {
    const [value, setValue] = useState('');
    const attachments = useAttachments();
    return <Composer value={value} onChange={setValue} onSubmit={onSubmit} attachments={attachments} />;
}

function box() {
    return screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement;
}

function type(text: string) {
    fireEvent.change(box(), { target: { value: text } });
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listSkills.mockResolvedValue({ skills: SKILLS, invalid: {} });
});

describe('Composer "/" skill commands', () => {
    it('lists installed skills matching what is typed, with a short description', async () => {
        render(<Harness />);
        type('/p');

        const options = await screen.findAllByRole('option');
        expect(options.map((o) => o.textContent)).toEqual([
            '/pdfMake and edit PDF files.',
            '/pptxBuild slide decks.',
        ]);
        expect(box()).toHaveAttribute('aria-activedescendant', options[0].id);
    });

    it('fills in the chosen name with arrows and Enter, without sending', async () => {
        const onSubmit = vi.fn();
        render(<Harness onSubmit={onSubmit} />);
        type('/p');
        await screen.findAllByRole('option');

        fireEvent.keyDown(box(), { key: 'ArrowDown' });
        fireEvent.keyDown(box(), { key: 'Enter' });

        expect(box().value).toBe('/pptx ');
        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.queryByRole('listbox')).toBeNull();
    });

    it('sends a name typed in full on Enter', async () => {
        const onSubmit = vi.fn();
        render(<Harness onSubmit={onSubmit} />);
        type('/pdf');
        await screen.findAllByRole('option');

        fireEvent.keyDown(box(), { key: 'Enter' });

        expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    it('fills in a clicked skill', async () => {
        render(<Harness />);
        type('/');
        fireEvent.click(await screen.findByText('/web-research'));

        expect(box().value).toBe('/web-research ');
    });

    it('hides on Escape and claims the key so a running reply is not stopped', async () => {
        render(<Harness />);
        type('/p');
        await screen.findAllByRole('option');

        const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
        act(() => { box().dispatchEvent(escape); });

        expect(escape.defaultPrevented).toBe(true);
        expect(screen.queryByRole('listbox')).toBeNull();
    });

    it('stays shut for paths and ordinary text', async () => {
        render(<Harness />);
        type('/tmp/notes.txt');
        type('summarize /pdf');
        type('/zzz');
        await Promise.resolve();

        expect(screen.queryByRole('listbox')).toBeNull();
    });

    describe('stays inside the window', () => {
        const many = Array.from({ length: 20 }, (_, i) => skill(`skill-${i}`, 'Does a thing.'));

        /** Lays the composer out at `top`..`bottom` in a window `height` tall, with a list 700px long. */
        function layout(top: number, bottom: number, height: number) {
            vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(height);
            vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(700);
            vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(this: HTMLElement) {
                const y = this.classList.contains('composer') ? top : 0;
                const h = this.classList.contains('composer') ? bottom - top : 0;
                return { x: 0, y, top: y, bottom: y + h, left: 0, right: 0, width: 0, height: h, toJSON: () => ({}) } as DOMRect;
            });
        }

        async function openMenu() {
            mockedApi.listSkills.mockResolvedValue({ skills: many, invalid: {} });
            render(<Harness />);
            type('/');
            await screen.findAllByRole('option');
            return document.querySelector<HTMLElement>('.skill-menu')!;
        }

        afterEach(() => { vi.restoreAllMocks(); });

        it('opens downward on the new-chat screen, where the box sits high in a short window', async () => {
            layout(261, 375, 800);
            const el = await openMenu();
            expect(el).toHaveClass('is-below');
            expect(el.style.maxHeight).toBe('320px');
        });

        it('stays above the box in a chat, where the box sits at the bottom', async () => {
            layout(660, 780, 800);
            const el = await openMenu();
            expect(el).not.toHaveClass('is-below');
            expect(el.style.maxHeight).toBe('320px');
        });

        it('shrinks to the room there is when neither side holds the whole list', async () => {
            layout(120, 240, 400);
            const el = await openMenu();
            expect(el).toHaveClass('is-below');
            expect(el.style.maxHeight).toBe('146px');
        });

        it('follows the window when it is resized', async () => {
            layout(660, 780, 800);
            const el = await openMenu();
            layout(100, 220, 800);
            act(() => { window.dispatchEvent(new Event('resize')); });
            expect(el).toHaveClass('is-below');
        });

        it('follows the message box when it grows without the window changing', async () => {
            const watched: { el: Element; grew: () => void }[] = [];
            vi.stubGlobal('ResizeObserver', class {
                constructor(private grew: () => void) {}
                observe(el: Element) { watched.push({ el, grew: this.grew }); }
                disconnect() {}
            });
            layout(660, 780, 800);
            const el = await openMenu();
            const box = watched.find((w) => w.el === el.parentElement);
            expect(box).toBeDefined();
            layout(100, 420, 800);
            act(() => { box!.grew(); });
            expect(el).toHaveClass('is-below');
            vi.unstubAllGlobals();
        });

        it('scrolls the row the arrow keys move to into view', async () => {
            const el = await openMenu();
            const into = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
            fireEvent.keyDown(box(), { key: 'ArrowDown' });
            expect(into).toHaveBeenCalledWith({ block: 'nearest' });
            expect(into.mock.contexts[into.mock.contexts.length - 1]).toBe(el.querySelectorAll('[role="option"]')[1]);
        });
    });

    it('prefers above, and never returns more height than the side it picked has', () => {
        expect(placeSkillMenu({ top: 400, bottom: 500 }, 800, 200)).toEqual({ below: false, maxHeight: 320 });
        expect(placeSkillMenu({ top: 100, bottom: 200 }, 800, 60)).toEqual({ below: false, maxHeight: 86 });
        expect(placeSkillMenu({ top: 100, bottom: 200 }, 800, 600)).toEqual({ below: true, maxHeight: 320 });
        expect(placeSkillMenu({ top: 300, bottom: 420 }, 500, 600)).toEqual({ below: false, maxHeight: 286 });
    });

    it('says so when the skills cannot be read', async () => {
        mockedApi.listSkills.mockRejectedValue(new Error('GAIA is not running'));
        render(<Harness />);
        type('/');

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your skills: GAIA is not running");
    });
});
