// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Download, Loader2, XCircle, RefreshCw } from 'lucide-react';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import type { DownloadProgress } from '../../types';

interface ModelDownloadStepProps {
    modelName: string;
    /** Guard the Next button until the model is downloaded (or loaded). */
    onGuardChange: (blocked: boolean) => void;
}

const POLL_MS = 2000;

function gb(bytes: number): string {
    return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Step 3 — in-app model download with visible progress + retry (#1726).
 *
 * Drives the existing ``POST /api/system/download-model`` endpoint and reads
 * its SSE-fed progress from ``GET /api/system/status.download_progress`` — the
 * same channel the main app uses, no new backend surface. A failed download is
 * surfaced loudly with the backend's message and a Retry (force re-pull);
 * nothing silently advances past a missing model.
 */
export function ModelDownloadStep({ modelName, onGuardChange }: ModelDownloadStepProps) {
    const [progress, setProgress] = useState<DownloadProgress | null>(null);
    const [downloaded, setDownloaded] = useState(false);
    const [starting, setStarting] = useState(false);
    const [startError, setStartError] = useState<string | null>(null);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const stopPolling = useCallback(() => {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }, []);

    // Returns true while a download is still in flight (so callers can keep polling).
    const poll = useCallback(async (): Promise<boolean> => {
        try {
            const status = await api.getSystemStatus();
            setProgress(status.download_progress);
            const isReady =
                status.download_progress?.state === 'complete' ||
                status.model_downloaded === true ||
                (!!status.model_loaded &&
                    status.model_loaded.toLowerCase() === modelName.toLowerCase());
            if (isReady) {
                setDownloaded(true);
                onGuardChange(false);
                stopPolling();
                return false;
            }
            const state = status.download_progress?.state;
            return state === 'downloading' || state === 'starting';
        } catch (err) {
            log.system.warn('Model download poll failed', err);
            return false;
        }
    }, [modelName, onGuardChange, stopPolling]);

    // Start the polling loop, guarded so we never stack two intervals.
    const beginPolling = useCallback(() => {
        if (pollRef.current) return;
        pollRef.current = setInterval(poll, POLL_MS);
    }, [poll]);

    // On mount: guard until we know the model exists, then poll once to detect
    // an already-downloaded model. If a download is already in flight (the wizard
    // remounted mid-pull — refresh or Back nav), resume the loop so progress keeps
    // advancing instead of freezing on the first frame.
    useEffect(() => {
        onGuardChange(true);
        poll().then((active) => {
            if (active) beginPolling();
        });
        return stopPolling;
    }, [onGuardChange, poll, beginPolling, stopPolling]);

    const startDownload = useCallback(async (force: boolean) => {
        setStarting(true);
        setStartError(null);
        onGuardChange(true);
        try {
            await api.downloadModel(modelName, force);
            log.system.info(`Onboarding download started: ${modelName} (force=${force})`);
            beginPolling();
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to start download.';
            log.system.error('Onboarding download failed to start', err);
            setStartError(message);
        } finally {
            setStarting(false);
        }
    }, [modelName, onGuardChange, beginPolling]);

    if (downloaded) {
        return (
            <div className="onboarding-body" data-testid="onboarding-download-done">
                <h2>Model ready</h2>
                <div className="onboarding-banner warn" style={{ background: 'transparent', border: 'none' }}>
                    <CheckCircle2 size={18} className="pf-icon ok" />
                    <div><span className="download-model-name">{modelName}</span> is downloaded and ready to use.</div>
                </div>
            </div>
        );
    }

    const errored = progress?.state === 'error' || !!startError;
    const errorMessage = startError || progress?.message || 'Download failed.';
    const active = progress?.state === 'downloading' || progress?.state === 'starting';

    return (
        <div className="onboarding-body" data-testid="onboarding-download">
            <h2>Download your AI model</h2>
            <p className="lede">
                GAIA needs a local model to run. This is a one-time download of{' '}
                <span className="download-model-name">{modelName}</span>.
            </p>

            {errored && (
                <div className="onboarding-banner error" role="alert" data-testid="download-error">
                    <XCircle size={18} />
                    <div>
                        {errorMessage}
                        <div style={{ marginTop: 6 }}>Check your connection and disk space, then retry.</div>
                    </div>
                </div>
            )}

            {active && progress && (
                <div data-testid="download-progress">
                    <div className="download-model-name">{progress.file || modelName}</div>
                    <div className="download-bar">
                        <div className="fill" style={{ width: `${progress.percent}%` }} />
                    </div>
                    <div className="download-meta">
                        <span>{progress.percent}%</span>
                        <span>
                            {progress.total_bytes > 0
                                ? `${gb(progress.downloaded_bytes)} / ${gb(progress.total_bytes)}`
                                : 'Starting…'}
                        </span>
                    </div>
                </div>
            )}

            {!active && !errored && (
                <button
                    className="onboarding-btn primary"
                    onClick={() => startDownload(false)}
                    disabled={starting}
                    data-testid="download-start"
                >
                    {starting ? <Loader2 size={15} className="onboarding-spin" /> : <Download size={15} />}
                    {starting ? 'Starting…' : 'Download model'}
                </button>
            )}

            {errored && (
                <button
                    className="onboarding-btn primary"
                    onClick={() => startDownload(true)}
                    disabled={starting}
                    data-testid="download-retry"
                >
                    <RefreshCw size={15} /> Retry download
                </button>
            )}
        </div>
    );
}
