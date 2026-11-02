// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { SetupPlan } from '../../../types';
import { JobModels } from '../JobModels';
import { initialSelection, pendingDownloads, type Selection } from '../selection';
import { EMBED, GEMMA, GEMMA_CHAT, GPT_OSS, KOKORO, QWEN, QWEN_READS, SDXL, WHISPER, entry, plan } from './fixtures';

let current: Selection;

function Harness({ plan: p }: { plan: SetupPlan }) {
    const [sel, setSel] = useState<Selection>(initialSelection(p));
    current = sel;
    return <JobModels plan={p} selection={sel} onSelection={setSel} />;
}

const REAL = () => plan([QWEN_READS, GEMMA_CHAT, EMBED, GPT_OSS, WHISPER, KOKORO, SDXL]);
const job = (name: string) => within(screen.getByRole('region', { name }));
const chat = (name: RegExp) => job('Chat').getByRole('radio', { name });
const vision = (name: RegExp) => job('Reading images').getByRole('radio', { name });
const get = () => within(screen.getByRole('region', { name: "What you'll get" }));
const fact = (term: string) => get().getByText(term).closest('div') as HTMLElement;
const pending = (p: SetupPlan) => pendingDownloads(p.catalog!, current).map((e) => e.id);

describe('models by job: support labels', () => {
    it('marks verified models and explains the label', () => {
        render(<Harness plan={REAL()} />);
        expect(chat(/Qwen3\.6-35B-A3B/).closest('li')).toHaveTextContent('Verified');
        expect(chat(/Qwen3\.6-35B-A3B/).closest('li')).toHaveTextContent('Recommended for this PC');
        expect(screen.getByText("Verified models passed GAIA's evaluations.")).toBeInTheDocument();
    });

    it('keeps untested models apart, under a warning, and warns again once one is picked', () => {
        const mine = entry({ id: 'my-model', name: 'My own model', gaia_model: false, downloaded: true, size_gb: 0, support: 'untested' });
        render(<Harness plan={plan([QWEN_READS, GEMMA_CHAT, EMBED, mine])} />);
        expect(chat(/My own model/).closest('li')).toHaveTextContent('Untested');
        expect(chat(/My own model/).closest('li')).not.toHaveTextContent('Verified');
        expect(screen.getByText(/Untested models are used at your own risk/)).toBeInTheDocument();
        expect(screen.queryByText(/we haven't tested it/)).not.toBeInTheDocument();
        fireEvent.click(chat(/My own model/));
        expect(screen.getByRole('note')).toHaveTextContent("GAIA will use this model, but we haven't tested it.");
    });

    it('shows no support label on an optional extra', () => {
        render(<Harness plan={REAL()} />);
        expect(screen.getByRole('checkbox', { name: /Speech to text/ }).closest('li')).not.toHaveTextContent(/Verified|Untested/);
    });

    it("warns with real numbers when a chat model won't fit", () => {
        const big = { ...QWEN_READS, fits: false as const, need_gb: 27.1, capacity_gb: 16 };
        render(<Harness plan={plan([big, GEMMA_CHAT, EMBED])} />);
        expect(chat(/Qwen3\.6-35B-A3B/).closest('li')).toHaveTextContent('Needs about 27.1 GB; this PC has 16.0 GB. It may be very slow or fail to load.');
    });
});

describe('models by job: reading images', () => {
    it("needs nothing more when the chat model reads images, so Gemma isn't downloaded", () => {
        const p = REAL();
        render(<Harness plan={p} />);
        expect(screen.getByText(`Handled by ${QWEN.name}`)).toBeInTheDocument();
        expect(screen.getByText('Nothing more to download')).toBeInTheDocument();
        expect(job('Reading images').queryByRole('radio')).not.toBeInTheDocument();
        expect(pending(p)).not.toContain(GEMMA.id);
        expect(fact('Reading images')).toHaveTextContent(QWEN.name);
    });

    it('preselects Gemma, and recommends it, when the picked chat model has no vision', () => {
        const p = REAL();
        render(<Harness plan={p} />);
        fireEvent.click(chat(/gpt-oss 20B/));
        expect(screen.getByText("gpt-oss 20B can't read images, so we picked a model for that job.")).toBeInTheDocument();
        expect(vision(/Gemma-4-E4B-it/)).toBeChecked();
        expect(vision(/Gemma-4-E4B-it/).closest('li')).toHaveTextContent('Recommended for this PC');
        expect(pending(p)).toEqual(expect.arrayContaining([GPT_OSS.id, GEMMA.id]));
        expect(fact('Reading images')).toHaveTextContent(GEMMA.name);
    });

    it('Skip for now says what is lost, drops the download, and shows off in the summary', () => {
        const p = REAL();
        render(<Harness plan={p} />);
        fireEvent.click(chat(/gpt-oss 20B/));
        fireEvent.click(vision(/Skip for now/));
        expect(vision(/Skip for now/)).toBeChecked();
        expect(screen.getByRole('note')).toHaveTextContent('Reading images needs a vision model. GAIA will ask before downloading one.');
        expect(pending(p)).not.toContain(GEMMA.id);
        expect(fact('Reading images')).toHaveTextContent('Off for now');
    });

    it('opens the other vision models on request and can pick one', () => {
        const p = REAL();
        render(<Harness plan={p} />);
        fireEvent.click(screen.getByRole('button', { name: 'Choose a different vision model' }));
        fireEvent.click(vision(/Gemma-4-E4B-it/));
        expect(pending(p)).toContain(GEMMA.id);
        expect(fact('Reading images')).toHaveTextContent(GEMMA.name);
        expect(screen.getByRole('button', { name: 'Hide other vision models' })).toHaveAttribute('aria-expanded', 'true');
    });

    // Product bug: "Use the chat model" sends VISION_NONE, the same value as Skip for now.
    it('Use the chat model keeps the chat model reading images', () => {
        const p = REAL();
        render(<Harness plan={p} />);
        fireEvent.click(screen.getByRole('button', { name: 'Choose a different vision model' }));
        fireEvent.click(vision(/Gemma-4-E4B-it/));
        fireEvent.click(vision(/Use the chat model/));
        expect(fact('Reading images')).toHaveTextContent(QWEN.name);
    });

    // Product bug: the chat model is in use, yet no radio in the group is checked.
    it('shows Use the chat model as the choice while it is', () => {
        render(<Harness plan={REAL()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Choose a different vision model' }));
        expect(vision(/Use the chat model/)).toBeChecked();
    });
});

describe('models by job: documents and extras', () => {
    it('warns what stops working without the embedding model', () => {
        render(<Harness plan={REAL()} />);
        const docs = screen.getByRole('checkbox', { name: /embeddinggemma/ });
        expect(docs).toBeChecked();
        fireEvent.click(docs);
        expect(screen.getByRole('note')).toHaveTextContent('Without it, document search and memory stay off until you add it in Settings.');
        expect(fact('Documents and memory')).toHaveTextContent('Off for now');
    });

    it('leaves the extras off, and lists each one ticked in the summary', () => {
        render(<Harness plan={REAL()} />);
        const speech = screen.getByRole('checkbox', { name: /Speech to text/ });
        const voice = screen.getByRole('checkbox', { name: /Text to speech/ });
        expect(speech).not.toBeChecked();
        expect(voice).not.toBeChecked();
        expect(fact('Voice')).toHaveTextContent('Off for now');
        fireEvent.click(speech);
        fireEvent.click(voice);
        expect(fact('Voice')).toHaveTextContent('Speech to text, Text to speech');
    });

    it('shows a size for each extra', () => {
        render(<Harness plan={REAL()} />);
        expect(screen.getByRole('checkbox', { name: /Speech to text/ }).closest('li')).toHaveTextContent('1.6 GB');
    });

    it('greys out image creation under a compact chat model, says why, and unticks it', () => {
        const p = REAL();
        render(<Harness plan={p} />);
        const images = screen.getByRole('checkbox', { name: /Creating images/ });
        fireEvent.click(images);
        expect(images).toBeChecked();
        expect(fact('Creating images')).toHaveTextContent('On');
        fireEvent.click(chat(/Gemma-4-E4B-it/));
        expect(images).toBeDisabled();
        expect(images).not.toBeChecked();
        expect(images.closest('li')).toHaveTextContent("Gemma-4-E4B-it doesn't have this feature yet. Choose a larger chat model to use it.");
        expect(fact('Creating images')).toHaveTextContent('Not available with this chat model');
        expect(pending(p)).not.toContain(SDXL.id);
    });

    it('offers image creation again once a full chat model is picked', () => {
        render(<Harness plan={REAL()} />);
        fireEvent.click(chat(/Gemma-4-E4B-it/));
        fireEvent.click(chat(/Qwen3\.6-35B-A3B/));
        expect(screen.getByRole('checkbox', { name: /Creating images/ })).toBeEnabled();
    });

    // Product bug: a compact chat model with the image model on disk keeps it ticked, so the summary says On.
    it('says image creation is unavailable when a compact model starts with the image model on disk', () => {
        const compact = { ...GEMMA_CHAT, planned: true, selected: true, recommended: true };
        render(<Harness plan={plan([compact, EMBED, { ...SDXL, downloaded: true, size_gb: 0 }])} />);
        expect(fact('Creating images')).toHaveTextContent('Not available with this chat model');
    });
});

describe("models by job: what you'll get", () => {
    it('names the model for each job', () => {
        render(<Harness plan={plan([QWEN, GEMMA, EMBED])} />);
        expect(fact('Chat')).toHaveTextContent(QWEN.name);
        expect(fact('Reading images')).toHaveTextContent(GEMMA.name);
        expect(fact('Documents and memory')).toHaveTextContent(EMBED.name);
    });
});
