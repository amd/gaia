// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertTriangle, WifiOff, X, MonitorX } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import './ConnectionBanner.css';

const GITHUB_DEVICE_SUPPORT_URL =
    'https://github.com/amd/gaia/issues/new?' +
    'template=feature_request.md&' +
    'title=[Feature]%20Support%20Agent%20UI%20on%20additional%20devices&' +
    'labels=enhancement,agent-ui';

/**
 * Banner shown when the backend is unreachable, Lemonade Server is not running,
 * or the current device is not a supported Strix Halo machine.
 * Provides clear messaging and hints for the user to resolve the issue.
 */
export function ConnectionBanner({ onRetry }: { onRetry?: () => void }) {
    const { backendConnected, systemStatus } = useChatStore();
    const [dismissed, setDismissed] = useState(false);
    const [deviceDismissed, setDeviceDismissed] = useState(false);

    // Reset dismissed state when the underlying status changes so the
    // banner reappears if Lemonade stops again after being dismissed.
    const prevLemonadeRef = useRef(systemStatus?.lemonade_running);
    useEffect(() => {
        const current = systemStatus?.lemonade_running;
        if (prevLemonadeRef.current !== current) {
            prevLemonadeRef.current = current;
            // Only reset if it changed to a warning-worthy state
            if (current === false) {
                setDismissed(false);
            }
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

    // Case 3: Backend is up but Lemonade Server is not running
    if (!dismissed && systemStatus && !systemStatus.lemonade_running) {
        return (
            <div className="connection-banner connection-banner--warning" role="status">
                <div className="connection-banner__icon">
                    <AlertTriangle size={16} />
                </div>
                <div className="connection-banner__text">
                    LLM server is not running &mdash; chat will not work.{' '}
                    <span className="connection-banner__hint">
                        Start it with: <code>lemonade-server serve</code>
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
