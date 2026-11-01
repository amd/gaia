// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useChatStore } from '../../../stores/chatStore';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SetupRunStatus } from '../../../types';
import { DownloadProgress } from '../DownloadProgress';
import { EMBED, GEMMA, QWEN, progress, runStatus } from './fixtures';

const NAMES = { [QWEN.id]: 'Qwen3.6-35B-A3B', [GEMMA.id]: 'Gemma-4-E4B-it', [EMBED.id]: 'embeddinggemma-300m' };

afterEach(() => {
    vi.useRealTimers();
});

function rowOf(name: string): HTMLElement {
    return screen.getByText(name, { selector: '.dl-name bdi' }).closest('li')!;
}

describe('DownloadProgress', () => {
    it('shows each state as a word, with the embedder first, then chat, then the rest', () => {
        render(<DownloadProgress status={runStatus([
            progress({ id: GEMMA.id, roles: ['vision'], state: 'queued', bytes_total: 5.97e9 }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 7.6e9, bytes_total: 23.3e9 }),
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
        ])} names={NAMES} />);
        const names = [...document.querySelectorAll('.dl-name')].map((n) => n.textContent);
        expect(names).toEqual(['embeddinggemma-300m', 'Qwen3.6-35B-A3B', 'Gemma-4-E4B-it']);
        expect(within(rowOf('embeddinggemma-300m')).getByText('Ready')).toBeInTheDocument();
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText('Downloading')).toBeInTheDocument();
        expect(within(rowOf('Gemma-4-E4B-it')).getByText('Queued')).toBeInTheDocument();
    });

    it('labels verifying, failed and cancelled with words too', () => {
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'verifying', bytes_done: 1e9, bytes_total: 1e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', error: 'boom', error_kind: 'lemonade' }),
            progress({ id: EMBED.id, roles: ['embedding'], state: 'cancelled', bytes_done: 0.1e9, bytes_total: 0.3e9 }),
        ], { state: 'failed' })} names={NAMES} onRetry={vi.fn()} onResume={vi.fn()} />);
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText('Verifying')).toBeInTheDocument();
        expect(within(rowOf('Gemma-4-E4B-it')).getByText('Failed')).toBeInTheDocument();
        expect(within(rowOf('embeddinggemma-300m')).getByText('Cancelled')).toBeInTheDocument();
        expect(within(rowOf('embeddinggemma-300m')).getByText('100 MB kept. Resume continues from there.')).toBeInTheDocument();
    });

    it('says a downloaded row waits for the load check while others download, and animates only during the check', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const rows = [
            progress({ id: EMBED.id, roles: ['embedding'], state: 'verifying', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 }),
        ];
        const { rerender } = render(<DownloadProgress status={runStatus(rows)} names={NAMES} />);
        const row = rowOf('embeddinggemma-300m');
        expect(within(row).getByText('Downloaded. GAIA checks that it loads once the others finish.')).toBeInTheDocument();
        expect(row).not.toHaveClass('is-live');
        const checking = runStatus([rows[0]], { state: 'verifying' });
        rerender(<DownloadProgress status={checking} names={NAMES} />);
        expect(within(rowOf('embeddinggemma-300m')).getByText('Checking that it loads…')).toBeInTheDocument();
        expect(rowOf('embeddinggemma-300m')).toHaveClass('is-live');
        vi.setSystemTime(12_000);
        rerender(<DownloadProgress status={{ ...checking }} names={NAMES} />);
        expect(within(rowOf('embeddinggemma-300m')).getByText('Checking that it loads… 12 s')).toBeInTheDocument();
    });

    it('holds Retry on a failed row while the run still downloads the others', () => {
        render(<DownloadProgress
            status={runStatus([
                progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', error_kind: 'network', error: 'x' }),
                progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 }),
            ])}
            names={NAMES}
            onRetry={vi.fn()}
            retryLater
        />);
        const row = rowOf('Gemma-4-E4B-it');
        expect(within(row).getByText('Retry becomes available once the other downloads finish.')).toBeInTheDocument();
        expect(within(row).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    });

    it("leaves a failed row's bytes out of the time left", () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const at = (done: number): SetupRunStatus => runStatus([
            progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', bytes_done: 2e9, bytes_total: 50e9, error_kind: 'network' }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: done, bytes_total: 1e9 + 30 * 20e6 }),
        ]);
        const { rerender } = render(<DownloadProgress status={at(1e9)} names={NAMES} />);
        for (let s = 1; s <= 11; s++) {
            vi.setSystemTime(s * 1000);
            rerender(<DownloadProgress status={at(1e9 + s * 20e6)} names={NAMES} />);
        }
        // 19 x 20 MB left at 20 MB/s: well under a minute, not the failed row's 48 GB.
        expect(screen.getByText(/about \d+ sec left/)).toBeInTheDocument();
    });

    it('keeps sizes and speeds whole inside an Arabic line', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 10.4e9, bytes_total: 23.3e9 }),
        ])} names={NAMES} />);
        const detail = rowOf('Qwen3.6-35B-A3B').querySelector('.dl-detail')!.textContent ?? '';
        // LRI ... PDI around the numbers, so the right-to-left run can't reorder them.
        expect(detail).toContain('\u206610.4 GB / 23.3 GB\u2069');
        act(() => useChatStore.getState().setLocale('en'));
    });

    it('gives each bar a spoken value, not just a number', () => {
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 7.6e9, bytes_total: 23.3e9 }),
        ])} names={NAMES} />);
        const bar = screen.getByRole('progressbar', { name: 'Qwen3.6-35B-A3B, Downloading' });
        expect(bar).toHaveAttribute('aria-valuetext', '32 percent, 7.6 GB of 23.3 GB');
        expect(screen.getByRole('progressbar', { name: 'Overall download' })).toHaveAttribute('aria-valuenow', '32');
    });

    it('says Estimating… until 10 s of data, then shows speed and ETA', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const at = (done: number): SetupRunStatus => runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: done, bytes_total: 23.3e9 })]);
        const { rerender } = render(<DownloadProgress status={at(1e9)} names={NAMES} />);
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText(/Estimating…/)).toBeInTheDocument();
        for (let s = 1; s <= 11; s++) {
            vi.setSystemTime(s * 1000);
            rerender(<DownloadProgress status={at(1e9 + s * 18e6)} names={NAMES} />);
        }
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText(/18 MB\/s · about \d+ min/)).toBeInTheDocument();
    });

    it('starts speed and time left fresh after a cancel and resume', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const at = (state: 'downloading' | 'cancelled', done: number): SetupRunStatus =>
            runStatus([progress({ id: QWEN.id, state, bytes_done: done, bytes_total: 23.3e9 })]);
        const { rerender } = render(<DownloadProgress status={at('downloading', 1e9)} names={NAMES} />);
        for (let s = 1; s <= 11; s++) {
            vi.setSystemTime(s * 1000);
            rerender(<DownloadProgress status={at('downloading', 1e9 + s * 18e6)} names={NAMES} />);
        }
        expect(screen.getByText(/18 MB\/s · about/, { selector: '.dl-detail bdi' })).toBeInTheDocument();
        vi.setSystemTime(60_000);
        rerender(<DownloadProgress status={at('cancelled', 1.2e9)} names={NAMES} />);
        vi.setSystemTime(300_000);
        rerender(<DownloadProgress status={at('downloading', 1.3e9)} names={NAMES} />);
        // No speed carried over from before the pause: it estimates again.
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText(/Estimating…/)).toBeInTheDocument();
        expect(screen.getByText(/Overall .* Estimating…/)).toBeInTheDocument();
    });

    it('says when no data has come for 15 s, and offers Retry after a minute', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const onRestart = vi.fn();
        const st = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
        const { rerender } = render(<DownloadProgress status={st} names={NAMES} onRestart={onRestart} />);
        vi.setSystemTime(16_000);
        rerender(<DownloadProgress status={{ ...st, models: [...st.models!] }} names={NAMES} onRestart={onRestart} />);
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText('No data for 15 seconds. Still trying…')).toBeInTheDocument();
        vi.setSystemTime(61_000);
        rerender(<DownloadProgress status={{ ...st, models: [...st.models!] }} names={NAMES} onRestart={onRestart} />);
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText('The download seems stuck. Retry?')).toBeInTheDocument();
        fireEvent.click(within(rowOf('Qwen3.6-35B-A3B')).getByRole('button', { name: 'Retry' }));
        expect(onRestart).toHaveBeenCalled();
    });

    it('counts down the opted-in keep-trying wait instead of failing', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(1_000_000);
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 3e9, bytes_total: 23.3e9, retrying_until: 1_000 + 90 }),
        ])} names={NAMES} />);
        expect(within(rowOf('Qwen3.6-35B-A3B')).getByText('The connection dropped. Trying again for 90 more seconds…')).toBeInTheDocument();
        expect(within(rowOf('Qwen3.6-35B-A3B')).queryByText('Failed')).not.toBeInTheDocument();
    });

    it.each([
        ['network', 'The connection dropped while downloading Qwen3.6-35B-A3B. Press Retry to continue from where it stopped.'],
        ['disk_full', 'Ran out of disk space after 14.2 GB of Qwen3.6-35B-A3B. Free 7.1 GB and press Retry. Nothing was lost; the download continues from where it stopped.'],
        ['lemonade', 'Lemonade couldn’t download Qwen3.6-35B-A3B. Details has its message.'],
    ] as const)('words a %s failure with what to do, and Retry works', (kind, text) => {
        const onRetry = vi.fn();
        render(<DownloadProgress
            status={runStatus([progress({ id: QWEN.id, state: 'failed', bytes_done: 14.2e9, bytes_total: 23.3e9, error: 'Lemonade said: x', error_kind: kind })], { state: 'failed' })}
            names={NAMES}
            freeGb={2}
            onRetry={onRetry}
        />);
        const row = rowOf('Qwen3.6-35B-A3B');
        expect(within(row).getByText(text)).toBeInTheDocument();
        const retry = within(row).getByRole('button', { name: 'Retry' });
        expect(retry).toHaveAccessibleDescription(text);
        fireEvent.click(retry);
        expect(onRetry).toHaveBeenCalledWith(QWEN.id, kind);
        expect(within(row).getByText('Details')).toBeInTheDocument();
    });

    it('shows how many failed in the overall line, and offers Skip this model', () => {
        const onSkip = vi.fn();
        render(<DownloadProgress
            status={runStatus([
                progress({ id: QWEN.id, state: 'failed', error_kind: 'network', error: 'x' }),
                progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 1e9 }),
            ], { state: 'failed' })}
            names={NAMES}
            onRetry={vi.fn()}
            onSkip={onSkip}
        />);
        expect(screen.getByText(/Failed: 1/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Skip this model' }));
        expect(onSkip).toHaveBeenCalledWith(QWEN.id);
    });

    it('asks before cancelling once more than 1 GB is in, and keeps what finished', () => {
        const onCancel = vi.fn();
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 2e9, bytes_total: 23.3e9 }),
        ])} names={NAMES} onCancel={onCancel} />);
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        const sheet = screen.getByRole('alertdialog', { name: 'Stop downloading?' });
        expect(sheet).toHaveTextContent('Finished models stay. Partial downloads are kept so you can resume.');
        expect(within(sheet).getByRole('button', { name: 'Keep downloading' })).toHaveFocus();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Stop' }));
        expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('cancels at once under 1 GB', () => {
        const onCancel = vi.fn();
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 0.2e9, bytes_total: 23.3e9 }),
        ])} names={NAMES} onCancel={onCancel} />);
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(onCancel).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });

    it('offers Resume per cancelled row and Resume all once nothing runs', () => {
        const onResume = vi.fn();
        const onResumeAll = vi.fn();
        render(<DownloadProgress status={runStatus([
            progress({ id: QWEN.id, state: 'cancelled', bytes_done: 3e9, bytes_total: 23.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'cancelled', bytes_done: 0, bytes_total: 5.97e9 }),
        ], { state: 'cancelled' })} names={NAMES} onResume={onResume} onResumeAll={onResumeAll} />);
        fireEvent.click(within(rowOf('Qwen3.6-35B-A3B')).getByRole('button', { name: 'Resume' }));
        expect(onResume).toHaveBeenCalledWith(QWEN.id);
        fireEvent.click(screen.getByRole('button', { name: 'Resume all' }));
        expect(onResumeAll).toHaveBeenCalled();
    });

    it('announces state changes and failures, never each poll', () => {
        const st = (state: 'downloading' | 'ready' | 'failed', done: number) => runStatus([
            progress({ id: QWEN.id, state, bytes_done: done, bytes_total: 10e9, error_kind: state === 'failed' ? 'network' : null }),
        ]);
        const { rerender } = render(<DownloadProgress status={st('downloading', 1e9)} names={NAMES} />);
        const live = document.querySelector('[aria-live="polite"].visually-hidden')!;
        rerender(<DownloadProgress status={st('downloading', 1.1e9)} names={NAMES} />);
        expect(live.textContent).toBe('');
        rerender(<DownloadProgress status={st('downloading', 3e9)} names={NAMES} />);
        expect(live.textContent).toBe('Downloads 25 percent done.');
        rerender(<DownloadProgress status={st('failed', 3e9)} names={NAMES} />);
        expect(screen.getByRole('alert')).toHaveTextContent('Qwen3.6-35B-A3B failed to download.');
    });
});

