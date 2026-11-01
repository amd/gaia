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
import { COLD_BIG, EMBED, FLM, GEMMA, GPT_OSS, QWEN, plan } from './fixtures';

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

function Harness({ plan: p, onContinue = vi.fn(), cloud = false, initial }: { plan: SetupPlan; onContinue?: (ack: boolean) => void; cloud?: boolean; initial?: Selection }) {
    const [sel, setSel] = useState<Selection>(initial ?? initialSelection(p));
    const [keep, setKeep] = useState(false);
    return (
        <>
            <h2 id="models-title">Choose your models</h2>
            <ModelsStep
                plan={p}
                planError={null}
                onRetryPlan={vi.fn()}
                onUseRecommended={vi.fn()}
                selection={sel}
                onSelection={setSel}
                cloud={cloud}
                keepTrying={keep}
                onKeepTrying={setKeep}
                labelledBy="models-title"
                onBack={vi.fn()}
                onContinue={onContinue}
                busy={false}
                error={null}
            />
        </>
    );
}

const row = (name: string) => screen.getByRole('checkbox', { name: new RegExp(name) });
const summary = () => screen.getByRole('group', { name: 'Download total' });

describe('Choose your models', () => {
    it('lists every planned model with purpose, size left, recommended mark and the total', () => {
        render(<Harness plan={COLD_BIG()} />);
        const list = screen.getByRole('group', { name: 'Choose your models' });
        const rows = within(list).getAllByRole('checkbox');
        expect(rows).toHaveLength(3);
        expect(row('Qwen3.6-35B-A3B')).toBeChecked();
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/Qwen3\.6-35B-A3B.*Chat.*Download 23\.3 GB.*Recommended for this PC/);
        expect(row('embeddinggemma')).toHaveAccessibleName(/Documents.*Download 300 MB/);
        // Lemonade's memory is GiB and shown as GiB, beside the model's need, as the fit verdict compares them.
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/Needs about 27\.1 GB of memory; this PC has 96\.0 GiB for models\./);
        expect(within(summary()).getByText('29.6 GB')).toBeInTheDocument();
        expect(within(summary()).getByText('Free on C:')).toBeInTheDocument();
        expect(within(summary()).getByText('412.3 GB')).toBeInTheDocument();
        expect(screen.getByText('Unticking never deletes a model.')).toBeInTheDocument();
    });

    it('starts focus on the first model, and Continue sends no download until pressed', () => {
        const onContinue = vi.fn();
        render(<Harness plan={COLD_BIG()} onContinue={onContinue} />);
        expect(row('Qwen3.6-35B-A3B')).toHaveFocus();
        expect(onContinue).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(onContinue).toHaveBeenCalledWith(false);
    });

    it('says On this PC for a downloaded model and Partly downloaded for a partial one', () => {
        const done = { ...GEMMA, downloaded: true, size_gb: 0 };
        const half = { ...QWEN, partial: true, partial_gb: 22.1, size_gb: 1.2 };
        render(<Harness plan={plan([half, done, EMBED])} />);
        expect(row('Gemma-4-E4B-it')).toHaveAccessibleName(/On this PC/);
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/Partly downloaded, 1\.2 GB left/);
    });

    it('updates the total on every tick and lets the user tick a model setup does not plan', async () => {
        render(<Harness plan={COLD_BIG()} />);
        fireEvent.click(row('Qwen3.6-35B-A3B'));
        await waitFor(() => expect(within(summary()).getByText('6.3 GB')).toBeInTheDocument());
        fireEvent.click(screen.getByText('More models GAIA can download (1)'));
        fireEvent.click(row('gpt-oss 20B'));
        expect(row('gpt-oss 20B')).toBeChecked();
        // 5.97 + 0.3 + 12.1, eased in over 150 ms.
        await waitFor(() => expect(within(summary()).getByText('18.4 GB')).toBeInTheDocument());
        await waitFor(() => expect(within(summary()).getByText('393.9 GB')).toBeInTheDocument());
    });

    it("shows the vision caveat when Gemma is unticked, and doesn't block Continue", () => {
        render(<Harness plan={COLD_BIG()} />);
        fireEvent.click(row('Gemma-4-E4B-it'));
        const note = screen.getByRole('note');
        expect(note).toHaveTextContent('Without Gemma-4-E4B-it, GAIA can’t read images or screenshots. You can add it later in Settings.');
        expect(row('Gemma-4-E4B-it')).toHaveAttribute('aria-describedby', expect.stringContaining(note.id));
        expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    });

    it('warns with real numbers when a model may not fit memory', () => {
        const big = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16 };
        render(<Harness plan={plan([big, GEMMA, EMBED])} />);
        expect(screen.getByRole('note')).toHaveTextContent('Needs about 27.1 GB; this PC has 16.0 GiB. It may be very slow or fail to load.');
        expect(row('Qwen3.6-35B-A3B')).toBeChecked();
    });

    it("marks fit unknown and invents no number when the PC's memory couldn't be read", () => {
        const unknown = (e: typeof QWEN) => ({ ...e, fits: null, need_gb: null, capacity_gb: null, recommended: e.id === GEMMA.id || e.id === EMBED.id });
        render(<Harness plan={plan([unknown(QWEN), unknown(GEMMA), unknown(EMBED)], { memory_gb: null, capacity_gb: null })} />);
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/Fit unknown.*GAIA couldn’t read this PC’s memory/);
        expect(row('Qwen3.6-35B-A3B')).not.toHaveAccessibleName(/Recommended/);
        expect(row('Gemma-4-E4B-it')).toHaveAccessibleName(/Recommended for this PC/);
        expect(screen.queryByText(/Needs about/)).not.toBeInTheDocument();
    });

    it('says how much to free and the one model to drop when the disk is short, and Continue becomes Continue anyway', () => {
        const onContinue = vi.fn();
        render(<Harness plan={plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } })} onContinue={onContinue} />);
        expect(within(summary()).getByRole('note')).toHaveTextContent(
            'Needs 29.6 GB; only 11.2 GB free on D:. Free 18.4 GB or untick Qwen3.6-35B-A3B (saves 23.3 GB).',
        );
        fireEvent.click(screen.getByRole('button', { name: 'Continue anyway' }));
        expect(onContinue).toHaveBeenCalledWith(true);
        fireEvent.click(row('Qwen3.6-35B-A3B'));
        expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    });

    it('says free space is unknown and offers no after-value', () => {
        render(<Harness plan={plan([QWEN, GEMMA, EMBED], { disk: { path: null, free_gb: null, source: null } })} />);
        expect(within(summary()).getByText('Free space unknown')).toBeInTheDocument();
        expect(within(summary()).queryByText('After')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    });

    it('disables Continue only with no chat model at all, and says why next to it', () => {
        render(<Harness plan={COLD_BIG()} />);
        fireEvent.click(row('Qwen3.6-35B-A3B'));
        fireEvent.click(row('Gemma-4-E4B-it'));
        const cont = screen.getByRole('button', { name: 'Continue' });
        expect(cont).toBeDisabled();
        expect(cont).toHaveAccessibleDescription('Pick at least one chat model, or choose a cloud provider in step 2.');
    });

    it("lets an FLM-only user make their NPU model the chat model and continue with nothing to download", async () => {
        const onContinue = vi.fn();
        const p = plan([QWEN, GEMMA, { ...EMBED, downloaded: true, size_gb: 0 }, FLM]);
        render(<Harness plan={p} onContinue={onContinue} />);
        fireEvent.click(row('Qwen3.6-35B-A3B'));
        fireEvent.click(row('Gemma-4-E4B-it'));
        fireEvent.click(screen.getByText('Other models already in Lemonade (1)'));
        const npu = screen.getByRole('radio', { name: /Gemma 4 E2B \(NPU\).*NPU.*On this PC/ });
        expect(npu).toBeChecked();
        expect(screen.getByRole('combobox', { name: 'Default chat model' })).toHaveValue(FLM.id);
        await waitFor(() => expect(within(summary()).getByText('0.0 GB')).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(onContinue).toHaveBeenCalledWith(false);
    });

    it('offers one screen when everything is already on this PC, with Change models', () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        const onContinue = vi.fn();
        render(<Harness plan={plan([on(QWEN), on(GEMMA), on(EMBED)])} onContinue={onContinue} />);
        expect(screen.getByText('Everything is already on this PC.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Change models' }));
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/On this PC/);
    });

    it('lists only the embedding model for a cloud choice', () => {
        render(<Harness plan={COLD_BIG()} cloud />);
        expect(screen.getAllByRole('checkbox')).toHaveLength(2);
        expect(row('embeddinggemma')).toBeChecked();
        expect(screen.getByText('Cloud models need no download.')).toBeInTheDocument();
        expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    });

    it('offers keep-trying as an opt-in, off by default', () => {
        render(<Harness plan={COLD_BIG()} />);
        const keep = screen.getByRole('checkbox', { name: 'Keep trying if the connection drops (up to 2 minutes)' });
        expect(keep).not.toBeChecked();
    });

    it('reads in Arabic with numbers and model names left to right', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<Harness plan={COLD_BIG()} />);
        expect(document.documentElement.dir).toBe('rtl');
        const name = row('Qwen3.6-35B-A3B').closest('li')!.querySelector('bdi.mc-name')!;
        expect(name.textContent).toBe('Qwen3.6-35B-A3B');
        expect(stripIsolates(row('Qwen3.6-35B-A3B').closest('li')!.querySelector('.mc-status')!.textContent ?? '')).toContain('تنزيل 23.3 GB (Download 23.3 GB)');
        expect(screen.getAllByRole('button').filter((b) => stripIsolates(b.textContent ?? '').trim() === 'متابعة (Continue)')).toHaveLength(1);
    });
});

describe('Choose your models: GPT-OSS and other GAIA models', () => {
    it('keeps GAIA models setup does not plan behind a disclosure, unticked', () => {
        render(<Harness plan={COLD_BIG()} />);
        expect(screen.queryByRole('checkbox', { name: /gpt-oss 20B/ })).not.toBeVisible();
        fireEvent.click(screen.getByText('More models GAIA can download (1)'));
        expect(row('gpt-oss 20B')).not.toBeChecked();
        expect(GPT_OSS.planned).toBe(false);
    });
});
