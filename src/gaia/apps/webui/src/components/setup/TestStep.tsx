// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { Check, Circle, Loader2, Minus, X } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import { displayModelName } from '../../stores/modelStore';
import type { SetupCatalogEntry, SetupRunStatus, SetupTest } from '../../types';
import { ConfirmSheet } from './ConfirmSheet';
import { formatSize } from './format';
import { SetupFooter } from './SetupFooter';

/** A failed test's plain reason, by what went wrong; anything else gets the general one. */
const WHY: Record<string, Parameters<Translate>[0]> = {
    memory: 'setup.test.why.memory',
    broken: 'setup.test.why.broken',
    stuck: 'setup.test.why.stuck',
    missing: 'setup.test.why.missing',
    agent: 'setup.test.why.agent',
    answer: 'setup.test.why.answer',
    window: 'setup.test.why.window',
};

function jobLabel(job: SetupTest['job'], t: Translate): string {
    return t(`setup.test.job.${job}`);
}

function Icon({ state }: { state: SetupTest['state'] | 'waiting' }) {
    if (state === 'passed') return <Check size={13} aria-hidden="true" />;
    if (state === 'running') return <Loader2 size={13} className="spin" aria-hidden="true" />;
    if (state === 'failed') return <X size={13} aria-hidden="true" />;
    if (state === 'skipped') return <Minus size={13} aria-hidden="true" />;
    return <Circle size={13} aria-hidden="true" />;
}

function resultText(row: SetupTest, t: Translate): string {
    if (row.state === 'running') return t('setup.test.running');
    if (row.state === 'failed') return t('setup.test.failed');
    if (row.state === 'skipped') return t('setup.test.skipped');
    return row.seconds != null ? t('setup.test.passedIn', { seconds: String(Math.round(row.seconds)) }) : t('setup.test.passed');
}

interface Props {
    run: SetupRunStatus;
    /** The jobs this setup should end up testing, so the bar has a true total. */
    expected: SetupTest['job'][];
    names: Record<string, string>;
    catalog: SetupCatalogEntry[];
    onRetry: () => void;
    /** Absent on a cloud setup: the local model picker would switch it to local. */
    onChooseAnother?: () => void;
    /** Stops the tests while they run. */
    onCancel?: () => void;
    /** Absent on the user's own server, where GAIA deletes nothing. */
    onDownloadAgain?: (id: string) => void;
    busy: boolean;
}