describe('a run that stopped before its load check', () => {
    it('keeps the models that downloaded, with no Retry, and counts only real failures', () => {
        const onRetry = vi.fn();
        render(<DownloadProgress
            status={runStatus([
                progress({ id: EMBED.id, roles: ['embedding'], state: 'unchecked', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
                progress({ id: QWEN.id, state: 'unchecked', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
                progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', bytes_done: 1e9, bytes_total: 5.97e9, error_kind: 'network', can_retry: true }),
            ], { state: 'failed', overall: { bytes_done: 24.6e9, bytes_total: 29.57e9, speed_bps: 0, eta_s: null, percent: 83, failed: 1 } })}
            names={NAMES}
            onRetry={onRetry}
        />);
        const qwen = rowOf('Qwen3.6-35B-A3B');
        expect(within(qwen).getByText('Downloaded')).toBeInTheDocument();
        expect(within(qwen).getByText('Downloaded and kept. GAIA checks that it loads when you finish setup.')).toBeInTheDocument();
        expect(within(qwen).queryByRole('button')).not.toBeInTheDocument();
        expect(screen.getByText(/Failed: 1/)).toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1);
    });
});

describe('a model that failed its load check', () => {
    const failed = () => runStatus([
        progress({ id: QWEN.id, state: 'failed', bytes_done: 23.3e9, bytes_total: 23.3e9, error: 'would not load', error_kind: 'verify', can_retry: true }),
    ], { state: 'failed' });

    it('says it will be deleted and asks before Retry deletes it', () => {
        const onRetry = vi.fn();
        render(<DownloadProgress status={failed()} names={NAMES} onRetry={onRetry} />);
        const row = rowOf('Qwen3.6-35B-A3B');
        expect(within(row).getByText('Qwen3.6-35B-A3B downloaded but didn’t load. Retry deletes it and downloads it again.')).toBeInTheDocument();
        fireEvent.click(within(row).getByRole('button', { name: 'Retry' }));
        expect(onRetry).not.toHaveBeenCalled();
        const sheet = screen.getByRole('alertdialog', { name: 'Download Qwen3.6-35B-A3B again?' });
        expect(sheet).toHaveTextContent('GAIA deletes the copy on this PC and downloads it again (23.3 GB).');
        expect(within(sheet).getByRole('button', { name: 'Keep it' })).toHaveFocus();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Keep it' }));
        expect(onRetry).not.toHaveBeenCalled();
        fireEvent.click(within(row).getByRole('button', { name: 'Retry' }));
        fireEvent.click(screen.getByRole('button', { name: 'Delete and download again' }));
        expect(onRetry).toHaveBeenCalledExactlyOnceWith(QWEN.id, 'verify');
    });

    it('retries a network failure at once, with no sheet', () => {
        const onRetry = vi.fn();
        render(<DownloadProgress
            status={runStatus([progress({ id: QWEN.id, state: 'failed', bytes_done: 3e9, bytes_total: 23.3e9, error_kind: 'network', can_retry: true })], { state: 'failed' })}
            names={NAMES}
            onRetry={onRetry}
        />);
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        expect(onRetry).toHaveBeenCalledWith(QWEN.id, 'network');
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
});

describe('speed after a resume', () => {
    afterEach(() => vi.useRealTimers());

    it('never reads the jump from 0 to the bytes already on disk as speed', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(0);
        const at = (done: number, before: number | null = null): SetupRunStatus => runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: done, bytes_total: 23.3e9, downloaded_before_gb: before }),
        ]);
        // The first poll reads 0; the next jumps to 15 GB already on disk.
        const { rerender } = render(<DownloadProgress status={at(0)} names={NAMES} />);
        vi.setSystemTime(1000);
        rerender(<DownloadProgress status={at(15e9, 15)} names={NAMES} />);
        for (let s = 2; s <= 12; s++) {
            vi.setSystemTime(s * 1000);
            rerender(<DownloadProgress status={at(15e9 + (s - 1) * 150e6, 15)} names={NAMES} />);
        }
        const detail = within(rowOf('Qwen3.6-35B-A3B')).getByText(/MB\/s/);
        expect(detail).toHaveTextContent('150 MB/s');
        expect(detail.textContent).not.toMatch(/\d{4} MB\/s/);
    });
});
