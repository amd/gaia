// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SpendMeter, formatUsd, startOfLocalDay } from '../SpendMeter';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { ActiveModel, ChatSpending } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const FLASH = 'fireworks.deepseek-v4p1-flash';
const CLOUD: ActiveModel = { provider: 'fireworks', model: FLASH, label: 'DeepSeek', remote: true, is_default: false };
const LOCAL: ActiveModel = { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true };

function spending(over: Partial<ChatSpending> = {}): ChatSpending {
    return {
        paid: true,
        model: FLASH,
        chat: {
            usd: 0.0425,
            answers: 3,
            unpriced_answers: 1,
            items: [
                { message_id: 1, created_at: '2026-11-05T17:00:00+00:00', model: 'DeepSeek', kind: 'estimate', usd: 0.0405 },
                { message_id: 2, created_at: '2026-11-05T17:05:00+00:00', model: 'DeepSeek', kind: 'estimate', usd: 0.002 },
                { message_id: 3, created_at: '2026-11-05T17:09:00+00:00', model: 'DeepSeek', kind: 'unreported', usd: null },
            ],
        },
        today: { usd: 0.31, answers: 9, unpriced_answers: 1, chats: 4, since: '2026-11-05T08:00:00+00:00' },
        price_source: 'Fireworks AI list price',
        ...over,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: CLOUD });
    useChatStore.setState({ sessions: [], currentSessionId: 's1', settingsSection: null, settingsProvider: null });
});

describe('formatUsd', () => {
    it('keeps a share of a cent from reading as nothing', () => {
        expect(formatUsd(0)).toBe('$0.00');
        expect(formatUsd(0.0042)).toBe('$0.0042');
        expect(formatUsd(0.31)).toBe('$0.31');
        expect(formatUsd(12.345)).toBe('$12.35');
    });
});

describe('startOfLocalDay', () => {
    it('is local midnight of the given day', () => {
        const now = new Date(2026, 10, 5, 15, 30);
        const start = new Date(startOfLocalDay(now));
        expect([start.getFullYear(), start.getMonth(), start.getDate(), start.getHours(), start.getMinutes()])
            .toEqual([2026, 10, 5, 0, 0]);
    });
});

describe('SpendMeter', () => {
    it('shows this chat and today, and asks for the model the chip shows', async () => {
        const all = spending();
        mockedApi.getChatSpending.mockResolvedValue(spending({
            chat: { ...all.chat, unpriced_answers: 0 },
            today: { ...all.today, unpriced_answers: 0 },
        }));
        render(<SpendMeter sessionId="s1" busy={false} />);
        const chip = await screen.findByRole('button', { name: /Cloud spending: \$0\.04 in this chat, \$0\.31 today/ });
        expect(chip.textContent).toContain('$0.04');
        expect(chip.textContent).toContain('today $0.31');
        expect(chip.textContent).not.toContain('≥');
        expect(mockedApi.getChatSpending).toHaveBeenCalledWith('s1', expect.any(String), FLASH);
        // Amounts are isolated left-to-right, so an Arabic layout can't reverse "$0.04".
        expect(chip.querySelectorAll('bdi[dir="ltr"]')).toHaveLength(2);
    });

    it('lists each answer, newest first, in the breakdown', async () => {
        mockedApi.getChatSpending.mockResolvedValue(spending());
        render(<SpendMeter sessionId="s1" busy={false} />);
        fireEvent.click(await screen.findByRole('button', { name: /Cloud spending/ }));
        const panel = await screen.findByRole('dialog', { name: 'Cloud spending' });
        expect(within(panel).getByText('This chat')).toBeTruthy();
        expect(within(panel).getByText('Today, across 4 chats')).toBeTruthy();
        const rows = panel.querySelectorAll('.spend-meter-answers .context-meter-row dd');
        expect(Array.from(rows).map((r) => r.textContent)).toEqual(['Not reported', '$0.0020', '$0.04']);
    });

    it('marks totals missing a stopped answer as a floor and says why', async () => {
        mockedApi.getChatSpending.mockResolvedValue(spending({
            today: { ...spending().today, unpriced_answers: 2 },
        }));
        render(<SpendMeter sessionId="s1" busy={false} />);
        const chip = await screen.findByRole('button', { name: /≥ \$0\.04 in this chat, ≥ \$0\.31 today/ });
        expect(chip.title).toMatch(/stopped answer isn't counted/);
        fireEvent.click(chip);
        const panel = await screen.findByRole('dialog', { name: 'Cloud spending' });
        expect(within(panel).getByRole('note').textContent)
            .toMatch(/Not counted: 1 stopped answer\(s\) here, 2 today\..*may still bill it/);
    });

    it('opens Settings for the monthly total and limit', async () => {
        const openSettings = vi.fn();
        useChatStore.setState({ openSettings });
        mockedApi.getChatSpending.mockResolvedValue(spending());
        render(<SpendMeter sessionId="s1" busy={false} />);
        fireEvent.click(await screen.findByRole('button', { name: /Cloud spending/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'This month\'s total and limit' }));
        expect(openSettings).toHaveBeenCalledWith('model');
    });

    it('shows nothing for a model on this PC', async () => {
        useModelStore.setState({ active: LOCAL });
        mockedApi.getChatSpending.mockResolvedValue(spending({ paid: false, model: LOCAL.model }));
        const { container } = render(<SpendMeter sessionId="s1" busy={false} />);
        await waitFor(() => expect(mockedApi.getChatSpending).toHaveBeenCalled());
        await act(async () => {});
        expect(container.innerHTML).toBe('');
    });

    it('hides an answer about another model until the new one is read', async () => {
        mockedApi.getChatSpending.mockResolvedValue(spending({ model: 'fireworks.other' }));
        const { container } = render(<SpendMeter sessionId="s1" busy={false} />);
        await waitFor(() => expect(mockedApi.getChatSpending).toHaveBeenCalled());
        await act(async () => {});
        expect(container.innerHTML).toBe('');
    });

    it('re-reads when the window regains focus, so "today" follows other chats and midnight', async () => {
        mockedApi.getChatSpending.mockResolvedValue(spending());
        render(<SpendMeter sessionId="s1" busy={false} />);
        await screen.findByRole('button', { name: /Cloud spending/ });
        mockedApi.getChatSpending.mockResolvedValue(spending({ today: { ...spending().today, usd: 0.75 } }));
        act(() => { window.dispatchEvent(new Event('focus')); });
        await screen.findByRole('button', { name: /\$0\.75 today/ });
    });

    it('re-reads once a reply ends', async () => {
        mockedApi.getChatSpending.mockResolvedValue(spending());
        const { rerender } = render(<SpendMeter sessionId="s1" busy />);
        await screen.findByRole('button', { name: /Cloud spending/ });
        mockedApi.getChatSpending.mockResolvedValue(spending({ chat: { ...spending().chat, usd: 0.09 } }));
        rerender(<SpendMeter sessionId="s1" busy={false} />);
        await screen.findByRole('button', { name: /\$0\.09 in this chat/ });
    });
});
