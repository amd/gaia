// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { getUpdaterBridge, useUpdateStatus } from '../../hooks/useUpdateStatus';
import type { GaiaUpdaterBridge, UpdateState } from '../../hooks/useUpdateStatus';
import { log } from '../../utils/logger';
import { VersionPicker } from '../VersionPicker';

const AUTO_LABEL = 'Install updates automatically';

/** One line saying where updates stand. */
export function describeUpdateStatus(s: UpdateState): string {
    switch (s.status) {
        case 'checking':
            return 'Checking for updates…';
        case 'available':
            return `GAIA ${s.version ?? ''} is available.`;
        case 'downloading':
            return `Downloading GAIA ${s.version ?? ''}… ${s.progress}%`;
        case 'downloaded':
            return `GAIA ${s.version ?? ''} is ready — restart to finish installing.`;
        case 'error':
            return `Update check failed: ${s.error ?? 'unknown error'}`;
        case 'no-channel':
            return s.error ?? 'No update channel is configured.';
        case 'disabled':
            return 'Updates are turned off for this install (GAIA_DISABLE_UPDATE=1).';
        default:
            return 'GAIA is up to date.';
    }
}

/**
 * Settings → General → Updates. Auto-update is off by default while GAIA
 * changes fast: a found update is offered here and in the corner chip, never
 * installed unasked. "Install a specific version" pins the app to that one.
 * Shown only in the desktop app, which owns updating.
 */
export function UpdatesSection() {
    const update = useUpdateStatus();
    const bridge = getUpdaterBridge();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showPicker, setShowPicker] = useState(false);

    if (!bridge) return null;

    async function run(what: string, call: (b: GaiaUpdaterBridge) => Promise<unknown>) {
        if (!bridge) return;
        setBusy(true);
        setError(null);
        try {
            await call(bridge);
        } catch (err) {
            log.system.error(`${what} failed`, err);
            setError(`${what} failed: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
            setBusy(false);
        }
    }

    const disabled = update.status === 'disabled';
    const working = busy || update.status === 'checking' || update.status === 'downloading';

    return (
        <div className="settings-field" role="group" aria-labelledby="updates-label">
            <span className="settings-label" id="updates-label">Updates</span>
            <p className="settings-hint" role="status">
                Running GAIA {update.currentVersion ?? 'unknown'}. {describeUpdateStatus(update)}
            </p>

            <label className="setting-row">
                <span>{AUTO_LABEL}</span>
                <span className="toggle-switch">
                    <input
                        type="checkbox"
                        role="switch"
                        checked={update.autoUpdate}
                        aria-describedby="updates-auto-hint"
                        aria-label={AUTO_LABEL}
                        aria-busy={busy}
                        disabled={disabled}
                        onChange={() => {
                            if (!busy) void run('Saving the auto-update setting', (b) => b.setAutoUpdate(!update.autoUpdate));
                        }}
                    />
                    <span className="toggle-track" />
                </span>
            </label>
            <p className="settings-hint" id="updates-auto-hint">
                Off by default while GAIA is changing quickly — you choose when to update.
                The Terminal UI follows the same setting.
            </p>

            {update.pinnedVersion && (
                <p className="settings-hint">
                    Pinned to {update.pinnedVersion}, so updates are paused.{' '}
                    <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => void run('Resuming updates', (b) => b.resumeUpdates())}
                    >
                        Unpin
                    </button>
                </p>
            )}

            <div className="setting-actions">
                {update.status === 'available' && !update.pinnedVersion && (
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void run('Downloading the update', (b) => b.downloadUpdate())}
                    >
                        Update to {update.version}
                    </button>
                )}
                {update.status === 'downloaded' && (
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void run('Restarting to install', (b) => b.restart())}
                    >
                        Restart to install
                    </button>
                )}
                <button
                    type="button"
                    className="btn-secondary"
                    disabled={working || disabled}
                    onClick={() => void run('Checking for updates', (b) => b.check())}
                >
                    Check for updates
                </button>
                <button
                    type="button"
                    className="btn-secondary"
                    disabled={working || disabled}
                    onClick={() => setShowPicker(true)}
                >
                    Install a specific version…
                </button>
            </div>

            {update.configError && <p className="settings-error" role="alert">{update.configError}</p>}
            {error && <p className="settings-error" role="alert">{error}</p>}
            {showPicker && <VersionPicker onClose={() => setShowPicker(false)} />}
        </div>
    );
}
