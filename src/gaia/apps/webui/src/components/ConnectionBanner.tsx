// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertTriangle, Cpu, Download, Layers, Loader2, WifiOff, X } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { MIN_CONTEXT_SIZE, DEFAULT_MODEL_NAME } from '../utils/constants';
import { useModelActions } from '../hooks/useModelActions';
import './ConnectionBanner.css';

/**
 * Banner shown when the backend is unreachable, Lemonade Server is not running,
 * the required model is not downloaded, the wrong model is loaded, or the
 * context window is too small. Provides clear messaging and actionable hints.
 */
export function ConnectionBanner({ onRetry }: { onRetry?: () => void }) {
    const { backendConnected, systemStatus, currentSessionId, messages } = useChatStore();
    const [dismissed, setDismissed] = useState(false);

    // Suppress the banner entirely while a conversation is in progress.
    // The banner will not show, will not update its internal state, and will
    // resume normal behaviour only when there is no active conversation.
    const hasActiveConversation = !!currentSessionId && messages.length > 0;

    const modelName = systemStatus?.default_model_name ?? DEFAULT_MODEL_NAME;
    const { isLoadingModel, isDownloadingModel, loadModel, downloadModel } = useModelActions(modelName);

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

    // Case 0: Boot-time initialization in progress.
    // Takes priority over all other cases (including dismiss and active-conversation suppression)
    // because the system is not yet ready to serve requests.
    if (systemStatus?.init_state === 'initializing') {
        const runningTask = systemStatus.init_tasks?.find(t => t.status === 'running');
        const label = runningTask?.name || 'Preparing AI system';
        return (
            <div className="connection-banner connection-banner--init" role="status">
                <div className="connection-banner__icon">
                    <Loader2 size={16} className="connection-banner__spinner" />
                </div>
                <div className="connection-banner__text">
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
                <div className="connection-banner__text">
                    Cannot connect to GAIA Agent UI server.{' '}
                    <span className="connection-banner__hint">
                        Start it with: <code>gaia chat --ui</code>
                    </span>
                </div>
                {onRetry && (
                    <button className="connection-banner__retry" onClick={onRetry}>
                        Retry
                    </button>
                )}
            </div>
        );
    }

    // Case 2: Backend is up but Lemonade Server is not running
    if (systemStatus && !systemStatus.lemonade_running) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text">
                    LLM server is not responding &mdash; it may be busy or not running.{' '}
                    <span className="connection-banner__hint">
                        If not started, run: <code>lemonade-server serve</code>
                    </span>
                </div>
                {onRetry && (
                    <button className="connection-banner__retry" onClick={onRetry}>
                        Check again
                    </button>
                )}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    // Case 3: Lemonade is running but the required default model is not downloaded
    if (
        systemStatus &&
        systemStatus.lemonade_running &&
        !systemStatus.model_loaded &&
        systemStatus.model_downloaded === false
    ) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Download size={16} />
                </div>
                <div className="connection-banner__text">
                    Required model <strong>{modelName}</strong> is not downloaded (~25 GB).
                </div>
                {isDownloadingModel ? (
                    <span className="connection-banner__loading">
                        <Loader2 size={13} className="connection-banner__spinner" />
                        Downloading…
                    </span>
                ) : (
                    <button
                        className="connection-banner__retry"
                        onClick={() => downloadModel(false)}
                    >
                        Download
                    </button>
                )}
                {onRetry && !isDownloadingModel && (
                    <button className="connection-banner__retry connection-banner__retry--secondary" onClick={onRetry}>
                        Recheck
                    </button>
                )}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    // Case 4: A model is loaded but it is not the expected one.
    // Suppressed when the embedding model is the currently active model — that
    // is a normal transient state after indexing. The backend pre-flight check
    // in _chat_helpers.py loads the correct LLM before the first query executes.
    if (
        systemStatus &&
        systemStatus.lemonade_running &&
        systemStatus.model_loaded &&
        systemStatus.expected_model_loaded === false &&
        !systemStatus.embedding_model_loaded
    ) {
        const currentModel = systemStatus.model_loaded;
        const contextAlsoSmall = systemStatus.context_size_sufficient === false;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Cpu size={16} />
                </div>
                <div className="connection-banner__text">
                    Wrong model loaded: <strong>{currentModel}</strong>.{' '}
                    GAIA Chat requires <strong>{modelName}</strong>.
                    {contextAlsoSmall && (
                        <>{' '}Context window is also too small.</>
                    )}
                </div>
                {isLoadingModel ? (
                    <span className="connection-banner__loading">
                        <Loader2 size={13} className="connection-banner__spinner" />
                        Loading…
                    </span>
                ) : (
                    <button
                        className="connection-banner__retry"
                        onClick={() => loadModel()}
                    >
                        Load Now
                    </button>
                )}
                {onRetry && !isLoadingModel && (
                    <button className="connection-banner__retry connection-banner__retry--secondary" onClick={onRetry}>
                        Recheck
                    </button>
                )}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    // Case 5: Model is loaded but context window is too small
    if (
        systemStatus &&
        systemStatus.lemonade_running &&
        systemStatus.model_loaded &&
        systemStatus.context_size_sufficient === false
    ) {
        const current = systemStatus.model_context_size ?? 0;
        const lemonadeUI = systemStatus.lemonade_url ?? 'http://localhost:8000';
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Layers size={16} />
                </div>
                <div className="connection-banner__text">
                    LLM context window is too small ({current.toLocaleString()} tokens;{' '}
                    {MIN_CONTEXT_SIZE.toLocaleString()} required).{' '}
                    <span className="connection-banner__hint">
                        In{' '}
                        <a
                            className="connection-banner__link"
                            href={lemonadeUI}
                            target="_blank"
                            rel="noreferrer"
                        >
                            Lemonade
                        </a>
                        , set ctx&#8209;size to {MIN_CONTEXT_SIZE.toLocaleString()}, or
                        restart with:{' '}
                        <code>
                            lemonade-server serve --ctx-size {MIN_CONTEXT_SIZE}
                        </code>
                    </span>
                </div>
                {onRetry && (
                    <button className="connection-banner__retry" onClick={onRetry}>
                        Check again
                    </button>
                )}
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    return null;
}
