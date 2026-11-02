// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { Check, Circle, Loader2, Minus, X } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import type { SetupCatalogEntry, SetupRunStatus, SetupTest } from '../../types';
import { ConfirmSheet } from './ConfirmSheet';
import { formatSize } from './format';

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
    onChooseAnother: () => void;
    onDownloadAgain: (id: string) => void;
    busy: boolean;
}

/** "Test": each job runs for real, and setup is ready only when they pass. */
export function TestStep({ run, expected, names, catalog, onRetry, onChooseAnother, onDownloadAgain, busy }: Props) {
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
    const broken = [...new Set(required.filter((r) => r.kind === 'broken' && r.subject).map((r) => r.subject as string))];
    const [again, setAgain] = useState<string | null>(null);
    const againSize = catalog.find((e) => e.id === again)?.full_size_gb;
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
                                {r.subject && <bdi className="setup-result-model"> {names[r.subject] ?? r.subject}</bdi>}
                            </span>
                            <span className="setup-result-value">{resultText(r, t)}</span>
                            {r.error && <p className="setup-result-error">{r.error}</p>}
                        </li>
                    ))}
                    {waiting.map((job) => (
                        <li key={job} className="setup-result is-waiting">
                            <Icon state="waiting" />
                            <span className="setup-result-label">{jobLabel(job, t)}</span>
                            <span className="setup-result-value">{t('setup.test.waiting')}</span>
                        </li>
                    ))}
                </ul>
            </section>
            {stopped && (
                <div className="setup-failure" role="alert">
                    <p>{required.some((r) => r.kind === 'memory') ? t('setup.test.failedMemory') : t('setup.test.failedHelp')}</p>
                    <div className="setup-row">
                        <button type="button" className="btn-primary" onClick={onRetry} disabled={busy}>{t('setup.test.tryAgain')}</button>
                        <button type="button" className="btn-secondary" onClick={onChooseAnother} disabled={busy}>{t('setup.test.chooseAnother')}</button>
                        {broken.map((id) => (
                            <button key={id} type="button" className="btn-secondary setup-wrap-btn" onClick={() => setAgain(id)} disabled={busy}>
                                {t('setup.test.downloadAgain', { name: names[id] ?? id })}
                            </button>
                        ))}
                    </div>
                </div>
            )}
            {again && (
                <ConfirmSheet
                    title={t('setup.redownload.title', { name: names[again] ?? again })}
                    confirmLabel={t('setup.redownload.confirm')}
                    cancelLabel={t('setup.redownload.keep')}
                    onConfirm={() => { const id = again; setAgain(null); onDownloadAgain(id); }}
                    onCancel={() => setAgain(null)}
                >
                    <p>{t('setup.redownload.body', { name: names[again] ?? again, size: againSize != null ? formatSize(againSize) : '' })}</p>
                </ConfirmSheet>
            )}
        </>
    );
}
