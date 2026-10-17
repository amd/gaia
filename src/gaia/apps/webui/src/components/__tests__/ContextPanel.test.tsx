// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Context & performance panel: measured numbers, "unknown" otherwise, and no polling. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ContextPanel } from '../ContextPanel';
import { ContextMeter } from '../ContextMeter';
import { useChatStore } from '../../stores/chatStore';
import type { ChatPerformance, ContextUsage, Session, TurnPerformance } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 's1',
    title: 'Report chat',
    created_at: '2026-10-16T00:00:00Z',
    updated_at: '2026-10-16T00:00:00Z',
    model: 'Gemma-4-E4B-it-GGUF',
    system_prompt: null,
    message_count: 4,
    document_ids: [],
    agent_type: 'gaia',
    private: false,
};

const CATEGORIES = [
    { key: 'system', label: 'System prompt', tokens: 3000 },
    { key: 'tools', label: 'Tool definitions', tokens: 2500 },
    { key: 'skills', label: 'Skills', tokens: 500 },
    { key: 'memory', label: 'Memory', tokens: 400 },
    { key: 'documents', label: 'Documents', tokens: 4000 },
    { key: 'messages', label: 'Messages', tokens: 1600 },
];

function usage(over: Partial<ContextUsage> = {}): ContextUsage {
    return {
        window: 65536, reply_reserve: 4096, system: 3900, skills: 500, memory: 400, tools: 2600,
        tool_definitions: 2500, documents: 4000, history: 1500, query: 0, used: 12000,
        history_budget: 20000, history_used: 5600, turns_in_view: 2, turns_hidden: 0, at_risk: [],
        at_risk_tokens: 0, categories: CATEGORIES, model: 'Gemma-4-E4B-it-GGUF',
        tokenizer: { exact: true, model: 'Gemma-4-E4B-it-GGUF' },
        tool_loading: { loaded: ['read_file'], on_demand: ['web_search', 'write_file'] },
        ...over,
    };
}

const TURN: TurnPerformance = {
    message_id: 7,
    created_at: '2026-10-16T00:01:00Z',
    model: 'Gemma-4-E4B-it-GGUF',
    time: { wall_seconds: 14, model_seconds: 8, gaia_seconds: 6, tool_seconds: 1.5, waiting_seconds: 2, own_seconds: 2.5 },
    first_token_seconds: 0.5,
    prefill_tokens_per_second: 1600,
    generation_tokens_per_second: 22.2,
    model_calls: 2,
    tokens_in: 18200,
    tokens_new: 1200,
    tokens_cached: 17000,
    tokens_out: 200,
    last_prompt_tokens: 9200,
    tool_calls: 2,
    tools: [{ name: 'read_file', seconds: 0.5 }, { name: 'write_file', seconds: 3 }],
    loads: [{ model: 'Gemma-4-E4B-it-GGUF', seconds: 2, replaced: [] }],
    cost: { kind: 'local', usd: 0 },
};

function perf(turns: TurnPerformance[] = [TURN]): ChatPerformance {
    return {
        latest_message_id: turns.length ? turns[turns.length - 1].message_id : null,
        turns,
        totals: {
            turns: turns.length, wall_seconds: 14, model_seconds: 8, gaia_seconds: 6, tokens_in: 18200,
            tokens_cached: 17000, tokens_out: 200, tool_calls: 2, loads: 1, load_seconds: 2, usd: 0, unpriced_turns: 0,
        },
        now: {
            model: 'Gemma-4-E4B-it-GGUF', provider: 'local', loaded: true, device: 'gpu', backend: 'vulkan',
            ctx_size: 65536, weights_gb: 5.97, kv_cache_gb: 1.0, free_gb: 40.2, error: null,
        },
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getContextUsage.mockResolvedValue({ available: true, ...usage() });
    mockedApi.getChatPerformance.mockResolvedValue(perf());
    useChatStore.setState({ sessions: [SESSION], currentSessionId: SESSION.id, messages: [] });
});

const panel = () => screen.getByRole('dialog', { name: 'Context and performance' });

