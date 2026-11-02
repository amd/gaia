// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { SetupTest } from '../../../types';
import { TestStep } from '../TestStep';
import { EMBED, GEMMA, QWEN, WHISPER, runStatus } from './fixtures';

const row = (job: SetupTest['job'], state: SetupTest['state'], over: Partial<SetupTest> = {}): SetupTest => ({
    job, subject: null, state, error: null, kind: null, seconds: null, ...over,
});

const NAMES = { [QWEN.id]: QWEN.name, [GEMMA.id]: GEMMA.name, [WHISPER.id]: WHISPER.name };

function show(tests: SetupTest[], state: 'verifying' | 'failed' | 'cancelled' = 'verifying', expected: SetupTest['job'][] = []) {
    const handlers = { onRetry: vi.fn(), onChooseAnother: vi.fn(), onDownloadAgain: vi.fn() };
    render(
        <TestStep
            run={runStatus([], { state, tests })}
            expected={expected}
            names={NAMES}
            catalog={[QWEN, GEMMA, EMBED, WHISPER]}
            busy={false}
            {...handlers}
        />,
    );
    return handlers;
}

const rowOf = (name: RegExp) => screen.getByText(name).closest('li') as HTMLElement;

describe('Test step', () => {
    it('shows one row per test with its model, state and time', () => {
        show([
            row('server', 'passed', { seconds: 0.4 }),
            row('chat', 'passed', { subject: QWEN.id, seconds: 12.6 }),
            row('vision', 'running', { subject: GEMMA.id }),
        ], 'verifying', ['server', 'chat', 'vision', 'embedding', 'agent']);
        expect(rowOf(/Model server answers/)).toHaveTextContent('Passed, 0 s');
        expect(rowOf(/Chat: loads and answers/)).toHaveTextContent(QWEN.name);
        expect(rowOf(/Chat: loads and answers/)).toHaveTextContent('Passed, 13 s');
        expect(rowOf(/Reading images/)).toHaveTextContent(GEMMA.name);
        expect(rowOf(/Reading images/)).toHaveTextContent('Running');
    });

    it('lists the tests still to come, and counts them in the total', () => {
        show([row('server', 'passed')], 'verifying', ['server', 'chat', 'agent']);
        expect(rowOf(/Chat: loads and answers/)).toHaveTextContent('Waiting');
        expect(rowOf(/A real chat with GAIA/)).toHaveTextContent('Waiting');
        expect(screen.getByText('Tests done: 1 of 3')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Testing everything' })).toHaveAttribute('value', '33');
    });

    it('says a model is not on this PC rather than calling a skipped test a failure', () => {
        show([row('speech', 'skipped', { subject: WHISPER.id })]);
        expect(rowOf(/Speech to text/)).toHaveTextContent('Not on this PC');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('offers nothing while the tests run', () => {
        show([row('chat', 'running', { subject: QWEN.id })]);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it("shows a failed test's error on its row", () => {
        show([row('chat', 'failed', { subject: QWEN.id, kind: 'stuck', error: 'No answer in 120 seconds.' })], 'failed');
        expect(rowOf(/Chat: loads and answers/)).toHaveTextContent('Failed');
        expect(rowOf(/Chat: loads and answers/)).toHaveTextContent('No answer in 120 seconds.');
    });

    it('for a memory failure says to free memory, with no re-download', () => {
        const { onRetry, onChooseAnother } = show([row('chat', 'failed', { subject: QWEN.id, kind: 'memory', error: 'out of memory' })], 'failed');
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent("This PC doesn't have enough free memory right now.");
        expect(within(alert).queryByRole('button', { name: /Download .* again/ })).not.toBeInTheDocument();
        fireEvent.click(within(alert).getByRole('button', { name: 'Test again' }));
        expect(onRetry).toHaveBeenCalledTimes(1);
        fireEvent.click(within(alert).getByRole('button', { name: 'Choose another model' }));
        expect(onChooseAnother).toHaveBeenCalledTimes(1);
    });

    it('for a broken model offers to download that model again', () => {
        const { onDownloadAgain } = show([row('chat', 'failed', { subject: QWEN.id, kind: 'broken', error: 'bad file' })], 'failed');
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent("Something didn't work, so GAIA isn't ready yet. Your downloads are safe.");
        fireEvent.click(within(alert).getByRole('button', { name: `Download ${QWEN.name} again` }));
        expect(onDownloadAgain).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Delete and download again' }));
        expect(onDownloadAgain).toHaveBeenCalledExactlyOnceWith(QWEN.id);
    });

    it('offers no re-download for a broken test with no model behind it', () => {
        show([row('agent', 'failed', { kind: 'broken', error: 'The agent did not answer.' })], 'failed');
        expect(screen.queryByRole('button', { name: /Download .* again/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Test again' })).toBeInTheDocument();
    });

    it("doesn't block on an optional extra that failed while the run goes on", () => {
        show([
            row('chat', 'passed', { subject: QWEN.id }),
            row('speech', 'failed', { subject: WHISPER.id, kind: 'broken', error: 'no audio' }),
        ]);
        expect(rowOf(/Speech to text/)).toHaveTextContent('Failed');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('gives no advice about an optional failure when something else stopped the run', () => {
        show([
            row('speech', 'failed', { subject: WHISPER.id, kind: 'memory', error: 'out of memory' }),
            row('voice', 'failed', { subject: WHISPER.id, kind: 'broken', error: 'no audio' }),
            row('chat', 'failed', { subject: QWEN.id, kind: 'stuck', error: 'timed out' }),
        ], 'failed');
        expect(screen.getByRole('alert')).toHaveTextContent("Something didn't work");
        expect(screen.queryByRole('button', { name: /Download .* again/ })).not.toBeInTheDocument();
    });

    // Product bug: a model that chats and reads images fails both tests and gets two identical buttons.
    it('offers one re-download when one model fails two tests', () => {
        show([
            row('chat', 'failed', { subject: QWEN.id, kind: 'broken', error: 'bad file' }),
            row('vision', 'failed', { subject: QWEN.id, kind: 'broken', error: 'bad file' }),
        ], 'failed');
        expect(screen.getAllByRole('button', { name: `Download ${QWEN.name} again` })).toHaveLength(1);
    });
});
