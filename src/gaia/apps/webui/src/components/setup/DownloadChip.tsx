// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AlertCircle, Download } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import { isMoving, scopedStatus, useSetupDownloads } from '../../stores/setupDownloadsStore';
import './setup.css';

/**
 * "Downloading 2 of 3 · 38%" in the app chrome while models download, whether
 * setup or Settings started them, so the user can keep chatting and still see
 * it; a click opens Settings > Model, where the downloads can be cancelled or
 * resumed.
 */
export function DownloadChip({ placement = 'sidebar' }: { placement?: 'sidebar' | 'main' }) {
    const t = useT();
    const raw = useSetupDownloads((s) => s.status);
    const started = useSetupDownloads((s) => s.started);
    const queue = useSetupDownloads((s) => s.queue);
    const status = scopedStatus(raw, started);
    const refresh = useSetupDownloads((s) => s.refresh);
    const openSettings = useChatStore((s) => s.openSettings);
    // A pull from Settings runs outside the setup store; the app-wide status
    // poll follows it from start to failure even after Settings closes.
    const background = useChatStore((s) => s.systemStatus?.download_progress ?? null);

    useEffect(() => { void refresh(); }, [refresh]);

    const models = (status?.models ?? []).filter((m) => m.state !== 'skipped');
    const setupMoving = isMoving(status) || queue.length > 0;
    const backgroundMoving = background?.state === 'starting' || background?.state === 'downloading';
    const moving = setupMoving || backgroundMoving;
    const setupFailed = models.some((m) => m.state === 'failed') && status?.state !== 'running';
    const failed = !moving && (setupFailed || background?.state === 'error');
    if (!moving && !failed) return null;

    // A failed or cancelled row isn't downloading, so it isn't one of the "N of M".
    const counted = models.filter((m) => m.state !== 'failed' && m.state !== 'cancelled');
    const count = counted.length + queue.length;
    const finished = counted.filter((m) => m.state === 'ready' || m.state === 'verifying' || m.state === 'unchecked').length;
    // A Settings pull is one model; stale setup rows must not label it "2 of 2".
    const pct = setupMoving ? (status?.overall?.percent ?? 0) : (background?.percent ?? 0);
    const done = setupMoving ? Math.min(count, finished + 1) : 1;
    const total = setupMoving ? count : 1;
    return (
        <button
            type="button"
            className={`download-chip is-${placement}${failed && !moving ? ' is-failed' : ''}`}
            onClick={() => openSettings('model')}
            aria-label={t('download.chipAria')}
            title={t('download.chipAria')}
        >
            {failed ? <AlertCircle size={13} aria-hidden="true" /> : <Download size={13} aria-hidden="true" />}
            <span>{failed ? t('download.chipFailed') : t('download.chip', { done: String(done), count: String(total), pct: String(pct) })}</span>
            {moving && (
                <span className="download-chip-bar" aria-hidden="true"><span style={{ inlineSize: `${pct}%` }} /></span>
            )}
        </button>
    );
}
