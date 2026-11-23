// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { AlertCircle, Check, RotateCw } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import * as api from '../../services/api';
import type { AgentDownload } from '../../types';
import { formatBytes, ltrIn } from './format';
import './setup.css';

const POLL_MS = 1000;

const messageOf = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * The gaiacpp download: start (or join) it, poll it once a second while it runs,
 * and call `onDone` once when it finishes, saying whether `start` here began it.
 */
export function useAgentDownload(onDone: (startedHere: boolean) => void) {
    const [job, setJob] = useState<AgentDownload | null>(null);
    // The server answered "idle" mid-download: it restarted and the download is gone.
    const [interrupted, setInterrupted] = useState(false);
    const doneRef = useRef(onDone);
    doneRef.current = onDone;
    const finished = useRef(false);
    const startedHere = useRef(false);

    const start = useCallback(async () => {
        finished.current = false;
        startedHere.current = true;
        setInterrupted(false);
        setJob({ state: 'downloading', bytes_done: 0, bytes_total: null, error: null });
        try {
            setJob(await api.startAgentDownload());
        } catch (err) {
            setJob({ state: 'failed', bytes_done: 0, bytes_total: null, error: messageOf(err) });
        }
    }, []);

    /** A download already running, e.g. one setup started, is followed rather than restarted. */
    const follow = useCallback((running: AgentDownload) => {
        if (running.state !== 'downloading') return;
        finished.current = false;
        startedHere.current = false;
        setInterrupted(false);
        setJob(running);
    }, []);

    const state = job?.state;
    useEffect(() => {
        if (state !== 'downloading') return;
        let live = true;
        const timer = setTimeout(() => {
            api.getAgentDownload()
                .then((next) => {
                    if (!live) return;
                    if (next.state === 'idle') {
                        setInterrupted(true);
                        setJob((j) => ({ ...(j ?? next), state: 'failed', error: null }));
                    } else setJob(next);
                })
                .catch((err: unknown) => { if (live) setJob((j) => ({ ...(j ?? { bytes_done: 0, bytes_total: null }), state: 'failed', error: messageOf(err) })); });
        }, POLL_MS);
        return () => { live = false; clearTimeout(timer); };
    }, [job, state]);

    useEffect(() => {
        if (state !== 'done' || finished.current) return;
        finished.current = true;
        doneRef.current(startedHere.current);
    }, [state]);

    return { job, start, follow, interrupted };
}

function stateWord(job: AgentDownload, t: Translate): string {
    if (job.state === 'done') return t('setup.state.ready');
    if (job.state === 'failed') return t('setup.state.failed');
    return t('setup.state.downloading');
}

interface RowProps {
    job: AgentDownload;
    name: string;
    onRetry: () => void;
    /** What happens meanwhile, said under the failure. */
    failedNote?: string;
    /** The server restarted and dropped the download. */
    interrupted?: boolean;
}

/** One download row in the model rows' style: name, state, a bar with bytes done of total. */
export function AgentDownloadRow({ job, name, onRetry, failedNote, interrupted = false }: RowProps) {
    const t = useT();
    const errId = useId();
    const total = job.bytes_total ?? 0;
    const pct = job.state === 'done' ? 100 : total > 0 ? Math.min(100, (job.bytes_done / total) * 100) : 0;
    const state = stateWord(job, t);
    const sizes = total > 0 ? `${formatBytes(job.bytes_done)} / ${formatBytes(total)}` : formatBytes(job.bytes_done);
    return (
        <ul className="dl-list agent-download">
            <li className={`dl-row is-${job.state === 'done' ? 'ready' : job.state}${job.state === 'downloading' ? ' is-live' : ''}`}>
                <div className="dl-row-head">
                    <span className="dl-icon" aria-hidden="true">
                        {job.state === 'done' ? <Check size={14} /> : job.state === 'failed' ? <AlertCircle size={14} /> : <span className="dl-dot" />}
                    </span>
                    <span className="dl-name"><bdi dir="ltr">{name}</bdi></span>
                    <span className="dl-state">{state}</span>
                    {job.state === 'downloading' && <bdi className="dl-pct">{Math.floor(pct)}%</bdi>}
                </div>
                <div
                    className="dl-track"
                    role="progressbar"
                    aria-label={t('setup.progress.rowLabel', { name, state })}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.floor(pct)}
                    aria-valuetext={total > 0 ? t('setup.progress.of', { done: formatBytes(job.bytes_done), total: formatBytes(total) }) : state}
                >
                    <span className="dl-fill" style={{ inlineSize: `${pct}%` }} />
                </div>
                {job.state === 'downloading' && <p className="dl-detail"><bdi>{ltrIn(t.locale, sizes)}</bdi></p>}
                {job.state === 'failed' && (
                    <div className="dl-fail" role="alert">
                        <p className="dl-error" id={errId}>{t('setup.agent.downloadFailed', { name })}</p>
                        {interrupted && <p className="dl-detail">{t('setup.agent.downloadInterrupted')}</p>}
                        {failedNote && <p className="dl-detail">{failedNote}</p>}
                        {job.error && (
                            <details className="dl-details">
                                <summary>{t('setup.progress.details')}</summary>
                                <pre dir="ltr">{job.error}</pre>
                            </details>
                        )}
                        <div className="setup-row">
                            <button type="button" className="btn-primary" onClick={onRetry} aria-describedby={errId}>
                                <RotateCw size={13} aria-hidden="true" /> {t('setup.progress.retry')}
                            </button>
                        </div>
                    </div>
                )}
            </li>
        </ul>
    );
}
