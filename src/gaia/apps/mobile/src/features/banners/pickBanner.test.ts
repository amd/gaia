// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaNetworkError } from '@/api/errors';
import type { SystemStatus } from '@/api/client.types';
import { translator } from '@/i18n/useT';

import { pickBanner } from './pickBanner';

const t = translator('en');
const OK = {
    lemonade_running: true,
    context_size_sufficient: true,
    model_context_size: 65536,
    download_progress: null,
    init_state: 'ready',
} as unknown as SystemStatus;
const withStatus = (patch: Partial<SystemStatus>) => ({ ...OK, ...patch }) as SystemStatus;
const id = (status: SystemStatus | undefined, error: unknown = null) => pickBanner(status, error, t)?.id ?? null;

describe('pickBanner', () => {
    it('shows the web banner for each state, in the web order', () => {
        expect(id(OK)).toBeNull();
        expect(id(undefined)).toBeNull();
        expect(id(undefined, new GaiaNetworkError('http://pc', new Error('x')))).toBe('unreachable');
        const off = pickBanner(undefined, new GaiaNetworkError('http://pc', new Error('x')), t);
        expect(off?.title).toBe(t('mobileApp.pairing.off.title'));
        expect(off?.message).toBe(t('mobileApp.pairing.unreachable.body'));
        expect(pickBanner(undefined, new GaiaApiError(404, 'gone'), t)?.title).toBe(t('banner.unreachable'));
        expect(id(undefined, new GaiaApiError(503, 'busy'))).toBe('backendError');
        expect(id(withStatus({ init_state: 'initializing', init_tasks: [{ name: 'Models', status: 'running' }] }))).toBe('starting');
        expect(id(withStatus({ lemonade_running: false }))).toBe('serverStopped');
        expect(id(withStatus({ lemonade_running: false, lemonade_error: 'timed out' }))).toBe('serverHung');
        expect(id(withStatus({ context_size_sufficient: false }))).toBe('contextTooSmall');
        const download = { state: 'downloading', model_name: 'M', percent: 50, file: 'a.gguf', file_index: 1, total_files: 2, downloaded_bytes: 5e9, total_bytes: 1e10, message: null } as const;
        const banner = pickBanner(withStatus({ lemonade_running: false, download_progress: download }), null, t);
        expect(banner?.id).toBe('download');
        expect(banner?.progress?.value).toBe(0.5);
        expect(banner?.progress?.label).toBe('50% · 5.00 / 10.00 GB');
        expect(id(withStatus({ download_progress: { ...download, state: 'error' } }))).toBe('download');
    });

    it('says a PC that answered mid-download has gone away rather than show its last download, and keeps it for an HTTP error', () => {
        const download = { state: 'downloading', model_name: 'M', percent: 50, file: 'a.gguf', file_index: 1, total_files: 2, downloaded_bytes: 5e9, total_bytes: 1e10, message: null } as const;
        const last = withStatus({ download_progress: download });
        expect(id(last, new GaiaNetworkError('http://pc', new Error('refused')))).toBe('unreachable');
        expect(id(OK, new GaiaNetworkError('http://pc', new Error('refused')))).toBe('unreachable');
        expect(id(last, new GaiaApiError(503, 'busy'))).toBe('download');
        expect(id(OK, new GaiaApiError(503, 'busy'))).toBe('backendError');
    });
});
