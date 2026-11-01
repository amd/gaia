// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { AlertCircle, Check, Pause, RotateCw } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import type { SetupModelProgress, SetupModelState, SetupRunStatus } from '../../types';
import { ConfirmSheet } from './ConfirmSheet';
import { DownloadMeter, STALL_NOTICE_S, STALL_STUCK_S, formatBytes, formatEta, formatSize, formatSpeed, ltrIn, spokenEta, type Estimate } from './format';
import { usePageVisible } from './motion';

/** Cancelling past this much asks first. */
const CONFIRM_CANCEL_BYTES = 1e9;

/** Embedder first, then chat, then the rest: the order setup pulls them. */
function order(m: SetupModelProgress): number {
    if (m.roles.includes('embedding')) return 0;
    if (m.roles.includes('chat')) return 1;
    return 2;
}

export function sortedModels(models: SetupModelProgress[]): SetupModelProgress[] {
    return [...models].filter((m) => m.state !== 'skipped').sort((a, b) => order(a) - order(b));
}

const STATE_KEY: Record<SetupModelState, Parameters<Translate>[0]> = {
    queued: 'setup.state.queued',
    downloading: 'setup.state.downloading',
    verifying: 'setup.state.verifying',
    ready: 'setup.state.ready',
    failed: 'setup.state.failed',
    skipped: 'setup.state.skipped',
    cancelled: 'setup.state.cancelled',
};

/** Whether anything is still moving: a run, or a single download. */
export function downloadsActive(status: SetupRunStatus | null): boolean {
    if (!status) return false;
    if (status.state === 'running' || status.state === 'verifying') return true;
    return !!status.models?.some((m) => m.state === 'downloading' || m.state === 'queued');
}

function failureText(m: SetupModelProgress, name: string, freeGb: number | null, t: Translate): string {
    switch (m.error_kind) {
        case 'network':
            return t('setup.fail.network', { name });
        case 'disk_full': {
            const left = Math.max(0, m.bytes_total - m.bytes_done) / 1e9;
            const need = freeGb != null ? Math.max(0.1, left - freeGb) : left;
            return t('setup.fail.diskFull', { name, done: formatBytes(m.bytes_done), need: formatSize(need) });
        }
        case 'verify':
            return t('setup.fail.verify', { name });
        default:
            return t('setup.fail.lemonade', { name });
    }
}

interface RowProps {
    model: SetupModelProgress;
    name: string;
    estimate: Estimate | undefined;
    now: number;
    freeGb: number | null;
    onRetry?: (id: string, kind: SetupModelProgress['error_kind']) => void;
    onResume?: (id: string) => void;
    onSkip?: (id: string) => void;
    onRestart?: () => void;
    busy: boolean;
    /** The rest of the run is still downloading; Retry waits for it. */
    retryLater: boolean;
    /** The run is loading each model to check it; a verifying row is being checked now. */
    checking: boolean;
    /** Seconds this row has been checked, once past 10. */
    checkingFor: number;
}

