// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ModelsStep } from '../../SetupSteps';
import { stripIsolates } from '../../../i18n/registry';
import { useChatStore } from '../../../stores/chatStore';
import type { SetupPlan } from '../../../types';
import { initialSelection, type Selection } from '../selection';
import { COLD_BIG, EMBED, FLM, GEMMA, QWEN, plan } from './fixtures';

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

interface HarnessProps {
    plan: SetupPlan | null;
    onContinue?: (ack: boolean) => void;
    onRetryPlan?: () => void;
    onUseRecommended?: () => void;
    initial?: Selection | null;
    planError?: string | null;
}

function Harness({ plan: p, onContinue = vi.fn(), onRetryPlan = vi.fn(), onUseRecommended = vi.fn(), initial, planError = null }: HarnessProps) {
    const [sel, setSel] = useState<Selection | null>(initial !== undefined ? initial : p && initialSelection(p));
    const [keep, setKeep] = useState(false);
    return (
        <ModelsStep
            plan={p}
            planError={planError}
            onRetryPlan={onRetryPlan}
            onUseRecommended={onUseRecommended}
            selection={sel}
            onSelection={setSel}
            cloud={false}
            keepTrying={keep}
            onKeepTrying={setKeep}
            onBack={vi.fn()}
            onContinue={onContinue}
            busy={false}
            error={null}
        />
    );
}

const chat = (name: RegExp) => within(screen.getByRole('region', { name: 'Chat' })).getByRole('radio', { name });
const summary = () => screen.getByRole('group', { name: 'Download total' });

