// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DownloadContents } from '../DownloadContents';
import { COLD_BIG, COMPONENTS, QWEN, runStatus } from './fixtures';

const P = () => ({ ...COLD_BIG(), components: COMPONENTS });
const row = (name: string) => screen.getByText(name).closest('li') as HTMLElement;

describe('Everything coming to this PC', () => {
    it('lists each component with its size and where it comes from', () => {
        render(<DownloadContents plan={P()} />);
        expect(row("AI engine")).toHaveTextContent('From github.com/lemonade-sdk/lemonade/releases');
        expect(row("AI engine")).toHaveTextContent('7 MB');
        expect(row('Graphics support')).toHaveTextContent('From github.com/ggml-org/llama.cpp');
        expect(row('Graphics support')).toHaveTextContent('141 MB');
        expect(row('GAIA assistant')).toHaveTextContent('From hub.amd-gaia.ai');
        // The app ships its packages: nothing comes from PyPI.
        expect(document.body).not.toHaveTextContent('pypi.org');
    });

    it('invents no size it cannot know', () => {
        const plan = P();
        render(<DownloadContents plan={{ ...plan, components: plan.components!.map((c) => (c.id === 'agent' ? { ...c, installed: false } : c)) }} />);
        expect(row('GAIA assistant')).toHaveTextContent('Size shown when it starts');
    });

    it('before a run says what is here and what is to come', () => {
        render(<DownloadContents plan={P()} />);
        expect(row('GAIA assistant')).toHaveTextContent('Already here');
        expect(row('GAIA assistant')).toHaveClass('is-done');
        expect(row("AI engine")).toHaveTextContent('Waiting');
        expect(row("AI engine")).toHaveClass('is-waiting');
    });

    it('during a run shows each state from the run', () => {
        const run = runStatus([], { components: { server: 'done', backend: 'active', agent: 'failed' } });
        render(<DownloadContents plan={P()} run={run} />);
        expect(row("AI engine")).toHaveTextContent('Ready');
        expect(row('Graphics support')).toHaveTextContent('In progress');
        expect(row('GAIA assistant')).toHaveTextContent('Failed');
        expect(row('GAIA assistant')).toHaveClass('is-failed');
    });

    it('a run that ended mid-step leaves that step unfinished, not still working', () => {
        const run = runStatus([], { state: 'failed', components: { server: 'done', agent: 'active' } });
        render(<DownloadContents plan={P()} run={run} />);
        expect(row("AI engine")).toHaveTextContent('Ready');
        expect(row('GAIA assistant')).not.toHaveTextContent('In progress');
        expect(row('GAIA assistant').querySelector('.spin')).toBeNull();
    });

    it('a failed row says why in plain words and offers a labelled Retry', () => {
        const onRetry = vi.fn();
        const run = runStatus([], { state: 'failed', error: 'Read timed out. (read timeout=10)', components: { server: 'done', agent: 'failed' } });
        render(<DownloadContents plan={P()} run={run} onRetry={onRetry} />);
        const agent = row('GAIA assistant');
        expect(agent).toHaveTextContent('GAIA assistant: the download timed out or the connection dropped.');
        expect(within(agent).getByText('Read timed out. (read timeout=10)').closest('details')).toBeInTheDocument();
        fireEvent.click(within(agent).getByRole('button', { name: 'Retry GAIA assistant' }));
        expect(onRetry).toHaveBeenCalledTimes(1);
        expect(within(row('AI engine')).queryByRole('button')).not.toBeInTheDocument();
    });

    it('a failure that is not the network still says which piece and how to see why', () => {
        const run = runStatus([], { state: 'failed', error: 'sha256 mismatch', components: { agent: 'failed' } });
        render(<DownloadContents plan={P()} run={run} onRetry={() => {}} />);
        expect(row('GAIA assistant')).toHaveTextContent('GAIA assistant could not be set up. Details say what happened; Retry runs setup again.');
    });

    it('offers no Retry while the run is still going', () => {
        const run = runStatus([], { state: 'running', components: { agent: 'failed' } });
        render(<DownloadContents plan={P()} run={run} onRetry={() => {}} />);
        expect(within(row('GAIA assistant')).queryByRole('button')).not.toBeInTheDocument();
    });

    it('lists the models that will download, with size and source', () => {
        const model = { ...QWEN, purpose: 'Answers your chats', source: 'huggingface.co/amd/Qwen3.6' };
        render(<DownloadContents plan={P()} models={[model, { ...model, id: 'other', name: 'Other', size_gb: null, source: null }]} />);
        expect(row(QWEN.name)).toHaveTextContent("Answers your messages and runs GAIA's tools");
        expect(row(QWEN.name)).toHaveTextContent('From huggingface.co/amd/Qwen3.6');
        expect(row(QWEN.name)).toHaveTextContent('23.3 GB');
        expect(row('Other')).toHaveTextContent('Size shown when it starts');
        expect(row('Other')).not.toHaveTextContent('From');
    });
});
