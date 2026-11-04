// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The chat's resource strip: what it shows, how it polls, and how it fails. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GRAPH_SLOTS, ResourceMonitor, Sparkline } from '../ResourceMonitor';
import { RETRY_MS } from '../../hooks/useResourceUsage';
import type { ResourceUsage } from '../../types';

const GIB = 1024 ** 3;

const USAGE: ResourceUsage = {
    interval_ms: 1000,
    ready: true,
    cpu: { percent: 20.4, ghz: 4.7, history: [10, 20.4] },
    memory: { used_bytes: 45 * GIB, total_bytes: 63.6 * GIB, percent: 70.8, history: [70, 70.8] },
    devices: [
        { id: 'gpu', name: 'AMD Radeon(TM) 8060S Graphics', kind: 'gpu', percent: 14, memory_used_bytes: 30 * GIB, history: [12, 14] },
        { id: 'npu', name: 'NPU Compute Accelerator Device', kind: 'npu', percent: 0, memory_used_bytes: null, history: [0, 0] },
    ],
};

let responses: Array<() => Response>;
const fetchMock = vi.fn(async (input: string) => {
    expect(new URL(input, 'http://localhost').pathname).toBe('/api/system/resources');
    const next = responses.length > 1 ? responses.shift()! : responses[0];
    return next();
});

function json(body: unknown, status = 200): () => Response {
    return () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

beforeEach(() => {
    responses = [json(USAGE)];
    fetchMock.mockClear();
    vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function tile(label: string): HTMLElement {
    const el = screen.getByText((_, node) => node?.matches('.resource-label') === true && node.firstChild?.textContent === label).closest('li');
    if (!el) throw new Error(`no tile ${label}`);
    return el;
}

describe('ResourceMonitor', () => {
    it('shows CPU, memory, GPU and NPU the way Task Manager words them', async () => {
        render(<ResourceMonitor onClose={() => {}} />);
        const region = await screen.findByRole('region', { name: 'Resource usage on this PC' });
        await waitFor(() => expect(region.querySelectorAll('li')).toHaveLength(4));

        expect(tile('CPU')).toHaveTextContent('20%4.70 GHz');
        expect(tile('Memory')).toHaveTextContent('71%45.0/63.6 GB');
        expect(tile('GPU')).toHaveTextContent('14%30.0 GB');
        expect(tile('NPU')).toHaveTextContent(/^NPU \(NPU Compute Accelerator Device\)0%$/);
        expect(tile('GPU')).toHaveAttribute('title', 'AMD Radeon(TM) 8060S Graphics · 14% · 30.0 GB in use');
    });

    it('names each GPU and NPU for screen readers, not only in a hover tooltip', async () => {
        render(<ResourceMonitor onClose={() => {}} />);
        const name = await screen.findByText('(AMD Radeon(TM) 8060S Graphics)');
        expect(name).toHaveClass('resource-sr-only');
        expect(tile('CPU').querySelector('.resource-sr-only')).toBeNull();
    });

    it('announces a failure as a status, never the live numbers', async () => {
        responses = [json({ detail: 'boom' }, 503)];
        render(<ResourceMonitor onClose={() => {}} />);
        expect(await screen.findByRole('status')).toHaveTextContent("Can't read this PC's usage. Trying again…");
        expect(document.querySelector('[aria-live]')).toBeNull();
    });

    it('keeps numbers with units left-to-right so an RTL layout cannot reverse them', async () => {
        render(<ResourceMonitor onClose={() => {}} />);
        await screen.findByText('45.0/63.6 GB');
        for (const el of document.querySelectorAll('.resource-value, .resource-detail')) {
            expect(el.tagName).toBe('BDI');
            expect(el).toHaveAttribute('dir', 'ltr');
        }
    });

    it('numbers several GPUs from 0, like Task Manager', async () => {
        const second = { ...USAGE.devices![0], id: 'gpu2', name: 'Radeon RX', percent: 50 };
        responses = [json({ ...USAGE, devices: [USAGE.devices![0], second] })];
        render(<ResourceMonitor onClose={() => {}} />);
        expect(await screen.findByText('GPU 0')).toBeInTheDocument();
        expect(screen.getByText('GPU 1')).toBeInTheDocument();
    });

    it('says it is reading until the first sample is in', async () => {
        responses = [json({ interval_ms: 1000, ready: false, devices: [] })];
        render(<ResourceMonitor onClose={() => {}} />);
        expect(await screen.findByText('Reading…')).toBeInTheDocument();
    });

    it('says when it cannot read usage, rather than showing nothing', async () => {
        responses = [json({ detail: "Could not read this PC's resource usage: PdhOpenQuery failed" }, 503)];
        render(<ResourceMonitor onClose={() => {}} />);
        expect(await screen.findByText("Can't read this PC's usage. Trying again…")).toBeInTheDocument();
    });

    it('dims the last reading and says so when a later read fails', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        responses = [json(USAGE), json({ detail: 'boom' }, 503)];
        render(<ResourceMonitor onClose={() => {}} />);
        await screen.findByText('4.70 GHz');
        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        await screen.findByText("Can't read this PC's usage. Trying again…");
        expect(document.querySelector('.resource-tiles')).toHaveClass('is-stale');
        expect(screen.getByText('4.70 GHz')).toBeInTheDocument();
    });

    it('polls once per interval, backs off after a failure, and stops when closed', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        responses = [json(USAGE), json({ detail: 'boom' }, 503), json(USAGE)];
        const { unmount } = render(<ResourceMonitor onClose={() => {}} />);
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

        await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
        expect(fetchMock).toHaveBeenCalledTimes(2); // this one fails

        await act(async () => { await vi.advanceTimersByTimeAsync(RETRY_MS - 100); });
        expect(fetchMock).toHaveBeenCalledTimes(2);
        await act(async () => { await vi.advanceTimersByTimeAsync(200); });
        expect(fetchMock).toHaveBeenCalledTimes(3);

        unmount();
        await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
        expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it('stops polling while the window is hidden and catches up when it comes back', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        render(<ResourceMonitor onClose={() => {}} />);
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

        const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
        act(() => { document.dispatchEvent(new Event('visibilitychange')); });
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(fetchMock).toHaveBeenCalledTimes(1);

        hidden.mockReturnValue(false);
        await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    });

    it('closes from a named button', async () => {
        const onClose = vi.fn();
        render(<ResourceMonitor onClose={onClose} />);
        fireEvent.click(screen.getByRole('button', { name: 'Hide resource usage' }));
        expect(onClose).toHaveBeenCalledOnce();
    });
});

describe('Sparkline', () => {
    it('draws newest on the right and keeps values inside the box', () => {
        const { container } = render(<Sparkline values={[-5, 50, 150]} />);
        const line = container.querySelector('.resource-spark-line')!.getAttribute('d')!;
        const points = line.split(/[ML]/).filter(Boolean).map((p) => p.trim().split(' ').map(Number));
        expect(points[points.length - 1]).toEqual([100, 4]); // 150% clamps to the top, at the right edge
        expect(points[0][1]).toBe(96); // -5% clamps to the bottom, still inside the border
        expect(points[0][0]).toBeCloseTo(((GRAPH_SLOTS - 3) / (GRAPH_SLOTS - 1)) * 100, 2);
        expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    });

    it('draws nothing from a single sample', () => {
        const { container } = render(<Sparkline values={[40]} />);
        expect(container.querySelector('path')).toBeNull();
    });
});
