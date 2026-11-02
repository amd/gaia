// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DownloadContents } from '../DownloadContents';
import { COLD_BIG, COMPONENTS, QWEN, runStatus } from './fixtures';

const P = () => ({ ...COLD_BIG(), components: COMPONENTS });
const row = (name: string) => screen.getByText(name).closest('li') as HTMLElement;

describe('Everything coming to this PC', () => {
    it('lists each component with its size and where it comes from', () => {
        render(<DownloadContents plan={P()} />);
        expect(row("GAIA's model server")).toHaveTextContent('From github.com/lemonade-sdk/lemonade/releases');
        expect(row("GAIA's model server")).toHaveTextContent('7 MB');
        expect(row('Model runtime')).toHaveTextContent('From github.com/ggml-org/llama.cpp');
        expect(row('Model runtime')).toHaveTextContent('141 MB');
        expect(row('Python packages')).toHaveTextContent('From pypi.org');
        expect(row('GAIA agent')).toHaveTextContent('From hub.amd-gaia.ai');
    });

    it('invents no size it cannot know', () => {
        render(<DownloadContents plan={P()} />);
        expect(row('Python packages')).toHaveTextContent('Size shown when it starts');
    });

    it('before a run says what is here and what is to come', () => {
        render(<DownloadContents plan={P()} />);
        expect(row('GAIA agent')).toHaveTextContent('Already here');
        expect(row('GAIA agent')).toHaveClass('is-done');
        expect(row("GAIA's model server")).toHaveTextContent('Waiting');
        expect(row("GAIA's model server")).toHaveClass('is-waiting');
    });

    it('during a run shows each state from the run', () => {
        const run = runStatus([], { components: { server: 'done', backend: 'active', packages: 'failed' } });
        render(<DownloadContents plan={P()} run={run} />);
        expect(row("GAIA's model server")).toHaveTextContent('Ready');
        expect(row('Model runtime')).toHaveTextContent('In progress');
        expect(row('Python packages')).toHaveTextContent('Failed');
        expect(row('Python packages')).toHaveClass('is-failed');
        expect(row('GAIA agent')).toHaveTextContent('Already here');
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
