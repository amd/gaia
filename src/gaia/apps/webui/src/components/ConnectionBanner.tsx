// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertTriangle, Download, Layers, Loader2, WifiOff, X } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useBackendStore } from '../stores/backendStore';
import type { BackendLifecycle } from '../types/agent';
import { useT } from '../i18n/useT';
import { isMessageKey } from '../i18n/registry';
import { namesSetupCommand, needsLocalSetup, SetUpLocalButton } from './settings/ErrorNotice';
import { MIN_CONTEXT_SIZE, DEFAULT_MODEL_NAME, GB_UNIT } from '../utils/constants';
import { ref } from '../i18n/registry';
import { useModelActions } from '../hooks/useModelActions';
import { isStarting, modelServerHealth } from '../services/statusPoll';
import { displayModelName, useModelNames, useModelStore } from '../stores/modelStore';
import './ConnectionBanner.css';

/**
 * Banner shown when the backend is unreachable, Lemonade Server is not running,
 * the required model is not downloaded, the wrong model is loaded, or the
 * context window is too small. Provides clear messaging and actionable hints.
 */
/** A retry that says it is checking until the check answers. */
function RetryButton({ onRetry, label, busyLabel }: {
    onRetry: () => void | Promise<void>;
    label: string;
    busyLabel?: string;
}) {
    const t = useT();
    const [checking, setChecking] = useState(false);
    const run = () => {
        setChecking(true);
        Promise.resolve(onRetry()).finally(() => setChecking(false));
    };
    return (
        <button className="connection-banner__retry" onClick={run} disabled={checking}>
            {checking ? (busyLabel ?? t('banner.checking')) : label}
        </button>
    );
}

/** Seconds as m:ss. */
function formatElapsed(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/** The desktop app's backend crashed: it is restarting, or gave up and waits for Restart GAIA. */
function BackendRestartBanner({ lifecycle }: { lifecycle: BackendLifecycle }) {
    const t = useT();
    const restart = useBackendStore((s) => s.restart);
    const restartError = useBackendStore((s) => s.restartError);
    const restarting = lifecycle.status === 'restarting';
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!restarting) return;
        setNow(Date.now());
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, [restarting]);

    if (restarting) {
        const elapsed = lifecycle.since ? Math.max(0, Math.floor((now - lifecycle.since) / 1000)) : 0;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Loader2 size={16} className="connection-banner__spinner" />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.restarting')}{' '}
                    <bdi dir="ltr" className="connection-banner__timer">{formatElapsed(elapsed)}</bdi>{' '}
                    <span className="connection-banner__hint">{t('banner.restartingHint')}</span>
                </div>
            </div>
        );
    }
    return (
        <div className="connection-banner connection-banner--error" role="alert">
            <div className="connection-banner__icon">
                <AlertTriangle size={16} />
            </div>
            <div className="connection-banner__text" dir="auto">
                {t('banner.restartStopped')}{' '}
                <span className="connection-banner__hint">
                    {restartError ? t('banner.restartFailed', { error: restartError }) : t('banner.restartStoppedHint')}
                </span>
            </div>
            <RetryButton onRetry={restart} label={t('banner.restartButton')} busyLabel={t('banner.restartingShort')} />
        </div>
    );
}

