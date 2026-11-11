// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let apiBase = 'http://127.0.0.1:4200/api';
vi.mock('../../utils/apiBase', () => ({ getApiBase: () => apiBase }));

import { useConnectorsSSE } from '../useConnectorsSSE';

class FakeEventSource {
    static instances: FakeEventSource[] = [];
    url: string;
    closed = false;
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(url: string) {
        this.url = url;
        FakeEventSource.instances.push(this);
    }
    close() {
        this.closed = true;
    }
}

beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    apiBase = 'http://127.0.0.1:4200/api';
    vi.stubGlobal('EventSource', FakeEventSource);
});
afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
});

describe('useConnectorsSSE', () => {
    it('reconnects to the restarted backend on its new port, not the dead one', () => {
        const onChange = vi.fn();
        const { unmount } = renderHook(() => useConnectorsSSE(onChange));
        expect(FakeEventSource.instances.map((es) => es.url)).toEqual([
            'http://127.0.0.1:4200/api/connectors/events',
        ]);

        // The backend dies and comes back on another port.
        apiBase = 'http://127.0.0.1:51234/api';
        act(() => FakeEventSource.instances[0].onerror?.());
        expect(FakeEventSource.instances[0].closed).toBe(true);
        act(() => vi.advanceTimersByTime(1000));

        expect(FakeEventSource.instances).toHaveLength(2);
        expect(FakeEventSource.instances[1].url).toBe('http://127.0.0.1:51234/api/connectors/events');

        act(() =>
            FakeEventSource.instances[1].onmessage?.({
                data: JSON.stringify({ type: 'connector.enabled', payload: { connector_id: 'github' } }),
            }),
        );
        expect(onChange).toHaveBeenCalledWith({
            connectorId: 'github',
            reason: 'enabled',
            payload: { connector_id: 'github' },
        });
        unmount();
    });
});
