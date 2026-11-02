// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { useT, type Translate } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
import type { EngineInstallError, EngineInstallReason, EngineInstallStatus } from '../types/agent';
import './EngineInstallScreen.css';

const AGENT_REFRESH_STAGE = 'agent-refresh';

/** Keyed by the installer's STAGES values, plus main's STARTING_STAGE. */
export const STAGE_LABELS: Readonly<Record<string, MessageKey>> = {
    'pre-checks': 'engine.stage.preChecks',
    'ensure-uv': 'engine.stage.ensureUv',
    'create-venv': 'engine.stage.createVenv',
    'install-package': 'engine.stage.installPackage',
    verify: 'engine.stage.verify',
    [AGENT_REFRESH_STAGE]: 'engine.stage.agentRefresh',
    start: 'engine.stage.start',
};

const REASON_HINTS: Readonly<Record<EngineInstallReason, MessageKey>> = {
    disk: 'engine.hint.disk',
    network: 'engine.hint.network',
    tls: 'engine.hint.tls',
    locked: 'engine.hint.locked',
};

function stageLabel(stage: string | null, t: Translate): string {
    if (!stage) return t('engine.stage.neutral');
    const key = STAGE_LABELS[stage];
    if (key) return t(key);
    console.warn(`[EngineInstallScreen] install stage "${stage}" has no label`);
    return t('engine.stage.neutral');
}

function failureHint(error: EngineInstallError, t: Translate): string {
    if (error.reason) return t(REASON_HINTS[error.reason]);
    return t(error.stage === AGENT_REFRESH_STAGE ? 'engine.hint.agent' : 'engine.hint.other');
}

function Progress({ status }: { status: EngineInstallStatus }) {
    const t = useT();
    const label = stageLabel(status.stage, t);
    return (
        <div className="engine-progress" role="status">
            <div className="engine-progress-head">
                <span>{label}</span>
                {status.percent !== null && <bdi dir="ltr">{status.percent}%</bdi>}
            </div>
            {/* No value means an indeterminate bar, for a stage with no percent to report. */}
            <progress className="engine-bar" aria-label={label} max={100} value={status.percent ?? undefined} />
        </div>
    );
}

function Failure({ error, busy, notice, onRetry, onContinue, onOpenLog }: {
    error: EngineInstallError;
    busy: boolean;
    notice: string | null;
    onRetry: () => void;
    onContinue: () => void;
    onOpenLog: () => void;
}) {
    const t = useT();
    return (
        <div className="engine-failure">
            <div role="alert">
                <p className="engine-hint">{failureHint(error, t)}</p>
                <p className="engine-error"><bdi dir="ltr">{error.message}</bdi></p>
            </div>
            <button type="button" className="btn-primary engine-retry" disabled={busy} onClick={onRetry} autoFocus>
                {t('engine.tryAgain')}
            </button>
            {error.stage === AGENT_REFRESH_STAGE && (
                <button type="button" className="btn-secondary engine-retry" disabled={busy} onClick={onContinue}>
                    {t('engine.continueWithout')}
                </button>
            )}
            {notice && <p className="engine-error" role="alert"><bdi dir="ltr">{notice}</bdi></p>}
            <details className="engine-details">
                <summary>{t('engine.details')}</summary>
                <pre className="engine-log" dir="ltr">{error.logTail.join('\n')}</pre>
                <button type="button" className="btn-secondary" onClick={onOpenLog}>{t('engine.openLog')}</button>
            </details>
        </div>
    );
}

/**
 * What the desktop window shows while main installs or updates the backend.
 * main.tsx renders it in place of the app; main then reloads the page into the app.
 */
export function EngineInstallScreen() {
    const t = useT();
    const [status, setStatus] = useState<EngineInstallStatus>({
        phase: 'running', stage: null, percent: null, message: '', error: null,
    });
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);

    const fail = useCallback((err: unknown) => {
        console.error('[EngineInstallScreen] the desktop app did not answer', err);
        setNotice(err instanceof Error ? err.message : String(err));
    }, []);

    useEffect(() => {
        const bridge = window.gaiaInstall!;
        const unsubscribe = bridge.onProgress(setStatus);
        bridge.status().then(setStatus).catch(fail);
        return unsubscribe;
    }, [fail]);

    const retry = useCallback(() => {
        setBusy(true);
        setNotice(null);
        window.gaiaInstall!.retry().catch(fail).finally(() => setBusy(false));
    }, [fail]);

    const continueAnyway = useCallback(() => {
        setBusy(true);
        setNotice(null);
        window.gaiaInstall!.continueWithoutAgentUpdate().catch(fail).finally(() => setBusy(false));
    }, [fail]);

    const openLog = useCallback(() => {
        setNotice(null);
        window.gaiaInstall!.openLogFile()
            .then((result) => { if (!result.ok) setNotice(result.message); })
            .catch(fail);
    }, [fail]);

    const { error } = status;
    const failed = status.phase === 'failed' && error;
    const title = !failed
        ? t('engine.title')
        : t(error.stage === AGENT_REFRESH_STAGE ? 'engine.failed.agentTitle' : 'engine.failed.title');

    return (
        <div className="app">
            <main className="engine">
                <div className="engine-inner">
                    <h1 className="engine-title">{title}</h1>
                    {failed ? (
                        <Failure error={error} busy={busy} notice={notice} onRetry={retry} onContinue={continueAnyway} onOpenLog={openLog} />
                    ) : (
                        <>
                            <p className="engine-lede">{t('engine.lede')}</p>
                            <Progress status={status} />
                            {notice && <p className="engine-error" role="alert"><bdi dir="ltr">{notice}</bdi></p>}
                        </>
                    )}
                </div>
            </main>
        </div>
    );
}