export function ConnectionBanner({ onRetry, backendSlow = false, backendError = null }: {
    onRetry?: () => void | Promise<void>;
    /** Status requests keep timing out: the backend is busy, not gone. */
    backendSlow?: boolean;
    /** The backend answered the status request with this HTTP error. */
    backendError?: string | null;
}) {
    const t = useT();
    const { backendConnected, systemStatus, currentSessionId, messages } = useChatStore();
    const lifecycle = useBackendStore((s) => s.lifecycle);
    const [dismissed, setDismissed] = useState(false);

    // Suppress the banner entirely while a conversation is in progress.
    // The banner will not show, will not update its internal state, and will
    // resume normal behaviour only when there is no active conversation.
    const hasActiveConversation = !!currentSessionId && messages.length > 0;

    const modelName = systemStatus?.default_model_name ?? DEFAULT_MODEL_NAME;
    const { isDownloadingModel, downloadError, downloadModel } = useModelActions(modelName);
    const pickLoad = useModelStore((s) => s.pickLoad);
    const retryPickLoad = useModelStore((s) => s.retryPickLoad);
    const dismissPickLoad = useModelStore((s) => s.dismissPickLoad);
    useModelNames();

    // Track previous warning-worthy states so the banner reappears when
    // a new issue is detected after being dismissed.
    const prevLemonadeRef = useRef(systemStatus?.lemonade_running);
    const prevModelDownloadedRef = useRef(systemStatus?.model_downloaded);
    const prevContextSufficientRef = useRef(systemStatus?.context_size_sufficient);
    const prevExpectedModelRef = useRef(systemStatus?.expected_model_loaded);

    useEffect(() => {
        // Freeze all state tracking while a conversation is active.
        if (hasActiveConversation) return;

        const lemonade = systemStatus?.lemonade_running;
        const modelDownloaded = systemStatus?.model_downloaded;
        const contextSufficient = systemStatus?.context_size_sufficient;
        const expectedModel = systemStatus?.expected_model_loaded;

        let shouldReset = false;

        if (prevLemonadeRef.current !== lemonade) {
            prevLemonadeRef.current = lemonade;
            if (lemonade === false) shouldReset = true;
        }
        if (prevModelDownloadedRef.current !== modelDownloaded) {
            prevModelDownloadedRef.current = modelDownloaded;
            if (modelDownloaded === false) shouldReset = true;
        }
        if (prevContextSufficientRef.current !== contextSufficient) {
            prevContextSufficientRef.current = contextSufficient;
            if (contextSufficient === false) shouldReset = true;
        }
        if (prevExpectedModelRef.current !== expectedModel) {
            prevExpectedModelRef.current = expectedModel;
            if (expectedModel === false) shouldReset = true;
        }

        if (shouldReset) setDismissed(false);
    }, [systemStatus, hasActiveConversation]);

    const activeProgress = systemStatus?.download_progress ?? null;
    const isActiveDownload =
        isDownloadingModel ||
        activeProgress?.state === 'downloading' ||
        activeProgress?.state === 'starting';
    const [downloadErrorDismissed, setDownloadErrorDismissed] = useState(false);
    // A new download's failure must show even if the last one was dismissed.
    useEffect(() => {
        if (isActiveDownload) setDownloadErrorDismissed(false);
    }, [isActiveDownload]);

    // A crashed backend outranks everything, even a chat in progress: nothing answers until it's back.
    if (lifecycle && lifecycle.status !== 'running') return <BackendRestartBanner lifecycle={lifecycle} />;

    // ── Top-priority: an active model download ──────────────────────────
    // Render the download banner BEFORE every other check (init,
    // dismiss, active-conversation, lemonade-down, etc.). A model pull
    // takes minutes and the user needs to see it progressing — burying
    // it under a generic "Preparing AI system…" or even a dismissed
    // state would silently look like the app froze. Same reasoning for
    // suppressing during an active conversation: if the download fails
    // mid-chat we still want to surface the failure prominently.
    const downloadName = activeProgress?.model_name || modelName;
    if (isActiveDownload || (activeProgress?.state === 'error' && !downloadErrorDismissed)) {
        const sizeHint = activeProgress?.total_bytes
            ? `${(activeProgress.total_bytes / 1e9).toFixed(1)} ${GB_UNIT}`
            : ref('banner.download.sizeUnknown');
        const isError = activeProgress?.state === 'error';
        return (
            <div
                className={`connection-banner ${
                    isError ? 'connection-banner--error' : 'connection-banner--warning'
                }`}
                role={isError ? 'alert' : 'status'}
            >
                <div className="connection-banner__icon">
                    {isError ? <AlertTriangle size={16} /> : <Download size={16} />}
                </div>
                <div className="connection-banner__text" dir="auto">
                    {isError ? (
                        <>
                            {t('banner.download.failed', { name: downloadName })}{' '}
                            {(downloadError ?? activeProgress?.message) && (
                                <span className="connection-banner__hint">{downloadError ?? activeProgress?.message}</span>
                            )}
                        </>
                    ) : (
                        <>
                            {t('banner.download.running', { name: downloadName, size: sizeHint })}
                            {activeProgress?.state === 'downloading' && activeProgress.file && (
                                <>
                                    {' '}
                                    <span className="connection-banner__hint">
                                        {t('banner.download.file', { index: activeProgress.file_index, total: activeProgress.total_files })} <code>{activeProgress.file}</code>
                                    </span>
                                </>
                            )}
                        </>
                    )}
                </div>
                {isError ? (
                    <button
                        className="connection-banner__retry"
                        onClick={() => downloadModel(false, downloadName, !systemStatus?.model_loaded)}
                    >
                        {t('docs.retry')}
                    </button>
                ) : (
                    <span className="connection-banner__loading connection-banner__progress">
                        {activeProgress && activeProgress.state === 'downloading' && activeProgress.total_bytes > 0 ? (
                            <>
                                <span
                                    className="connection-banner__progress-bar"
                                    role="progressbar"
                                    aria-valuenow={activeProgress.percent}
                                    aria-valuemin={0}
                                    aria-valuemax={100}
                                    aria-label={t('banner.download.progressAria')}
                                >
                                    <span
                                        className="connection-banner__progress-fill"
                                        style={{ width: `${activeProgress.percent}%` }}
                                    />
                                </span>
                                <span className="connection-banner__progress-label">
                                    <bdi dir="ltr">
                                        {`${activeProgress.percent}% · ${(activeProgress.downloaded_bytes / 1e9).toFixed(2)} / ${(activeProgress.total_bytes / 1e9).toFixed(2)} ${GB_UNIT}`}
                                    </bdi>
                                </span>
                            </>
                        ) : (
                            <>
                                <Loader2 size={13} className="connection-banner__spinner" />
                                {activeProgress?.state === 'starting' ? activeProgress.message ?? t('update.picker.starting') : t('banner.download.progress')}
                            </>
                        )}
                    </span>
                )}
                {/* No dismiss while a download is in flight — accidentally
                    closing it during a 5-minute pull is the exact UX we're
                    trying to fix. The error path is dismissable so the user
                    can clear stale failures. */}
                {isError && (
                    <button
                        className="connection-banner__dismiss"
                        onClick={() => setDownloadErrorDismissed(true)}
                        aria-label={t('banner.dismiss')}
                    >
                        <X size={14} />
                    </button>
                )}
            </div>
        );
    }

    // A model picked from the list is loading, or its load failed. Shown mid-chat
    // too: the pick is what the next message waits on.
    if (pickLoad) {
        const name = displayModelName(pickLoad.model);
        if (pickLoad.error) {
            return (
                <div className="connection-banner connection-banner--error" role="alert">
                    <div className="connection-banner__icon">
                        <AlertTriangle size={16} />
                    </div>
                    <div className="connection-banner__text" dir="auto">
                        {t('banner.pickLoad.failed', { model: name })}{' '}
                        <span className="connection-banner__hint" dir="auto">
                            {pickLoad.error.key ? t(pickLoad.error.key, { reason: pickLoad.error.reason }) : pickLoad.error.reason}
                        </span>
                    </div>
                    <RetryButton onRetry={retryPickLoad} label={t('banner.pickLoad.retry')} />
                    <button
                        className="connection-banner__dismiss"
                        onClick={dismissPickLoad}
                        aria-label={t('banner.dismiss')}
                    >
                        <X size={14} />
                    </button>
                </div>
            );
        }
        return (
            <div className="connection-banner connection-banner--init" role="status">
                <div className="connection-banner__icon">
                    <Loader2 size={16} className="connection-banner__spinner" />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.pickLoad.loading', { model: name })}{' '}
                    <bdi dir="ltr">{t('banner.pickLoad.seconds', { s: pickLoad.seconds })}</bdi>{' '}
                    <span className="connection-banner__hint">{t('banner.pickLoad.canType')}</span>
                </div>
            </div>
        );
    }

    // Case 0: Boot-time initialization in progress.
    // Takes priority over all other cases (including dismiss and active-conversation suppression)
    // because the system is not yet ready to serve requests. A status the backend
    // can't refresh (slow or failing) is not proof the boot is still running.
    if (isStarting(systemStatus) && !backendSlow && !backendError) {
        // The task's English `name` is a log label; only its `step` is shown, translated.
        const step = systemStatus?.init_tasks?.find(t => t.status === 'running')?.step;
        const stepKey = `banner.boot.${step}`;
        const label = step && isMessageKey(stepKey) ? t(stepKey) : t('banner.boot.generic');
        return (
            <div className="connection-banner connection-banner--init" role="status">
                <div className="connection-banner__icon">
                    <Loader2 size={16} className="connection-banner__spinner" />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {label}<span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
                </div>
            </div>
        );
    }

    // Nothing to show
    if (dismissed) return null;

    // Hidden while a conversation is active (user is in a chat with messages).
    if (hasActiveConversation) return null;

    // Case 1: Backend API is unreachable
    if (!backendConnected) {
        return (
            <div className="connection-banner connection-banner--error" role="alert">
                <div className="connection-banner__icon">
                    <WifiOff size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.unreachable')}{' '}
                    <span className="connection-banner__hint">{t('banner.unreachableHint')}</span>
                </div>
                {onRetry && <RetryButton onRetry={onRetry} label={t('docs.retry')} />}
            </div>
        );
    }

    // Case 1a: the backend answers, but with an error.
    if (backendError) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.backendError', { error: backendError })}{' '}
                    <span className="connection-banner__hint">
                        {t('banner.backendErrorHint')}
                    </span>
                </div>
                {onRetry && <RetryButton onRetry={onRetry} label={t('settings.key.checkAgain')} />}
            </div>
        );
    }

    // Case 1b: the backend answers, but too slowly to read its status.
    if (backendSlow) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.slow')}{' '}
                    <span className="connection-banner__hint">
                        {t('banner.slowHint')}
                    </span>
                </div>
                {onRetry && <RetryButton onRetry={onRetry} label={t('settings.key.checkAgain')} />}
            </div>
        );
    }

    // Case 1b: ~/.gaia/config.json exists but could not be loaded.
    // Not dismissable: the shared dismiss flag would also hide a Lemonade-down banner.
    if (systemStatus?.config_error) {
        return (
            <div className="connection-banner connection-banner--error" role="alert">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.configError')}{' '}
                    <span className="connection-banner__hint">{systemStatus.config_error}</span>
                </div>
                {onRetry && <RetryButton onRetry={onRetry} label={t('settings.key.checkAgain')} />}
            </div>
        );
    }

    // Case 2: Backend is up but Lemonade Server is not running
    if (systemStatus && !systemStatus.lemonade_running) {
        // Listening but failing its health check is often just busy; the backend can't yet say which.
        const unresponsive = modelServerHealth(systemStatus) === 'unresponsive';
        // A server that listens is installed: setup can't fix a hang, so don't offer it.
        const setupNeeded = !unresponsive && needsLocalSetup(systemStatus);
        const startCommand = unresponsive && namesSetupCommand(systemStatus.start_command) ? null : systemStatus.start_command;
        const startInstruction = unresponsive && namesSetupCommand(systemStatus.start_instruction) ? null : systemStatus.start_instruction;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t(unresponsive ? 'banner.serverHung' : 'banner.serverStopped')}{' '}
                    {setupNeeded ? (
                        <span className="connection-banner__hint">{t('banner.finishSetup')}</span>
                    ) : startCommand ? (
                        <span className="connection-banner__hint">
                            {t(unresponsive ? 'banner.restartHint' : 'banner.startHint')}{' '}
                            <code dir="ltr">{startCommand}</code>
                        </span>
                    ) : startInstruction && (
                        <span className="connection-banner__hint">{startInstruction}</span>
                    )}
                </div>
                {setupNeeded && <SetUpLocalButton className="connection-banner__retry" />}
                {onRetry && <RetryButton onRetry={onRetry} label={t('settings.key.checkAgain')} />}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t('banner.dismiss')}
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    // Case 3: the loaded model's context window is too small.
    if (
        systemStatus &&
        systemStatus.lemonade_running &&
        systemStatus.model_loaded &&
        systemStatus.context_size_sufficient === false
    ) {
        const current = systemStatus.model_context_size ?? 0;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Layers size={16} />
                </div>
                <div className="connection-banner__text" dir="auto">
                    {t('banner.contextTooSmall', { current: current.toLocaleString(), required: MIN_CONTEXT_SIZE.toLocaleString() })}{' '}
                    <span className="connection-banner__hint">
                        {t('banner.contextTooSmallHint')}
                    </span>
                </div>
                {onRetry && <RetryButton onRetry={onRetry} label={t('settings.key.checkAgain')} />}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t('banner.dismiss')}
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    return null;
}
