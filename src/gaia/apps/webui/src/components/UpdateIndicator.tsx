// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { Download, RefreshCw, AlertTriangle, X } from 'lucide-react';
import { useUpdateStatus } from '../hooks/useUpdateStatus';
import './UpdateIndicator.css';

/**
 * Small, subtle indicator shown in the corner of the main view when an
 * app update is available, downloading, ready, or failed. Hidden in the
 * `idle`, `checking`, and `disabled` states so it never distracts from
 * normal use. Wires up to the Electron main process via `gaiaUpdater`
 * (see preload.cjs + services/auto-updater.cjs). Phase F.
 */
export function UpdateIndicator() {
    const update = useUpdateStatus();
    const [dismissed, setDismissed] = useState(false);

    const restart = useCallback(() => {
        const updater = (window as unknown as {
            gaiaUpdater?: { check: () => Promise<unknown> };
        }).gaiaUpdater;
        // We deliberately re-trigger the check here: electron-updater's
        // checkForUpdates path is the same singleton that re-emits the
        // `update-downloaded` event, which shows the native restart dialog.
        // The user clicking the chip is treated as "I want to act on this".
        updater?.check().catch(() => {
            // Swallow — the auto-updater already logs errors.
        });
    }, []);

    // States where we deliberately render nothing — keeps the UI calm.
    if (dismissed) return null;
    if (
        update.status === 'idle' ||
        update.status === 'checking' ||
        update.status === 'disabled'
    ) {
        return null;
    }

    if (update.status === 'downloading') {
        const pct = Math.max(0, Math.min(100, update.progress));
        return (
            <div
                className="update-chip update-chip--downloading"
                role="status"
                aria-live="polite"
                title={`Downloading update${update.version ? ` ${update.version}` : ''}…`}
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    Downloading update… {pct}%
                </span>
            </div>
        );
    }

    if (update.status === 'available') {
        return (
            <div
                className="update-chip update-chip--available"
                role="status"
                title={update.releaseNotes || 'A new version is available.'}
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    Update available{update.version ? `: ${update.version}` : ''}
                </span>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss update notification"
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    if (update.status === 'downloaded') {
        return (
            <div
                className="update-chip update-chip--ready"
                role="status"
                aria-live="polite"
            >
                <RefreshCw size={13} className="update-chip__icon" />
                <button
                    type="button"
                    className="update-chip__action"
                    onClick={restart}
                    title="Click to restart and apply the update"
                >
                    Update ready — restart to apply
                </button>
            </div>
        );
    }

    if (update.status === 'error') {
        return (
            <div
                className="update-chip update-chip--error"
                role="status"
                title={update.error || 'Update check failed.'}
            >
                <AlertTriangle size={13} className="update-chip__icon" />
                <span className="update-chip__label">Update check failed</span>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label="Dismiss update error"
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    return null;
}
