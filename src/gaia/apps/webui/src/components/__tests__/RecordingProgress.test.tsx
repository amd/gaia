// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecordingProgress, STAGE_DONE, STAGE_RUNNING, STAGE_SKIPPED, timeLeft } from '../RecordingProgress';
import { translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');

const en = translator('en');
const TRANSCRIBE = 2;
const VOICES = 3;
const ANALYZE = 5;

afterEach(() => useChatStore.getState().resetStreaming());

function report(stage: number, state: number, percent?: number, etaSeconds?: number) {
    useChatStore.getState().setRecordingStage(stage, { state, percent, etaSeconds });
}

describe('RecordingProgress', () => {
    it('draws nothing until a recording is being worked on', () => {
        const { container } = render(<RecordingProgress />);
        expect(container).toBeEmptyDOMElement();
    });

    it('shows a real bar with percent and time left on the running stage', () => {
        report(TRANSCRIBE, STAGE_RUNNING, 42, 330);
        render(<RecordingProgress />);
        const bar = screen.getByRole('progressbar', { name: 'Transcribing' });
        expect(bar).toHaveAttribute('aria-valuenow', '42');
        expect(screen.getByText('42%')).toBeInTheDocument();
        expect(screen.getByText('About 6 min left')).toBeInTheDocument();
    });

    it('sweeps when a stage has no measure', () => {
        report(4, STAGE_RUNNING);
        render(<RecordingProgress />);
        expect(screen.getByRole('progressbar', { name: 'Preparing transcript' })).not.toHaveAttribute('aria-valuenow');
    });

    it('marks every earlier stage done once a later one starts, so a resumed run has no stuck rows', () => {
        report(TRANSCRIBE, STAGE_RUNNING, 90);
        report(VOICES, STAGE_RUNNING, 5);
        expect(useChatStore.getState().recordingStages?.[TRANSCRIBE].state).toBe(STAGE_DONE);
        expect(useChatStore.getState().recordingStages?.[1].state).toBe(STAGE_DONE);
    });

    it('says voices were skipped for one speaker', () => {
        report(VOICES, STAGE_SKIPPED);
        render(<RecordingProgress />);
        expect(screen.getByText('Skipped')).toBeInTheDocument();
    });

    it('leaves out download and analysis until the run reaches them', () => {
        report(TRANSCRIBE, STAGE_RUNNING, 1);
        const { rerender } = render(<RecordingProgress />);
        expect(screen.queryByText('Downloading')).not.toBeInTheDocument();
        expect(screen.queryByText('Analyzing')).not.toBeInTheDocument();
        report(ANALYZE, STAGE_RUNNING, 1);
        rerender(<RecordingProgress />);
        expect(screen.getByText('Analyzing')).toBeInTheDocument();
    });

    it('goes when the turn ends', () => {
        report(TRANSCRIBE, STAGE_RUNNING, 1);
        useChatStore.getState().resetStreaming();
        expect(useChatStore.getState().recordingStages).toBeNull();
    });
});

describe('timeLeft', () => {
    it('rounds up and never promises seconds', () => {
        expect(timeLeft(undefined, en)).toBe('');
        expect(timeLeft(10, en)).toBe('Under a minute left');
        expect(timeLeft(61, en)).toBe('About 2 min left');
    });
});

describe('Arabic', () => {
    it('has every stage', () => {
        const ar = translator('ar');
        for (const stage of ['download', 'decode', 'transcribe', 'voices', 'prepare', 'analyze'] as const) {
            expect(ar(`recording.stage.${stage}`)).toMatch(/[؀-ۿ]/);
        }
    });
});
