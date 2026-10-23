// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LearningPanel, stampToDate } from '../LearningPanel';
import { useChatStore } from '../../stores/chatStore';
import * as learn from '../../services/learningApi';
import type { Adapter, LearnedItem, LearningStatus } from '../../services/learningApi';

vi.mock('../../services/learningApi');

const score = (learned: number, general: number) => ({
    label: 'x',
    learned_passed: learned,
    learned_total: 4,
    regression_passed: general,
    regression_total: 12,
    results: [
        { question: 'What port does the cache use?', kind: 'learned' as const, passed: learned > 0, reply: 'port 7443', opportunity: 'c' },
    ],
});

const active: Adapter = {
    id: '20261023-021453',
    base_model: 'Gemma-4-E4B-it-GGUF',
    status: 'active',
    created_at: '2026-10-23T09:14:53Z',
    decision: 'answered 4/4 learned probes',
    scores: { base: score(0, 11), candidate: score(4, 11) },
    items: [{ id: 'concept:1', kind: 'concept', title: 'Bluefleet is staging' }],
};
const older: Adapter = { ...active, id: '20261022-020000', status: 'archived', scores: { base: score(0, 11), candidate: score(2, 11) } };

const status: LearningStatus = {
    config: { enabled: true, window_start: '01:00', window_end: '06:00', min_idle_minutes: 30, require_ac_power: true, base_model: null, keep_adapters: 7 },
    base_model: 'Gemma-4-E4B-it-GGUF',
    base_model_trainable: true,
    trainable_models: ['Gemma-4-E4B-it-GGUF'],
    toolchain: { ready: true, torch: '2.11.0+rocm7.14.0', gpu: 'AMD Radeon 8060S', problems: [] },
    active_adapter: active,
    latest_run: {
        id: '20261023-021453', status: 'running', phase: 'training', trigger: 'scheduled',
        started_at: '', finished_at: null, progress: { step: 50, total: 200, loss: 0.41 },
        opportunities: [{ id: 'workflow:3', kind: 'workflow', title: 'Go straight to query_specific_file', error: null }],
    },
};

const items: LearnedItem[] = [
    { id: 'concept:1', kind: 'concept', title: 'Bluefleet is staging', evidence: ['Bluefleet is the staging cluster'], examples: 10, probes: 2, first_learned: '', updated_at: '' },
];

beforeEach(() => {
    vi.mocked(learn.getStatus).mockResolvedValue(status);
    vi.mocked(learn.listAdapters).mockResolvedValue([active, older]);
    vi.mocked(learn.listItems).mockResolvedValue(items);
    vi.mocked(learn.activateAdapter).mockResolvedValue(older);
    vi.mocked(learn.forgetItem).mockResolvedValue({ removed: 'concept:1', adapter_retired: null });
    vi.mocked(learn.updateConfig).mockResolvedValue(status.config);
    vi.mocked(learn.stopRun).mockResolvedValue({ stopping: true });
    useChatStore.setState({ showLearning: true });
});

describe('LearningPanel', () => {
    it('shows the running cycle, its progress and what it found', async () => {
        render(<LearningPanel />);
        expect(await screen.findByText('Running')).toBeInTheDocument();
        expect(screen.getByText(/step 50\/200, loss 0\.410/)).toBeInTheDocument();
        expect(screen.getByText(/Faster workflow: Go straight to/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Stop/ }));
        await waitFor(() => expect(learn.stopRun).toHaveBeenCalled());
    });

    it('lists adapters with their scores and rolls back to an older one', async () => {
        render(<LearningPanel />);
        const rollBack = await screen.findByRole('button', { name: /Roll back to this/ });
        expect(screen.getAllByText(/Learned 4\/4/).length).toBeGreaterThan(0);
        fireEvent.click(rollBack);
        await waitFor(() => expect(learn.activateAdapter).toHaveBeenCalledWith('20261022-020000'));
    });

    it('shows base vs adapter answers when an adapter is expanded', async () => {
        render(<LearningPanel />);
        fireEvent.click(await screen.findByRole('button', { name: 'Details for 20261023-021453' }));
        const table = screen.getByRole('table');
        expect(within(table).getByText('What port does the cache use?')).toBeInTheDocument();
        expect(within(table).getAllByLabelText('passed')).toHaveLength(1);
        expect(within(table).getAllByLabelText('failed')).toHaveLength(1);
    });

    it('forgets a learned item', async () => {
        render(<LearningPanel />);
        fireEvent.click(await screen.findByRole('button', { name: 'Forget' }));
        await waitFor(() => expect(learn.forgetItem).toHaveBeenCalledWith('concept:1'));
    });

    it('turns nightly learning off from the switch', async () => {
        render(<LearningPanel />);
        fireEvent.click(await screen.findByRole('switch', { name: 'Nightly learning' }));
        await waitFor(() => expect(learn.updateConfig).toHaveBeenCalledWith({ enabled: false }));
    });

    it('says when the toolchain needs setup and disables Learn now', async () => {
        vi.mocked(learn.getStatus).mockResolvedValue({ ...status, toolchain: null, latest_run: null });
        render(<LearningPanel />);
        expect(await screen.findByText(/gaia learn setup/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Learn now/ })).toBeDisabled();
    });

    it('closes on Escape', async () => {
        render(<LearningPanel />);
        await screen.findByText('Running');
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(useChatStore.getState().showLearning).toBe(false);
    });
});

describe('stampToDate', () => {
    it('reads the local-time run id', () => {
        expect(stampToDate('20261023-021453')).toMatch(/Oct/);
        expect(stampToDate('not-a-stamp')).toBe('not-a-stamp');
    });
});