describe('Choose your models', () => {
    it('shows every chat model with what it costs, and the total', () => {
        render(<Harness plan={COLD_BIG()} />);
        expect(chat(/Qwen3\.6-35B-A3B/)).toBeChecked();
        expect(chat(/Qwen3\.6-35B-A3B/)).toHaveAccessibleName(/Qwen3\.6-35B-A3B.*Recommended for this PC.*Download 23\.3 GB/);
        expect(screen.getByRole('checkbox', { name: /embeddinggemma/ }).closest('li')).toHaveTextContent('300 MB');
        expect(within(summary()).getByText('29.6 GB')).toBeInTheDocument();
        expect(within(summary()).getByText('Free on C:')).toBeInTheDocument();
        expect(within(summary()).getByText('412.3 GB')).toBeInTheDocument();
    });

    it('starts focus on the first model, and sends no download until Continue', () => {
        const onContinue = vi.fn();
        render(<Harness plan={COLD_BIG()} onContinue={onContinue} />);
        expect(chat(/Qwen3\.6-35B-A3B/)).toHaveFocus();
        expect(onContinue).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(onContinue).toHaveBeenCalledExactlyOnceWith(false);
    });

    it('says On this PC for a downloaded model and Partly downloaded for a partial one', () => {
        const done = { ...GEMMA, downloaded: true, size_gb: 0 };
        const half = { ...QWEN, partial: true, partial_gb: 22.1, size_gb: 1.2 };
        render(<Harness plan={plan([half, done, EMBED])} />);
        expect(chat(/Gemma-4-E4B-it/)).toHaveAccessibleName(/On this PC/);
        expect(chat(/Qwen3\.6-35B-A3B/)).toHaveAccessibleName(/Partly downloaded, 1\.2 GB left/);
    });

    it('updates the total as the choices change', async () => {
        render(<Harness plan={COLD_BIG()} />);
        // gpt-oss replaces Qwen: 12.1 + 5.97 + 0.3, eased in over 150 ms.
        fireEvent.click(chat(/gpt-oss 20B/));
        await waitFor(() => expect(within(summary()).getByText('18.4 GB')).toBeInTheDocument());
        await waitFor(() => expect(within(summary()).getByText('393.9 GB')).toBeInTheDocument());
    });

    it('says how much to free and the one model to drop when the disk is short, and Continue becomes Continue anyway', () => {
        const onContinue = vi.fn();
        render(<Harness plan={plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } })} onContinue={onContinue} />);
        expect(within(summary()).getByRole('note')).toHaveTextContent(
            'Needs 29.6 GB; only 11.2 GB free on D:. Free 18.4 GB or untick Qwen3.6-35B-A3B (saves 23.3 GB).',
        );
        fireEvent.click(screen.getByRole('button', { name: 'Continue anyway' }));
        expect(onContinue).toHaveBeenCalledExactlyOnceWith(true);
    });

    it('goes back to plain Continue once a smaller pick makes it fit', () => {
        render(<Harness plan={plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } })} />);
        fireEvent.click(chat(/Gemma-4-E4B-it/));
        expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    });

    it('says free space is unknown and offers no after-value', () => {
        render(<Harness plan={plan([QWEN, GEMMA, EMBED], { disk: { path: null, free_gb: null, source: null } })} />);
        expect(within(summary()).getByText('Free space unknown')).toBeInTheDocument();
        expect(within(summary()).queryByText('After')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    });

    it('disables Continue only with no chat model at all, and says why next to it', () => {
        const p = COLD_BIG();
        render(<Harness plan={p} initial={{ ...initialSelection(p), defaultChat: null }} />);
        const cont = screen.getByRole('button', { name: 'Continue' });
        expect(cont).toBeDisabled();
        expect(cont).toHaveAccessibleDescription('Pick at least one chat model, or choose a cloud provider in step 2.');
    });

    it('lets an FLM-only user make their NPU model the chat model', async () => {
        const onContinue = vi.fn();
        render(<Harness plan={plan([QWEN, { ...GEMMA, recommended: false }, { ...EMBED, downloaded: true, size_gb: 0 }, FLM])} onContinue={onContinue} />);
        const npu = chat(/Gemma 4 E2B \(NPU\)/);
        expect(npu).toHaveAccessibleName(/NPU.*On this PC/);
        fireEvent.click(npu);
        expect(npu).toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(onContinue).toHaveBeenCalledExactlyOnceWith(false);
    });

    it('offers one screen when everything is already on this PC, with Change models', () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        const onContinue = vi.fn();
        render(<Harness plan={plan([on(QWEN), on(GEMMA), on(EMBED)])} onContinue={onContinue} />);
        expect(screen.getByText('Everything is already on this PC.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Change models' }));
        expect(chat(/Qwen3\.6-35B-A3B/)).toHaveAccessibleName(/On this PC/);
    });

    it('goes back to the recommended set', () => {
        render(<Harness plan={COLD_BIG()} />);
        fireEvent.click(chat(/gpt-oss 20B/));
        expect(chat(/gpt-oss 20B/)).toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: 'Tick only the recommended models' }));
        expect(chat(/Qwen3\.6-35B-A3B/)).toBeChecked();
    });

    it('offers keep-trying as an opt-in, off by default', () => {
        render(<Harness plan={COLD_BIG()} />);
        expect(screen.getByRole('checkbox', { name: 'Keep trying if the connection drops (up to 2 minutes)' })).not.toBeChecked();
    });

    it("says the model list couldn't be read, with Try again and setup's own set", () => {
        const onRetryPlan = vi.fn();
        const onUseRecommended = vi.fn();
        render(<Harness plan={null} planError="500" onRetryPlan={onRetryPlan} onUseRecommended={onUseRecommended} />);
        expect(screen.getByRole('alert')).toHaveTextContent('Couldn’t read the model list from Lemonade: 500');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(onRetryPlan).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', { name: 'Use the recommended set' }));
        expect(onUseRecommended).toHaveBeenCalledTimes(1);
    });

    it('reads in Arabic with numbers and model names left to right', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<Harness plan={COLD_BIG()} />);
        expect(document.documentElement.dir).toBe('rtl');
        const row = screen.getAllByRole('radio')[0].closest('li')!;
        expect(row.querySelector('.mc-name bdi')!.textContent).toBe('Qwen3.6-35B-A3B');
        expect(stripIsolates(row.querySelector('.mc-status')!.textContent ?? '')).toContain('تنزيل 23.3 GB (Download 23.3 GB)');
        expect(screen.getAllByRole('button').filter((b) => stripIsolates(b.textContent ?? '').trim() === 'متابعة (Continue)')).toHaveLength(1);
    });
});

describe('Choose your models: memory numbers', () => {
    it('labels a need and the memory it is compared with the same way, everywhere', () => {
        const big = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16 };
        render(<Harness plan={plan([big, GEMMA, EMBED])} />);
        const text = document.body.textContent ?? '';
        expect(text).not.toMatch(/GiB/);
        const pairs = [...text.matchAll(/Needs about [\d.]+ (\w+)[^.]*?has [\d.]+ (\w+)/g)];
        expect(pairs.length).toBeGreaterThan(1);
        for (const m of pairs) expect(m[1], m[0]).toBe(m[2]);
    });

    it("marks fit unknown and invents no number when the PC's memory couldn't be read", () => {
        const unknown = (e: typeof QWEN) => ({ ...e, fits: null, need_gb: null, capacity_gb: null, recommended: e.id === GEMMA.id || e.id === EMBED.id });
        render(<Harness plan={plan([unknown(QWEN), unknown(GEMMA), unknown(EMBED)], { memory_gb: null, capacity_gb: null })} />);
        expect(chat(/Gemma-4-E4B-it/)).toBeInTheDocument();
        expect(screen.queryByText(/Needs about/)).not.toBeInTheDocument();
    });
});
