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
}

/** Everything that comes to this PC: what, how big, and from where. Nothing hidden. */
export function DownloadContents({ plan, run, models = [] }: Props) {
    const t = useT();
    return (
        <ul className="setup-contents">
            {(plan.components ?? []).map((c) => {
                const state = run?.components?.[c.id] as RunState;
                return (
                    <li key={c.id} className={`setup-content is-${state ?? (c.installed ? 'done' : 'waiting')}`}>
                        <span className="setup-content-icon"><Icon state={state ?? (c.installed ? 'done' : undefined)} /></span>
                        <span className="setup-content-main">
                            <span className="setup-content-name">{t(`setup.component.${c.id}`)}</span>
                            <span className="setup-content-note">{t(`setup.component.${c.id}Note`)}</span>
                            <span className="setup-content-from">{t('setup.contents.from')} <bdi dir="ltr">{c.source}</bdi></span>
                        </span>
                        <span className="setup-content-side">
                            <bdi dir="ltr">{sizeText(c.size_gb, t)}</bdi>
                            <span>{stateText(c, state, t)}</span>
                        </span>
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
