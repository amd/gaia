// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { ResearchChip } from '../ResearchChip';
import { NEW_CHAT_RESEARCH, researchOn, useChatStore } from '../../stores/chatStore';

beforeEach(() => {
    useChatStore.setState({ research: {} });
});

describe('ResearchChip', () => {
    it('toggles research mode and says what it does either way', async () => {
        render(<ResearchChip sessionId={null} />);
        const chip = screen.getByRole('button', { name: 'Research' });
        expect(chip).toHaveAttribute('aria-pressed', 'false');
        expect(chip).toHaveAttribute('title', expect.stringMatching(/start a message with \/research/));

        await userEvent.click(chip);
        expect(chip).toHaveAttribute('aria-pressed', 'true');
        expect(chip).toHaveClass('is-on');
        expect(chip).toHaveAttribute('title', expect.stringMatching(/Click to turn it off/));
        expect(researchOn(useChatStore.getState(), null)).toBe(true);

        await userEvent.click(chip);
        expect(researchOn(useChatStore.getState(), null)).toBe(false);
    });

    it('switches only its own chat', async () => {
        render(
            <>
                <ResearchChip sessionId="a" />
                <ResearchChip sessionId="b" />
            </>,
        );
        const [a, b] = screen.getAllByRole('button', { name: 'Research' });
        await userEvent.click(a);
        expect(a).toHaveAttribute('aria-pressed', 'true');
        expect(b).toHaveAttribute('aria-pressed', 'false');
        const state = useChatStore.getState();
        expect([researchOn(state, 'a'), researchOn(state, 'b'), researchOn(state, null)]).toEqual([true, false, false]);
    });

    it('cannot be switched while disabled', async () => {
        render(<ResearchChip sessionId={null} disabled />);
        const chip = screen.getByRole('button', { name: 'Research' });
        expect(chip).toBeDisabled();
        await userEvent.click(chip);
        expect(researchOn(useChatStore.getState(), null)).toBe(false);
    });
});

describe('ResearchChip in a chat', () => {
    it('drops its label when compact but keeps its name and tooltip', () => {
        render(<ResearchChip sessionId="a" compact />);
        const chip = screen.getByRole('button', { name: 'Research' });
        expect(chip.querySelector('.composer-chip-label')).toBeNull();
        expect(chip).toHaveAttribute('title', expect.stringMatching(/Research a topic/));
    });
});

describe('a new chat takes the Research choice', () => {
    it('when its first message went out as a research turn', () => {
        useChatStore.setState({ research: { [NEW_CHAT_RESEARCH]: true } });
        useChatStore.getState().adoptDraftResearch('s1', '/research the EU AI Act');
        const state = useChatStore.getState();
        expect([researchOn(state, 's1'), researchOn(state, null)]).toEqual([true, false]);
    });

    it('not when the chat started from somewhere else with a plain message', () => {
        useChatStore.setState({ research: { [NEW_CHAT_RESEARCH]: true } });
        useChatStore.getState().adoptDraftResearch('s2', 'Summarise this file');
        const state = useChatStore.getState();
        expect([researchOn(state, 's2'), researchOn(state, null)]).toEqual([false, true]);
    });
});
