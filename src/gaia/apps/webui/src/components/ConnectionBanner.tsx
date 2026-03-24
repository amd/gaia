// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertTriangle, Cpu, Download, Layers, Loader2, WifiOff, X } from 'lucide-react';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import './ConnectionBanner.css';

/** Minimum LLM context window in tokens required for reliable agent operation. */
const MIN_CONTEXT_SIZE = 32768;

/**
 * Banner shown when the backend is unreachable, Lemonade Server is not running,
 * the required model is not downloaded, the wrong model is loaded, or the
 * context window is too small. Provides clear messaging and actionable hints.
 */
export function ConnectionBanner({ onRetry }: { onRetry?: () => void }) {
    const { backendConnected, systemStatus } = useChatStore();
    const [dismissed, setDismissed] = useState(false);
    const [isLoadingModel, setIsLoadingModel] = useState(false);
    const [isDownloadingModel, setIsDownloadingModel] = useState(false);

    const loadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const downloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => {
            if (loadTimerRef.current) clearTimeout(loadTimerRef.current);
            if (downloadTimerRef.current) clearTimeout(downloadTimerRef.current);
        };
    }, []);

    const handleLoadModel = useCallback(async (modelName: string) => {
        setIsLoadingModel(true);
        try {
            await api.loadModel(modelName, MIN_CONTEXT_SIZE);
            if (loadTimerRef.current) clearTimeout(loadTimerRef.current);
            loadTimerRef.current = setTimeout(() => setIsLoadingModel(false), 300_000);
        } catch (_err) {
            // Loading is async on backend; errors are logged server-side
            setIsLoadingModel(false);
        }
    }, []);

    const handleDownloadModel = useCallback(async (modelName: string) => {
        setIsDownloadingModel(true);
        try {
            await api.downloadModel(modelName);
            if (downloadTimerRef.current) clearTimeout(downloadTimerRef.current);
            downloadTimerRef.current = setTimeout(() => setIsDownloadingModel(false), 1_800_000);
        } catch (_err) {
            // Download is async on backend; errors are logged server-side
            setIsDownloadingModel(false);
        }
    }, []);

    // Track previous warning-worthy states so the banner reappears when
    // a new issue is detected after being dismissed.
    const prevLemonadeRef = useRef(systemStatus?.lemonade_running);
    const prevModelDownloadedRef = useRef(systemStatus?.model_downloaded);
    const prevContextSufficientRef = useRef(systemStatus?.context_size_sufficient);
    const prevExpectedModelRef = useRef(systemStatus?.expected_model_loaded);

    useEffect(() => {
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
    }, [systemStatus]);

    // Nothing to show
    if (dismissed) return null;

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
        const modelName = systemStatus.default_model_name ?? 'Qwen3.5-35B-A3B-GGUF';
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
                        onClick={() => handleDownloadModel(modelName)}
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

    // Case 5: A model is loaded but it is not the expected one.
    // Show a combined message when the context window is also too small, since
    // loading the correct model will likely fix both issues at once.
    if (
        systemStatus &&
        systemStatus.lemonade_running &&
        systemStatus.model_loaded &&
        systemStatus.expected_model_loaded === false
    ) {
        const currentModel = systemStatus.model_loaded;
        const expectedModel = systemStatus.default_model_name ?? 'Qwen3.5-35B-A3B-GGUF';
        const contextAlsoSmall = systemStatus.context_size_sufficient === false;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Cpu size={16} />
                </div>
                <div className="connection-banner__text">
                    Wrong model loaded: <strong>{currentModel}</strong>.{' '}
                    GAIA Chat requires <strong>{expectedModel}</strong>.
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
                        onClick={() => handleLoadModel(expectedModel)}
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

    // Case 4: Model is loaded but context window is too small
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