/** "Test": each job runs for real, and setup is ready only when they pass. */
export function TestStep({ run, expected, names, catalog, onRetry, onChooseAnother, onCancel, onDownloadAgain, busy }: Props) {
    const t = useT();
    const rows = run.tests ?? [];
    const seen = new Set(rows.map((r) => r.job));
    const waiting = expected.filter((job) => !seen.has(job));
    const finished = rows.filter((r) => r.state !== 'running').length;
    const total = Math.max(rows.length + waiting.length, 1);
    const percent = Math.round((finished / total) * 100);
    const failed = rows.filter((r) => r.state === 'failed');
    const required = failed.filter((r) => !catalog.find((e) => e.id === r.subject)?.roles.some((role) => ['speech', 'voice', 'image_creation'].includes(role)));
    const stopped = run.state === 'failed' || run.state === 'cancelled';
    const broken = onDownloadAgain
        ? [...new Set(required.filter((r) => r.kind === 'broken' && r.subject).map((r) => r.subject as string))]
        : [];
    const [again, setAgain] = useState<string | null>(null);
    const againSize = catalog.find((e) => e.id === again)?.full_size_gb;
    // The assistant never installed (no download server, or no internet): its test can't run.
    const agentNotInstalled = stopped && run.components?.agent === 'failed';
    // Only a model's own failure is helped by choosing another model; a missing assistant isn't one.
    const modelAtFault = required.some((r) => r.subject && r.kind !== 'missing');
    // Between two tests the run is still at work: the next one is starting, not waiting.
    const startingJob = run.state === 'verifying' && !rows.some((r) => r.state === 'running') ? waiting[0] : undefined;
    return (
        <>
            <section className="setup-card" aria-label={t('setup.test.title')}>
                <div className="setup-bar-head">
                    <span>{t('setup.test.count', { done: String(finished), total: String(total) })}</span>
                    <bdi dir="ltr">{percent}%</bdi>
                </div>
                <progress className="setup-progress" max={100} value={percent} aria-label={t('setup.test.title')} />
                <ul className="setup-rows">
                    {rows.map((r) => (
                        <li key={`${r.job}:${r.subject ?? ''}`} className={`setup-result is-${r.state}`}>
                            <Icon state={r.state} />
                            <span className="setup-result-label">
                                {jobLabel(r.job, t)}
                                {r.subject && <bdi className="setup-result-model"> {names[r.subject] ?? displayModelName(r.subject)}</bdi>}
                            </span>
                            <span className="setup-result-value">{resultText(r, t)}</span>
                            {r.error && (
                                <div className="setup-result-error">
                                    {/* A plain reason first; the engine's own words, paths and codes, behind Details. */}
                                    <p>{t(r.kind === 'broken' && !onDownloadAgain ? 'setup.test.why.brokenYours' : WHY[r.kind ?? ''] ?? 'setup.test.why.other')}</p>
                                    <details className="dl-details">
                                        <summary>{t('setup.progress.details')}</summary>
                                        <pre dir="ltr">{r.error}</pre>
                                    </details>
                                </div>
                            )}
                        </li>
                    ))}
                    {waiting.map((job) => (job === 'agent' && agentNotInstalled ? (
                        <li key={job} className="setup-result is-failed">
                            <Icon state="failed" />
                            <span className="setup-result-label">{jobLabel(job, t)}</span>
                            <span className="setup-result-value">{t('setup.test.failed')}</span>
                            <div className="setup-result-error"><p>{t('setup.test.why.hub')}</p></div>
                        </li>
                    ) : job === startingJob ? (
                        <li key={job} className="setup-result is-running">
                            <Icon state="running" />
                            <span className="setup-result-label">{jobLabel(job, t)}</span>
                            <span className="setup-result-value">{t('setup.test.starting')}</span>
                        </li>
                    ) : (
                        <li key={job} className="setup-result is-waiting">
                            <Icon state="waiting" />
                            <span className="setup-result-label">{jobLabel(job, t)}</span>
                            <span className="setup-result-value">{t('setup.test.waiting')}</span>
                        </li>
                    )))}
                </ul>
            </section>
            {run.state === 'verifying' && onCancel && (
                <SetupFooter>
                    <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>{t('setup.progress.cancel')}</button>
                </SetupFooter>
            )}
            {stopped && (
                <div className="setup-failure" role="alert">
                    <p>{run.state === 'cancelled' ? t('setup.test.cancelled') : required.some((r) => r.kind === 'memory') ? t('setup.test.failedMemory') : t('setup.test.failedHelp')}</p>
                    {/* The way on sits in the bar like every step's; a damaged file's fix is downloading it again. */}
                    <SetupFooter>
                        {modelAtFault && onChooseAnother && (
                            <button type="button" className="btn-secondary" onClick={onChooseAnother} disabled={busy}>{t('setup.test.chooseAnother')}</button>
                        )}
                        <button type="button" className={broken.length ? 'btn-secondary' : 'btn-primary'} onClick={onRetry} disabled={busy}>{t('setup.test.tryAgain')}</button>
                        {broken.map((id) => (
                            <button key={id} type="button" className="btn-primary setup-wrap-btn" onClick={() => setAgain(id)} disabled={busy}>
                                {/* One damaged model is named on its row above; several need their names here. */}
                                {broken.length === 1 ? t('setup.test.downloadItAgain') : t('setup.test.downloadAgain', { name: names[id] ?? id })}
                            </button>
                        ))}
                    </SetupFooter>
                </div>
            )}
            {again && (
                <ConfirmSheet
                    title={t('setup.redownload.title', { name: names[again] ?? again })}
                    confirmLabel={t('setup.redownload.confirm')}
                    cancelLabel={t('setup.redownload.keep')}
                    onConfirm={() => { const id = again; setAgain(null); onDownloadAgain?.(id); }}
                    onCancel={() => setAgain(null)}
                >
                    <p>{t('setup.redownload.body', { name: names[again] ?? again, size: againSize != null ? formatSize(againSize) : '' })}</p>
                </ConfirmSheet>
            )}
        </>
    );
}
