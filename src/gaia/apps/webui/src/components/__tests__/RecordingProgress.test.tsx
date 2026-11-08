// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecordingProgress, STAGE_DONE, STAGE_RUNNING, STAGE_SKIPPED, foundSoFar, timeLeft } from '../RecordingProgress';
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

function section(n: number, total: number, items: [string, string, number][], summary = `Part ${n}.`) {
    return {
        section: n, sections: total, start: `0${n - 1}:00`, end: `0${n}:10`, summary,
        items: items.map(([title, start, seconds]) => ({ title, start, seconds })),
    };
}

describe('found so far', () => {
    it('lists each section read so far, with its times, summary and items', () => {
        report(ANALYZE, STAGE_RUNNING, 50);
        useChatStore.getState().addRecordingSection(section(1, 3, [['Lunges', '00:20', 20]]));
        render(<RecordingProgress />);

        const found = screen.getByRole('region', { name: 'Found so far' });
        expect(found).toHaveTextContent('1 of 3 sections read');
        expect(found).toHaveTextContent('00:00–01:10');
        expect(found).toHaveTextContent('Part 1.');
        expect(found).toHaveTextContent('00:20Lunges');
    });

    it('shows an item found again in the overlap once, in recording order', () => {
        const sections = {
            2: section(2, 2, [['lunges', '01:05', 65], ['Plank', '01:40', 100]]),
            1: section(1, 2, [['Lunges', '01:00', 60]]),
        };

        const shown = foundSoFar(sections);

        expect(shown.map((s) => s.section)).toEqual([1, 2]);
        expect(shown[1].items.map((i) => i.title)).toEqual(['Plank']);
    });

    it('keeps the same name much later on as a second item', () => {
        const shown = foundSoFar({
            1: section(1, 2, [['Lunges', '00:10', 10]]),
            2: section(2, 2, [['Lunges', '09:00', 540]]),
        });
        expect(shown[1].items).toHaveLength(1);
    });

    it('goes with the stages when the turn ends', () => {
        useChatStore.getState().addRecordingSection(section(1, 1, []));
        useChatStore.getState().resetStreaming();
        expect(useChatStore.getState().recordingSections).toBeNull();
    });

    it('reads in Arabic', () => {
        const ar = translator('ar');
        expect(ar('recording.found.title')).toMatch(/[؀-ۿ]/);
        expect(ar('recording.found.count', { done: 1, total: 3 })).toMatch(/[؀-ۿ]/);
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
