// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AlertCircle, Download } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import { isMoving, scopedStatus, useSetupDownloads } from '../../stores/setupDownloadsStore';
import './setup.css';

/**
 * "Downloading 2 of 3 · 38%" in the app chrome while models download, so the
 * user can keep chatting and still see it; a click opens Settings > Model,
 * where the downloads can be cancelled or resumed.
 */
export function DownloadChip({ placement = 'sidebar' }: { placement?: 'sidebar' | 'main' }) {
    const t = useT();
    const raw = useSetupDownloads((s) => s.status);
    const started = useSetupDownloads((s) => s.started);
    const queue = useSetupDownloads((s) => s.queue);
    const status = scopedStatus(raw, started);
    const refresh = useSetupDownloads((s) => s.refresh);
    const openSettings = useChatStore((s) => s.openSettings);

    useEffect(() => { void refresh(); }, [refresh]);

    const models = (status?.models ?? []).filter((m) => m.state !== 'skipped');
    const moving = isMoving(status) || queue.length > 0;
    const failed = models.some((m) => m.state === 'failed') && status?.state !== 'running';
    if (!moving && !failed) return null;

    const count = models.length + queue.length;
    const finished = models.filter((m) => m.state === 'ready' || m.state === 'verifying').length;
    const pct = status?.overall?.percent ?? 0;
    return (
        <button
            type="button"
            className={`download-chip is-${placement}${failed && !moving ? ' is-failed' : ''}`}
            onClick={() => openSettings('model')}
            aria-label={t('download.chipAria')}
            title={t('download.chipAria')}
        >
            {failed && !moving ? <AlertCircle size={13} aria-hidden="true" /> : <Download size={13} aria-hidden="true" />}
            <span>{failed && !moving ? t('download.chipFailed') : t('download.chip', { done: String(Math.min(count, finished + 1)), count: String(count), pct: String(pct) })}</span>
            {moving && (
                <span className="download-chip-bar" aria-hidden="true"><span style={{ inlineSize: `${pct}%` }} /></span>
            )}
        </button>
    );
}
