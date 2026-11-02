// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2 } from 'lucide-react';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useT } from '../i18n/useT';
import * as api from '../services/api';
import { useModelNeededStore } from '../stores/modelNeededStore';
import { formatSize } from './setup/format';
import './ModelNeededDialog.css';

const POLL_MS = 1000;
/** Polls with no row for the model before the download counts as never started. */
const NO_ROW_LIMIT = 15;
/** Status reads in a row that may fail before the download counts as lost. */
const MISS_LIMIT = 5;

/**
 * A job needs a model that isn't on this PC: say which, how big and from where,
 * and ask. The message that needed it waits here until the model is ready.
 */
export function ModelNeededDialog() {
    const t = useT();
    const need = useModelNeededStore((s) => s.need);
    const settle = useModelNeededStore((s) => s.settle);
    const panel = useRef<HTMLDivElement>(null);
    const timer = useRef<ReturnType<typeof setInterval> | null>(null);
    const noRow = useRef(0);
    const misses = useRef(0);
    // The prompt a download belongs to: a closed or replaced one must not be answered by it.
    const shown = useRef<typeof need>(null);
    shown.current = need;
    const [percent, setPercent] = useState<number | null>(null);
    const [downloading, setDownloading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // The server found less free space than the prompt was opened with.
    const [serverShort, setServerShort] = useState(false);

    const stop = useCallback(() => {
        if (timer.current) clearInterval(timer.current);
        timer.current = null;
    }, []);

    useEffect(() => {
        setPercent(null);
        setDownloading(false);
        setError(null);
        setServerShort(false);
        return stop;
    }, [need, stop]);

    const close = useCallback(() => {
        stop();
        if (downloading && need?.model) {
            api.cancelSetup(need.model.id).catch((err) => console.warn('[ModelNeededDialog] could not cancel the download', err));
        }
        settle(false);
    }, [downloading, need, settle, stop]);

    useEscapeToClose(!!need, close);
    useFocusTrap(panel, !!need);

    const download = useCallback(async (acknowledgeLowDisk: boolean) => {
        if (!need?.model) return;
        const asked = need;
        const id = need.model.id;
        setError(null);
        setDownloading(true);
        noRow.current = 0;
        misses.current = 0;
        try {
            await api.downloadSetupModel(id, { acknowledgeLowDisk });
        } catch (err) {
            if (shown.current !== asked) return;
            setDownloading(false);
            setError(err instanceof Error ? err.message : String(err));
            if (err instanceof api.ApiError && err.body?.code === 'low_disk') setServerShort(true);
            return;
        }
        if (shown.current !== asked) return;
        stop();
        timer.current = setInterval(() => {
            api.getSetupStatus().then((status) => {
                if (shown.current !== asked) {
                    stop();
                    return;
                }
                misses.current = 0;
                const row = status.models?.find((m) => m.id === id);
                if (!row) {
                    if (++noRow.current < NO_ROW_LIMIT) return;
                    stop();
                    setDownloading(false);
                    setError(t('modelNeeded.failed'));
                    return;
                }
                if (row.bytes_total > 0) setPercent(Math.floor((row.bytes_done * 100) / row.bytes_total));
                if (row.state === 'ready' || row.state === 'verifying') {
                    stop();
                    settle(true);
                } else if (row.state === 'failed' || row.state === 'cancelled') {
                    stop();
                    setDownloading(false);
                    setError(row.error ?? t('modelNeeded.failed'));
                }
            }).catch((err) => {
                if (shown.current !== asked) {
                    stop();
                    return;
                }
                // One unanswered read isn't a lost download: it keeps going on the server.
                if (++misses.current < MISS_LIMIT) return;
                stop();
                setDownloading(false);
                setError(err instanceof Error ? err.message : String(err));
            });
        }, POLL_MS);
    }, [need, settle, stop, t]);

    if (!need?.model) return null;
    const { model, job, disk_free_gb: freeGb } = need;
    // The same rule as setup's: more to download than is free.
    const tooBig = model.size_gb != null && freeGb != null && model.size_gb > freeGb;
    const short = tooBig || serverShort;
    return createPortal(
        <div className="model-needed-overlay">
            <div ref={panel} className="model-needed" role="dialog" aria-modal="true" aria-label={t('modelNeeded.dialog')}>
                <h2 className="model-needed-title">{t(`modelNeeded.title.${job}`)}</h2>
                <p className="model-needed-line">
                    <bdi>{model.name}</bdi>
                    {model.size_gb != null && <>, <bdi dir="ltr">{formatSize(model.size_gb)}</bdi></>}
                    {freeGb != null && <> · {t('modelNeeded.free')} <bdi dir="ltr">{formatSize(freeGb)}</bdi></>}
                </p>
                {model.source && <p className="model-needed-from">{t('setup.contents.from')} <bdi dir="ltr">{model.source}</bdi></p>}
                <p className="model-needed-from">{t('modelNeeded.waits')}</p>
                {downloading && (
                    percent != null
                        ? <progress className="setup-progress" max={100} value={percent} aria-label={t('modelNeeded.dialog')} />
                        : <p role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('modelNeeded.starting')}</p>
                )}
                {tooBig && !downloading && (
                    <p className="setup-error" role="alert">
                        {t('modelNeeded.lowDisk', { need: formatSize(model.size_gb ?? 0), free: formatSize(freeGb ?? 0), freeUp: formatSize((model.size_gb ?? 0) - (freeGb ?? 0)) })}
                    </p>
                )}
                {error && <p className="setup-error" role="alert">{error}</p>}
                <div className="model-needed-actions">
                    <button type="button" className="btn-secondary" onClick={close}>{t('modelNeeded.notNow')}</button>
                    <button type="button" className="btn-primary" onClick={() => void download(short)} disabled={downloading}>
                        {short ? t('setup.confirm.downloadAnyway') : error ? t('setup.tryAgain') : t('modelNeeded.download')}
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
}