describe('ContextPanel', () => {
    it('breaks the window down by category, and the rows add up to the total', async () => {
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        await screen.findByText('18% used');
        const rows = CATEGORIES.map((c) => within(panel()).getByText(c.label));
        expect(rows).toHaveLength(6);
        expect(CATEGORIES.reduce((a, c) => a + c.tokens, 0)).toBe(usage().used);
        expect(screen.getByText(/own tokenizer/)).toBeInTheDocument();
        expect(screen.queryByText('approx.')).toBeNull();
        expect(screen.getByText('2 more tools load only when needed')).toBeInTheDocument();
    });

    it('says approx. with the reason when the model tokenizer was not used', async () => {
        mockedApi.getContextUsage.mockResolvedValue({
            available: true,
            ...usage({ tokenizer: { exact: false, reason: 'The cloud model’s tokenizer is not available here.' } }),
        });
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        const chip = await screen.findByText('approx.');
        expect(chip).toHaveAttribute('title', expect.stringMatching(/cloud model/));
    });

    it('splits the turn into model time and GAIA time, with exact values on hover', async () => {
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        await screen.findByText('In the model');
        expect(screen.getByText('GPU · Vulkan')).toBeInTheDocument();
        expect(screen.getByTitle('8000 ms')).toHaveTextContent('8.0 s');
        expect(screen.getByTitle('6000 ms')).toHaveTextContent('6.0 s');
        expect(screen.getByText('Waiting on you', { exact: false })).toBeInTheDocument();
        expect(screen.getByTitle('22.2 tokens per second')).toHaveTextContent('22 tokens/s');
        expect(screen.getByTitle('17,000 tokens').parentElement).toHaveTextContent('(93%)');
        expect(screen.getByText('$0 · on this PC')).toBeInTheDocument();
    });

    it('does not rate prompt reading from a handful of new tokens, nor round 99.7% up to 100%', async () => {
        mockedApi.getChatPerformance.mockResolvedValue(perf([{
            ...TURN, tokens_in: 17277, tokens_new: 57, tokens_cached: 17220, prefill_tokens_per_second: 41.4,
        }]));
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(await screen.findByText('only 57 new tokens')).toBeInTheDocument();
        expect(screen.queryByText('41 tokens/s')).toBeNull();
        expect(screen.getByTitle('17,220 tokens').parentElement).toHaveTextContent('(99%)');
        expect(screen.getByText(/last request carried/)).toHaveTextContent('9,200 tokens as the model server counted them');
    });

    it('shows unknown, never a made-up number, for what was not measured', async () => {
        mockedApi.getChatPerformance.mockResolvedValue(perf([{
            ...TURN,
            time: { ...TURN.time, model_seconds: null, gaia_seconds: null, own_seconds: null },
            first_token_seconds: null, prefill_tokens_per_second: null, generation_tokens_per_second: null,
            tokens_cached: null, cost: { kind: 'unknown', usd: null },
        }]));
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        await screen.findByText('Took');
        expect(screen.getAllByText('unknown').length).toBeGreaterThanOrEqual(5);
        expect(screen.queryByText('In the model')).toBeNull();
    });

    it('shows the chat totals on request', async () => {
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        await screen.findByText('In the model');
        fireEvent.click(screen.getByRole('button', { name: 'This chat' }));
        expect(screen.getByText('Turns measured').nextSibling).toHaveTextContent('1');
    });

    it('fetches on open and after a reply ends, never while closed or on a timer', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            const { rerender } = render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
            await waitFor(() => expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(1));
            await act(async () => { vi.advanceTimersByTime(60_000); });
            expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(1);
            rerender(<ContextPanel sessionId="s1" busy onClose={() => {}} />);
            expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(1);
            rerender(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
            await waitFor(() => expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(2));
        } finally {
            vi.useRealTimers();
        }
    });

    it('opened mid-reply, shows what it has at once and refreshes when the reply ends', async () => {
        const { rerender } = render(<ContextPanel sessionId="s1" busy onClose={() => {}} />);
        expect(await screen.findByText('18% used')).toBeInTheDocument();
        expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(1);
        rerender(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        await waitFor(() => expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(2));
    });

    it('hands the measured usage to the meter so both show one total', async () => {
        const onUsage = vi.fn();
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} onUsage={onUsage} />);
        await waitFor(() => expect(onUsage).toHaveBeenCalledWith(expect.objectContaining({ used: 12000 })));
    });

    it('shows what was put away and summarized, and says summarized turns stay searchable', async () => {
        mockedApi.getContextUsage.mockResolvedValue({
            available: true,
            ...usage({
                at_risk: ['first question'],
                context_state: {
                    put_away: { results: 3, tokens: 9000 },
                    summarized: { turns: 4, tokens: 12000, summary_tokens: 800 },
                },
            }),
        });
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(await screen.findByText('Put away')).toBeInTheDocument();
        expect(screen.getByText('Put away').nextSibling).toHaveTextContent('3 results · 9.0K');
        expect(screen.getByText('Summarized').nextSibling).toHaveTextContent('4 earlier turns · 12K → 800');
        expect(screen.getByText('Earlier messages summarized — still searchable.')).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Older tool results move out of the prompt and stay readable');
        expect(screen.getByRole('status')).not.toHaveTextContent(/summariz/i);
        expect(screen.queryByText(/new chat/i)).toBeNull();
    });

    it('claims nothing about put-away or summarized context when the agent reports none', async () => {
        mockedApi.getContextUsage.mockResolvedValue({ available: true, ...usage({ at_risk: ['first question'] }) });
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(await screen.findByText('Live in the window')).toBeInTheDocument();
        expect(screen.queryByText('Put away')).toBeNull();
        expect(screen.queryByText('Summarized')).toBeNull();
        expect(screen.queryByText(/still searchable/)).toBeNull();
        expect(screen.getByRole('status')).not.toHaveTextContent(/summariz/i);
    });

    it('says the last turn is unknown when the newest answer has no record', async () => {
        mockedApi.getChatPerformance.mockResolvedValue({ ...perf(), latest_message_id: 9 });
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(await screen.findByText(/It was not measured/)).toBeInTheDocument();
        expect(screen.queryByText('In the model')).toBeNull();
    });

    it('keeps focus where the user put it when the parent re-renders', async () => {
        const { rerender } = render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        const thisChat = await screen.findByRole('button', { name: 'This chat' });
        thisChat.focus();
        rerender(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(document.activeElement).toBe(thisChat);
    });

    it('shows a small share as <1%, never 0%', async () => {
        const tiny = CATEGORIES.map((c) => (c.key === 'messages' ? { ...c, tokens: 200 } : c));
        mockedApi.getContextUsage.mockResolvedValue({ available: true, ...usage({ categories: tiny }) });
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        const row = (await within(panel()).findByText('Messages')).closest('.cp-row') as HTMLElement;
        expect(within(row).getByText('<1%')).toBeInTheDocument();
    });

    it('names a cloud provider the way people know it, not by its id', async () => {
        const cloud = perf();
        cloud.now = { ...cloud.now, model: 'fireworks.deepseek-v4p1-flash', provider: 'fireworks', device: null, backend: null };
        mockedApi.getChatPerformance.mockResolvedValue(cloud);
        render(<ContextPanel sessionId="s1" busy={false} onClose={() => {}} />);
        expect(await within(panel()).findByText('Cloud · Fireworks AI')).toBeInTheDocument();
    });

    it('closes on Escape', async () => {
        const onClose = vi.fn();
        render(<ContextPanel sessionId="s1" busy={false} onClose={onClose} />);
        await screen.findByText('18% used');
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(onClose).toHaveBeenCalled();
    });
});

describe('ContextMeter entry point', () => {
    it('opens the panel from the meter, and fetches nothing until then', async () => {
        render(<ContextMeter usage={usage()} sessionId="s1" />);
        expect(mockedApi.getChatPerformance).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /^Context \d+% full/ }));
        fireEvent.click(screen.getByRole('button', { name: /Details: context & performance/ }));
        expect(await screen.findByRole('dialog', { name: 'Context and performance' })).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.getChatPerformance).toHaveBeenCalledTimes(1));
    });
});
