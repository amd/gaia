// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { useT } from '../i18n/useT';
import { useBackendStore } from '../stores/backendStore';

/** Past this, the wait shows how long it has been. */
export const STILL_STARTING_AFTER_MS = 10_000;

/** The launch wait: a spinner, then how long it has taken once it runs long. */
export function StartupWaiting() {
    const t = useT();
    const [startedAt] = useState(() => Date.now());
    const [now, setNow] = useState(startedAt);
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);
    const elapsed = now - startedAt;
    return (
        <main className="startup" role="status" aria-label={t('app.startingAria')}>
            <div className="loading-spinner" aria-hidden="true" />
            <p className="startup-text" dir="auto">{t('app.starting')}</p>
            {elapsed >= STILL_STARTING_AFTER_MS && (
                <p className="startup-elapsed" dir="auto">
                    {t('app.stillStarting')} · <bdi dir="ltr">{t('app.startingSeconds', { s: Math.floor(elapsed / 1000) })}</bdi>
                </p>
            )}
        </main>
    );
}

/** GAIA's backend never answered at launch: say so, offer what can fix it, and keep the real error. */
export function StartupFailed({ error, onCheckAgain }: { error: string; onCheckAgain: () => void }) {
    const t = useT();
    const lifecycle = useBackendStore((s) => s.lifecycle);
    const restart = useBackendStore((s) => s.restart);
    const restartError = useBackendStore((s) => s.restartError);
    // The desktop app can only restart a backend it has given up on.
    const canRestart = lifecycle?.status === 'stopped' || lifecycle?.status === 'restarting';
    const restarting = lifecycle?.status === 'restarting';
    const reasons = [...new Set([restartError, lifecycle?.lastError, error].filter((r): r is string => !!r))];
    return (
        <main className="startup startup--failed" role="alert">
            <div className="startup-failed">
                <h1 className="startup-failed-title" dir="auto">{t('startup.failed.title')}</h1>
                <p className="startup-failed-hint" dir="auto">{t(canRestart ? 'startup.failed.hintRestart' : 'startup.failed.hintReopen')}</p>
                <div className="startup-failed-actions">
                    {canRestart && (
                        <button type="button" className="btn-primary" disabled={restarting} onClick={() => void restart()}>
                            {t(restarting ? 'banner.restartingShort' : 'banner.restartButton')}
                        </button>
                    )}
                    <button type="button" className={canRestart ? 'btn-secondary' : 'btn-primary'} onClick={onCheckAgain}>
                        {t('setup.unknown.retry')}
                    </button>
                </div>
                <details className="startup-failed-details">
                    <summary>{t('startup.failed.details')}</summary>
                    <pre className="startup-failed-log" dir="ltr">{reasons.join('\n')}</pre>
                </details>
            </div>
        </main>
    );
}