function ProgressRow({ model: m, name, estimate, now, freeGb, onRetry, onResume, onSkip, onRestart, busy, retryLater, checking, checkingFor }: RowProps) {
    const t = useT();
    const errId = useId();
    const pct = m.bytes_total > 0 ? Math.min(100, (m.bytes_done / m.bytes_total) * 100) : m.state === 'ready' ? 100 : 0;
    const state = t(STATE_KEY[m.state]);
    const retryingFor = m.retrying_until ? Math.max(0, Math.round(m.retrying_until - now / 1000)) : 0;
    const stalled = m.state === 'downloading' && !retryingFor ? estimate?.stalledFor ?? 0 : 0;
    // Motion only while something moves: bytes arriving, or a file being checked.
    const live = (m.state === 'verifying' && checking) || (m.state === 'downloading' && !retryingFor && stalled < STALL_NOTICE_S);

    let detail: string;
    if (m.state === 'downloading') {
        const sizes = ltrIn(t.locale, m.bytes_total > 0 ? `${formatBytes(m.bytes_done)} / ${formatBytes(m.bytes_total)}` : formatBytes(m.bytes_done));
        if (retryingFor > 0) detail = t('setup.progress.retrying', { n: String(retryingFor) });
        else if (stalled >= STALL_STUCK_S) detail = t('setup.progress.stuck');
        else if (stalled >= STALL_NOTICE_S) detail = t('setup.progress.stalled', { n: String(STALL_NOTICE_S) });
        else if (estimate?.speed && estimate.eta != null) {
            const { speed, eta } = estimate;
            detail = t.compose((say) => `${sizes} · ${ltrIn(t.locale, formatSpeed(speed))} · ${formatEta(eta, say)}`);
        } else detail = t.compose((say) => `${sizes} · ${say('setup.progress.estimating')}`);
    } else if (m.state === 'verifying') {
        detail = !checking ? t('setup.progress.waitingCheck')
            : checkingFor >= 10 ? t('setup.progress.checkingFor', { n: String(checkingFor) })
                : t('setup.progress.checkingLoad');
    } else if (m.state === 'ready') {
        detail = m.bytes_total ? formatBytes(m.bytes_total) : '';
    } else if (m.state === 'cancelled') {
        detail = m.bytes_done > 0 ? t('setup.progress.keptPartial', { done: formatBytes(m.bytes_done) }) : '';
    } else if (m.state === 'queued') {
        detail = m.bytes_total ? formatBytes(m.bytes_total) : '';
    } else {
        detail = '';
    }

    const valueText = m.state === 'downloading'
        ? t.compose((say) => [
            say('setup.progress.percent', { n: String(Math.floor(pct)) }),
            m.bytes_total ? say('setup.progress.of', { done: formatBytes(m.bytes_done), total: formatBytes(m.bytes_total) }) : null,
            estimate?.eta != null ? spokenEta(estimate.eta, say) : null,
        ].filter(Boolean).join(', '))
        : state;

    return (
        <li className={`dl-row is-${m.state}${live ? ' is-live' : ''}${stalled >= STALL_NOTICE_S ? ' is-stalled' : ''}`}>
            <div className="dl-row-head">
                <span className="dl-icon" aria-hidden="true">
                    {m.state === 'ready' ? <Check size={14} /> : m.state === 'failed' ? <AlertCircle size={14} />
                        : m.state === 'cancelled' ? <Pause size={13} /> : <span className="dl-dot" />}
                </span>
                <span className="dl-name"><bdi title={m.id}>{name}</bdi></span>
                <span className="dl-state">{state}</span>
                {m.state === 'downloading' && <bdi className="dl-pct">{Math.floor(pct)}%</bdi>}
            </div>
            <div
                className="dl-track"
                role="progressbar"
                aria-label={t('setup.progress.rowLabel', { name, state })}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={live && m.state === 'verifying' ? undefined : Math.floor(pct)}
                aria-valuetext={valueText}
            >
                <span className="dl-fill" style={{ inlineSize: `${m.state === 'verifying' ? 100 : pct}%` }} />
            </div>
            {detail && <p className="dl-detail"><bdi>{detail}</bdi></p>}
            {m.state === 'failed' && (
                <div className="dl-fail">
                    <p className="dl-error" id={errId}>{failureText(m, name, freeGb, t)}</p>
                    {m.error && (
                        <details className="dl-details">
                            <summary>{t('setup.progress.details')}</summary>
                            <pre>{m.error}</pre>
                        </details>
                    )}
                    {retryLater ? <p className="dl-detail">{t('setup.progress.retryLater')}</p> : (
                    <div className="setup-row">
                        {onRetry && (
                            <button type="button" className="btn-primary" disabled={busy} onClick={() => onRetry(m.id, m.error_kind)} aria-describedby={errId}>
                                <RotateCw size={13} aria-hidden="true" /> {t('setup.progress.retry')}
                            </button>
                        )}
                        {onSkip && (
                            <button type="button" className="btn-secondary" disabled={busy} onClick={() => onSkip(m.id)}>
                                {t('setup.progress.skipModel')}
                            </button>
                        )}
                    </div>
                    )}
                </div>
            )}
            {m.state === 'cancelled' && onResume && (
                <div className="setup-row">
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => onResume(m.id)}>
                        {t('setup.progress.resume')}
                    </button>
                </div>
            )}
            {stalled >= STALL_STUCK_S && onRestart && (
                <div className="setup-row">
                    <button type="button" className="btn-secondary" disabled={busy} onClick={onRestart}>
                        <RotateCw size={13} aria-hidden="true" /> {t('setup.progress.retry')}
                    </button>
                </div>
            )}
        </li>
    );
}

