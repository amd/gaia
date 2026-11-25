// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../stores/chatStore';
import { TryChips } from '../TryChips';
import type { StartCard } from '../../types';

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

const CARDS: StartCard[] = [
    { skill: 'document-brief', title: 'Ask about your documents', note: '2 indexed documents', prompt: 'Using my indexed documents, give me the key points.', needs_input: false },
    { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research and write a short cited report on: ', needs_input: true },
    { skill: 'coding', title: 'Work on code', note: 'In my-project', prompt: 'Look around my-project and tell me what this project does.', needs_input: false },
];

const names = () => screen.getAllByRole('button').map((b) => b.textContent);

describe('TryChips', () => {
    it('fills the English prompt in English', () => {
        const onPick = vi.fn();
        render(<TryChips onPick={onPick} />);
        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));
        expect(onPick).toHaveBeenCalledWith('Give me a banana bread recipe');
    });

    it('shows three examples in one labelled group when this PC offers no tasks', () => {
        render(<TryChips cards={[]} onPick={vi.fn()} />);
        expect(screen.getByRole('group', { name: 'Things to try' })).toBeInTheDocument();
        expect(names()).toEqual(['Recipe', 'Checklist', 'Split a bill']);
    });

    it("puts this PC's first two tasks before one example, three in all", () => {
        render(<TryChips cards={CARDS} onPick={vi.fn()} />);
        expect(names()).toEqual(['Ask about your documents', 'Research a topic', 'Recipe']);
    });

    it('fills a task with the prompt as sent, even one that needs nothing typed', () => {
        const onPick = vi.fn();
        render(<TryChips cards={CARDS} onPick={onPick} />);
        fireEvent.click(screen.getByRole('button', { name: /^Ask about your documents:/ }));
        fireEvent.click(screen.getByRole('button', { name: /^Research a topic:/ }));
        expect(onPick.mock.calls).toEqual([[CARDS[0].prompt], [CARDS[1].prompt]]);
    });

    it('labels and fills every example in Arabic (#7203)', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        const onPick = vi.fn();
        render(<TryChips onPick={onPick} />);
        const chips = screen.getAllByRole('button');
        expect(chips).toHaveLength(3);
        for (const chip of chips) {
            expect(chip.textContent).toMatch(/[؀-ۿ]/);
            fireEvent.click(chip);
        }
        expect(onPick).toHaveBeenCalledTimes(3);
        for (const [prompt] of onPick.mock.calls) {
            expect(prompt).toMatch(/[؀-ۿ]/);
            expect(prompt).not.toMatch(/[A-Za-z]{2,}/);
        }
    });

    it("names this PC's tasks in Arabic", () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<TryChips cards={CARDS} onPick={vi.fn()} />);
        const [docs, research] = names();
        expect(docs).toContain('اسأل عن مستنداتك');
        expect(research).toContain('البحث في موضوع');
    });
});
