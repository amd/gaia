// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertTriangle, WifiOff, X, MonitorX, Download } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import './ConnectionBanner.css';

const GITHUB_DEVICE_SUPPORT_URL =
    'https://github.com/amd/gaia/issues/new?' +
    'template=feature_request.md&' +
    'title=[Feature]%20Support%20Agent%20UI%20on%20additional%20devices&' +
    'labels=enhancement,agent-ui';

/**
 * Banner shown at the top of the app for four conditions (in priority order):
 *   1. Backend API unreachable     — suggests `gaia chat --ui`
 *   2. Device not supported        — shows processor name, links to GitHub feature request
 *   3. Lemonade Server not running — suggests `gaia init` (first time) or `lemonade-server serve` (already set up)
 *   4. Lemonade running, no model  — suggests `gaia init` (covers model download + Lemonade start)
 *      (also warns if disk space < 30 GB)
 * Each banner is independently dismissible. Dismissed banners reappear if the
 * underlying condition changes again (e.g. Lemonade stops after being dismissed).
 */
export function ConnectionBanner({ onRetry }: { onRetry?: () => void }) {
    const { backendConnected, systemStatus } = useChatStore();
    const [dismissed, setDismissed] = useState(false);
    const [deviceDismissed, setDeviceDismissed] = useState(false);
    const [modelDismissed, setModelDismissed] = useState(false);

    // Reset dismissed state when the underlying status changes so the
    // banner reappears if Lemonade stops again after being dismissed.
    const prevLemonadeRef = useRef(systemStatus?.lemonade_running);
    const prevModelRef = useRef(systemStatus?.model_loaded);
    useEffect(() => {
        const currentLemonade = systemStatus?.lemonade_running;
        const currentModel = systemStatus?.model_loaded;
        if (prevLemonadeRef.current !== currentLemonade) {
            prevLemonadeRef.current = currentLemonade;
            if (currentLemonade === false) setDismissed(false);
        }
        if (prevModelRef.current !== currentModel) {
            prevModelRef.current = currentModel;
            // Re-show model banner if model was unloaded
            if (!currentModel) setModelDismissed(false);
        }
    }, [systemStatus]);

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

    // Case 2: Device is not a supported Strix Halo machine
    if (!deviceDismissed && systemStatus && systemStatus.device_supported === false) {
        const processorName = systemStatus.processor_name || 'Unknown processor';
        return (
            <div className="connection-banner connection-banner--device" role="alert">
                <div className="connection-banner__icon">
                    <MonitorX size={16} />
                </div>
                <div className="connection-banner__text">
                    Unsupported device: <strong>{processorName}</strong>.{' '}
                    <span className="connection-banner__hint">
                        GAIA Agent UI requires an AMD Ryzen AI Max (Strix Halo) or an AMD Radeon GPU with &ge;&nbsp;24&nbsp;GB VRAM.{' '}
                        <a
                            href={GITHUB_DEVICE_SUPPORT_URL}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="connection-banner__link"
                        >
                            Request support for your device &rarr;
                        </a>
                    </span>
                </div>
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setDeviceDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    // Case 3: Backend is up but Lemonade Server is not running.
    // If the system hasn't been initialized yet, `gaia init` is the single command
    // that installs Lemonade and downloads models. Once initialized, the user only
    // needs to start the already-installed server.
    if (!dismissed && systemStatus && !systemStatus.lemonade_running) {
        const notInitialized = !systemStatus.initialized;
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text">
                    LLM server is not running &mdash; chat will not work.{' '}
                    {notInitialized ? (
                        <span className="connection-banner__hint">
                            First time? Run: <code>gaia init --profile chat</code>
                        </span>
                    ) : (
                        <span className="connection-banner__hint">
                            Start it with: <code>lemonade-server serve</code>
                        </span>
                    )}
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

    // Case 4: Lemonade is running but no model is loaded.
    // `gaia init` is preferred over `gaia download` — it handles model download,
    // Lemonade server configuration, and marks the system as initialized.
    if (!modelDismissed && systemStatus && systemStatus.lemonade_running && !systemStatus.model_loaded) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <Download size={16} />
                </div>
                <div className="connection-banner__text">
                    No model loaded &mdash; chat will not work until a model is downloaded.{' '}
                    <span className="connection-banner__hint">
                        Run: <code>gaia init --profile chat</code> to download models (~25&nbsp;GB)
                        {systemStatus.disk_space_gb != null && systemStatus.disk_space_gb < 30 && (
                            <> &nbsp;&bull;&nbsp; <strong>Warning:</strong> only {systemStatus.disk_space_gb}&nbsp;GB free</>
                        )}
                    </span>
                </div>
                <button
                    className="connection-banner__dismiss"
                    onClick={() => setModelDismissed(true)}
                    aria-label="Dismiss"
                >
                    <X size={14} />
                </button>
            </div>
        );
    }

    return null;
}