export interface DownloadProgressProps {
    status: SetupRunStatus;
    names: Record<string, string>;
    /** Free space on the model drive, re-read after a disk-full failure. */
    freeGb?: number | null;
    onCancel?: () => void;
    onRetry?: (id: string, kind: SetupModelProgress['error_kind']) => void;
    onResume?: (id: string) => void;
    onResumeAll?: () => void;
    onSkip?: (id: string) => void;
    /** Cancel and start again, offered when a download has had no data for a minute. */
    onRestart?: () => void;
    busy?: boolean;
    /** A run is still pulling other models: a failed row waits to be retried. */
    retryLater?: boolean;
    /** Labels the region; the step or section heading. */
    labelledBy?: string;
}

/**
 * Per-model rows and one overall bar: queued → downloading → verifying →
 * ready, or failed with Retry, or cancelled with Resume. Every state has a
 * word; colour and motion only repeat it. The live region speaks milestones,
 * never the per-second percent.
 */
export function DownloadProgress({ status, names, freeGb = null, onCancel, onRetry, onResume, onResumeAll, onSkip, onRestart, busy = false, retryLater = false, labelledBy }: DownloadProgressProps) {
    const t = useT();
    const visible = usePageVisible();
    const models = useMemo(() => sortedModels(status.models ?? []), [status.models]);
    const meters = useRef(new Map<string, DownloadMeter>());
    const overallMeter = useRef(new DownloadMeter());
    const [now, setNow] = useState(() => Date.now());
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [announce, setAnnounce] = useState('');
    const [alert, setAlert] = useState('');
    const seen = useRef<{ states: Map<string, SetupModelState>; quarter: number } | null>(null);
    // The run's load check: when each row started being checked, for its elapsed time.
    const checking = status.state === 'verifying';
    const checkStarted = useRef(new Map<string, number>());

    // A countdown (keep trying) and a check's elapsed time move between polls.
    useEffect(() => {
        if (!visible || !(models.some((m) => m.retrying_until) || checking)) return;
        const tick = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(tick);
    }, [models, visible, checking]);

    const overall = status.overall;
    const { estimates, overallEstimate } = useMemo(() => {
        const at = Date.now();
        const out: Record<string, Estimate> = {};
        for (const m of models) {
            if (m.state !== 'downloading') {
                meters.current.delete(m.id);
                continue;
            }
            let meter = meters.current.get(m.id);
            if (!meter) {
                meter = new DownloadMeter();
                meters.current.set(m.id, meter);
            }
            out[m.id] = meter.update(m.bytes_done, m.bytes_total, at);
        }
        const moving = models.some((m) => m.state === 'downloading');
        // Time left counts only rows still coming: a failed or cancelled one isn't.
        const coming = models
            .filter((m) => m.state === 'downloading' || m.state === 'queued')
            .reduce((sum, m) => sum + Math.max(0, m.bytes_total - m.bytes_done), 0);
        const total = moving && overall ? overallMeter.current.update(overall.bytes_done, overall.bytes_done + coming, at) : undefined;
        return { estimates: out, overallEstimate: total };
    }, [models, overall]);

    // Milestones only: a row changing state, a failure, each quarter overall.
    useEffect(() => {
        const states = new Map(models.map((m) => [m.id, m.state]));
        const quarter = Math.floor((overall?.percent ?? 0) / 25);
        const before = seen.current;
        seen.current = { states, quarter };
        if (!before) return;
        const said: string[] = [];
        for (const m of models) {
            const was = before.states.get(m.id);
            if (was === m.state) continue;
            const name = names[m.id] ?? m.id;
            if (m.state === 'failed') setAlert(t('setup.progress.announceFailed', { name }));
            else said.push(t('setup.progress.announceState', { name, state: t(STATE_KEY[m.state]) }));
        }
        if (quarter > before.quarter && quarter > 0 && quarter < 4) said.push(t('setup.progress.announceOverall', { n: String(quarter * 25) }));
        if (said.length) setAnnounce(said.join('. '));
    }, [models, overall?.percent, names, t]);

    const checkingSeconds = (id: string): number => {
        if (!checking) {
            checkStarted.current.delete(id);
            return 0;
        }
        const at = Date.now();
        const started = checkStarted.current.get(id) ?? at;
        checkStarted.current.set(id, started);
        return Math.floor((at - started) / 1000);
    };

    const active = downloadsActive(status);
    const cancelled = models.filter((m) => m.state === 'cancelled');
    const failed = overall?.failed ?? models.filter((m) => m.state === 'failed').length;
    const pct = overall?.percent ?? 0;
    const cancel = () => {
        if ((overall?.bytes_done ?? 0) > CONFIRM_CANCEL_BYTES) setConfirmCancel(true);
        else onCancel?.();
    };

    const overallLine = t.compose((say) => [
        say('setup.progress.overallPct', { n: String(pct) }),
        overall && overall.bytes_total > 0 ? say('setup.progress.of', { done: formatBytes(overall.bytes_done), total: formatBytes(overall.bytes_total) }) : null,
        active && overallEstimate?.speed ? ltrIn(t.locale, formatSpeed(overallEstimate.speed)) : null,
        active ? (overallEstimate?.eta != null ? say('setup.progress.left', { eta: formatEta(overallEstimate.eta, say) }) : say('setup.progress.estimating')) : null,
        failed > 0 ? say('setup.progress.failedCount', { n: String(failed) }) : null,
    ].filter(Boolean).join(' · '));

    return (
        <section className={`dl${visible ? '' : ' is-paused'}`} aria-labelledby={labelledBy}>
            <div className="dl-overall">
                <p className="dl-overall-line"><bdi>{overallLine}</bdi></p>
                <div
                    className={`dl-track is-overall${models.some((m) => m.state === 'downloading') ? ' is-live' : ''}`}
                    role="progressbar"
                    aria-label={t('setup.progress.overallLabel')}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={pct}
                    aria-valuetext={t('setup.progress.percent', { n: String(pct) })}
                >
                    <span className="dl-fill" style={{ inlineSize: `${pct}%` }} />
                </div>
            </div>
            <ul className="dl-list">
                {models.map((m) => (
                    <ProgressRow
                        key={m.id}
                        model={m}
                        name={names[m.id] ?? m.id}
                        estimate={estimates[m.id]}
                        now={now}
                        freeGb={freeGb}
                        onRetry={onRetry}
                        onResume={active ? undefined : onResume}
                        onSkip={onSkip}
                        onRestart={onRestart}
                        busy={busy}
                        retryLater={retryLater}
                        checking={checking}
                        checkingFor={checkingSeconds(m.id)}
                    />
                ))}
            </ul>
            <div className="setup-row dl-controls">
                {active && onCancel && (
                    <button type="button" className="btn-secondary" onClick={cancel} disabled={busy}>{t('setup.progress.cancel')}</button>
                )}
                {!active && cancelled.length > 1 && onResumeAll && (
                    <button type="button" className="btn-primary" onClick={onResumeAll} disabled={busy}>{t('setup.progress.resumeAll')}</button>
                )}
                {active && <span className="setup-hint">{t('setup.progress.keepsGoing')}</span>}
            </div>
            <span className="visually-hidden" aria-live="polite">{announce}</span>
            <span className="visually-hidden" role="alert">{alert}</span>
            {confirmCancel && (
                <ConfirmSheet
                    title={t('setup.progress.cancelTitle')}
                    confirmLabel={t('setup.progress.cancelConfirm')}
                    cancelLabel={t('setup.progress.cancelKeep')}
                    onConfirm={() => { setConfirmCancel(false); onCancel?.(); }}
                    onCancel={() => setConfirmCancel(false)}
                >
                    <p>{t('setup.progress.cancelBody')}</p>
                </ConfirmSheet>
            )}
        </section>
    );
}

