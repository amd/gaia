// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useRef, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import {
    DEFAULT_MODEL_NAME,
    LOAD_SPINNER_TIMEOUT_MS,
    DOWNLOAD_SPINNER_TIMEOUT_MS,
    MODEL_POLL_INTERVAL_MS,
} from '../utils/constants';

/**
 * Shared hook for model load/download operations with spinner state,
 * timer-guarded reset, and status polling for early completion detection.
 *
 * @param defaultModelName - Model name to use when none is provided to loadModel().
 * @param contextSize      - Context window size (tokens) to pass to load-model.
 *                           When not set, the server loads at the window a
 *                           chat on that model asks for.
 */
export function useModelActions(defaultModelName?: string, contextSize?: number) {
    const [isLoadingModel, setIsLoadingModel] = useState(false);
    const [isDownloadingModel, setIsDownloadingModel] = useState(false);
    // The server's reason a download could not start, shown next to the button.
    const [downloadError, setDownloadError] = useState<string | null>(null);

    const loadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const downloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const clearAllTimers = useCallback(() => {
        if (loadTimerRef.current) { clearTimeout(loadTimerRef.current); loadTimerRef.current = null; }
        if (downloadTimerRef.current) { clearTimeout(downloadTimerRef.current); downloadTimerRef.current = null; }
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }, []);

    useEffect(() => clearAllTimers, [clearAllTimers]);

    // Poll /api/system/status to detect when the model operation completes,
    // clearing the spinner as soon as the expected model is loaded.
    const startPolling = useCallback((modelName: string, operation: 'load' | 'download') => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = setInterval(async () => {
            try {
                const s = await api.getSystemStatus();
                if (operation === 'load') {
                    // Load succeeded if the expected model is now active
                    if (s.model_loaded?.toLowerCase() === modelName.toLowerCase()) {
                        setIsLoadingModel(false);
                        if (loadTimerRef.current) { clearTimeout(loadTimerRef.current); loadTimerRef.current = null; }
                        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                    }
                } else {
                    // Download succeeded if model_downloaded becomes true or model is loaded
                    if (s.model_downloaded === true || s.model_loaded?.toLowerCase() === modelName.toLowerCase()) {
                        setIsDownloadingModel(false);
                        if (downloadTimerRef.current) { clearTimeout(downloadTimerRef.current); downloadTimerRef.current = null; }
                        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                    }
                }
            } catch {
                // Status endpoint failed — keep polling
            }
        }, MODEL_POLL_INTERVAL_MS);
    }, []);

    const modelName = defaultModelName ?? DEFAULT_MODEL_NAME;

    // A refusal was about the old model; don't show it against the new one.
    useEffect(() => setDownloadError(null), [modelName]);

    const loadModel = useCallback(async (name?: string, ctxSize?: number) => {
        const target = name ?? modelName;
        const ctx = ctxSize ?? contextSize;
        setIsLoadingModel(true);
        try {
            if (!(await api.loadModelAskingFirst(target, ctx))) {
                setIsLoadingModel(false);
                return;
            }
            log.system.info(`Load model triggered: ${target} (ctx=${ctx ?? 'server default'})`);
            if (loadTimerRef.current) clearTimeout(loadTimerRef.current);
            loadTimerRef.current = setTimeout(() => setIsLoadingModel(false), LOAD_SPINNER_TIMEOUT_MS);
            startPolling(target, 'load');
        } catch (err) {
            log.system.error('Failed to trigger model load', err);
            setIsLoadingModel(false);
        }
    }, [modelName, contextSize, startPolling]);

    const downloadModel = useCallback(
        async (force = false, name?: string, loadAfter = false) => {
            const target = name ?? modelName;
            setIsDownloadingModel(true);
            setDownloadError(null);
            try {
                await api.downloadModel(target, force, loadAfter);
                log.system.info(`Download model triggered: ${target} (force=${force})`);
                if (downloadTimerRef.current) clearTimeout(downloadTimerRef.current);
                downloadTimerRef.current = setTimeout(
                    () => setIsDownloadingModel(false),
                    DOWNLOAD_SPINNER_TIMEOUT_MS,
                );
                startPolling(target, 'download');
            } catch (err) {
                log.system.error('Failed to trigger model download', err);
                setDownloadError(err instanceof Error ? err.message : String(err));
                setIsDownloadingModel(false);
            }
        },
        [modelName, startPolling],
    );

    return { isLoadingModel, isDownloadingModel, downloadError, loadModel, downloadModel };
}
