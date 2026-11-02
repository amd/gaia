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
    const [percent, setPercent] = useState<number | null>(null);
    const [downloading, setDownloading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const stop = useCallback(() => {
        if (timer.current) clearInterval(timer.current);
        timer.current = null;
    }, []);

    useEffect(() => {
        setPercent(null);
        setDownloading(false);
        setError(null);
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

    const download = useCallback(async () => {
        if (!need?.model) return;
        const id = need.model.id;
        setError(null);
        setDownloading(true);
        try {
            await api.downloadSetupModel(id, { acknowledgeLowDisk: true });
        } catch (err) {
            setDownloading(false);
            setError(err instanceof Error ? err.message : String(err));
            return;
        }
        timer.current = setInterval(() => {
            api.getSetupStatus().then((status) => {
                const row = status.models?.find((m) => m.id === id);
                if (!row) return;
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
                stop();
                setDownloading(false);
                setError(err instanceof Error ? err.message : String(err));
            });
        }, POLL_MS);
    }, [need, settle, stop, t]);

    if (!need?.model) return null;
    const { model, job, disk_free_gb: freeGb } = need;
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
                {error && <p className="setup-error" role="alert">{error}</p>}
                <div className="model-needed-actions">
                    <button type="button" className="btn-secondary" onClick={close}>{t('modelNeeded.notNow')}</button>
                    <button type="button" className="btn-primary" onClick={() => void download()} disabled={downloading}>
                        {error ? t('setup.tryAgain') : t('modelNeeded.download')}
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
}
