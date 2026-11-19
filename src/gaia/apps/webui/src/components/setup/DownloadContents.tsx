// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Check, Circle, Loader2, X } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import type { SetupCatalogEntry, SetupComponent, SetupPlan, SetupRunStatus } from '../../types';
import { formatSize } from './format';

type RunState = 'active' | 'done' | 'failed' | undefined;

function stateText(c: SetupComponent, state: RunState, t: Translate): string {
    if (state === 'active') return t('setup.contents.working');
    if (state === 'done') return t('setup.contents.ready');
    if (state === 'failed') return t('setup.contents.failed');
    return c.installed ? t('setup.contents.alreadyHere') : t('setup.contents.waiting');
}

function sizeText(gb: number | null, t: Translate): string {
    return gb == null ? t('setup.contents.sizeOnDownload') : formatSize(gb);
}

function Icon({ state }: { state: RunState }) {
    if (state === 'done') return <Check size={13} aria-hidden="true" />;
    if (state === 'active') return <Loader2 size={13} className="spin" aria-hidden="true" />;
    if (state === 'failed') return <X size={13} aria-hidden="true" />;
    return <Circle size={13} aria-hidden="true" />;
}

interface Props {
    plan: SetupPlan;
    /** The run under way; without one this is the list before anything starts. */
    run?: SetupRunStatus | null;
    /** The models that will download, listed before anything starts. */
    models?: SetupCatalogEntry[];
    /** Runs setup again; a failed row offers it once the run has stopped. */
    onRetry?: () => void;
    retryDisabled?: boolean;
}

/** Why a piece failed, in a plain sentence, the run's own words behind Details. */
function FailedReason({ id, error, transient, onRetry, disabled }: { id: SetupComponent['id']; error: string | null | undefined; transient: boolean; onRetry?: () => void; disabled?: boolean }) {
    const t = useT();
    const name = t(`setup.component.${id}`);
    return (
        <div className="setup-content-failure" role="alert">
            <p>{t(transient ? 'setup.contents.failedNetwork' : 'setup.contents.failedOther', { name })}</p>
            {error && (
                <details className="dl-details">
                    <summary>{t('setup.progress.details')}</summary>
                    <pre dir="ltr">{error}</pre>
                </details>
            )}
            {onRetry && (
                <button type="button" className="btn-primary" onClick={onRetry} disabled={disabled} aria-label={t('setup.contents.retryNamed', { name })}>
                    {t('setup.progress.retry')}
                </button>
            )}
        </div>
    );
}

/** Everything that comes to this PC: what, how big, and from where. Nothing hidden. */
export function DownloadContents({ plan, run, models = [], onRetry, retryDisabled }: Props) {
    const t = useT();
    return (
        <ul className="setup-contents">
            {(plan.components ?? []).map((c) => {
                const reported = run?.components?.[c.id] as RunState;
                // A run that ended mid-step left that step unfinished, not still working.
                const ended = run != null && run.state !== 'running' && run.state !== 'verifying';
                const state = reported === 'active' && ended ? undefined : reported;
                return (
                    <li key={c.id} className={`setup-content is-${state ?? (c.installed ? 'done' : 'waiting')}`}>
                        <span className="setup-content-icon"><Icon state={state ?? (c.installed ? 'done' : undefined)} /></span>
                        <span className="setup-content-main">
                            <span className="setup-content-name">{t(`setup.component.${c.id}`)}</span>
                            <span className="setup-content-note">{t(`setup.component.${c.id}Note`)}</span>
                            {c.bundled
                                ? <span className="setup-content-from">{t('setup.contents.bundled')}</span>
                                : <span className="setup-content-from">{t('setup.contents.from')} <bdi dir="ltr">{c.source}</bdi></span>}
                        </span>
                        <span className="setup-content-side">
                            <bdi dir="ltr">{sizeText(c.size_gb, t)}</bdi>
                            <span>{stateText(c, state, t)}</span>
                        </span>
                        {state === 'failed' && ended && !run?.problem && <FailedReason id={c.id} error={run?.error} transient={run?.component_kinds?.[c.id] === 'transient'} onRetry={onRetry} disabled={retryDisabled} />}
                    </li>
                );
            })}
            {models.map((e) => (
                <li key={e.id} className="setup-content is-waiting">
                    <span className="setup-content-icon"><Icon state={undefined} /></span>
                    <span className="setup-content-main">
                        <span className="setup-content-name"><bdi>{e.name}</bdi></span>
                        <span className="setup-content-note">{e.roles.map((role) => t(`setup.purpose.${role}`)).join(' · ')}</span>
                        {e.source && <span className="setup-content-from">{t('setup.contents.from')} <bdi dir="ltr">{e.source}</bdi></span>}
                    </span>
                    <span className="setup-content-side">
                        <bdi dir="ltr">{sizeText(e.size_gb, t)}</bdi>
                    </span>
                </li>
            ))}
        </ul>
    );
}
