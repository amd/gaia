// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import type { ReleaseInfo } from '../hooks/useUpdateStatus';
import './VersionPicker.css';

interface VersionPickerProps {
    onClose: () => void;
}

type PickerView =
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'list'; releases: ReleaseInfo[] }
    | { kind: 'confirm'; release: ReleaseInfo; releases: ReleaseInfo[] }
    | { kind: 'installing' };

interface GaiaUpdaterBridge {
    listReleases: () => Promise<ReleaseInfo[] | { error: string }>;
    installVersion: (tag: string) => Promise<unknown>;
}

function getUpdaterBridge(): GaiaUpdaterBridge | undefined {
    return (window as unknown as { gaiaUpdater?: GaiaUpdaterBridge }).gaiaUpdater;
}

/**
 * Modal dialog that lets the user pick a previous GAIA release and trigger
 * a rollback download + restart.
 *
 * Accessibility: role=dialog, focus trap, Esc to close, keyboard-navigable rows.
 */
export function VersionPicker({ onClose }: VersionPickerProps) {
    const [view, setView] = useState<PickerView>({ kind: 'loading' });
    const dialogRef = useRef<HTMLDivElement>(null);

    // Fetch releases on mount.
    useEffect(() => {
        const bridge = getUpdaterBridge();
        if (!bridge) {
            setView({ kind: 'error', message: "Couldn't reach GitHub to list releases — check your connection; you can still download installers from the Releases page." });
            return;
        }

        bridge
            .listReleases()
            .then((result) => {
                if (Array.isArray(result)) {
                    setView({ kind: 'list', releases: result });
                } else {
                    setView({ kind: 'error', message: result.error });
                }
            })
            .catch(() => {
                setView({ kind: 'error', message: "Couldn't reach GitHub to list releases — check your connection; you can still download installers from the Releases page." });
            });
    }, []);

    // Esc to close.
    useEffect(() => {
        function onKeyDown(e: KeyboardEvent) {
            if (e.key === 'Escape') onClose();
        }
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [onClose]);

    // Focus trap — keep focus inside the dialog.
    useEffect(() => {
        const el = dialogRef.current;
        if (!el) return;
        const focusable = el.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        first?.focus();

        function trap(e: KeyboardEvent) {
            if (e.key !== 'Tab') return;
            if (e.shiftKey) {
                if (document.activeElement === first) {
                    e.preventDefault();
                    last?.focus();
                }
            } else {
                if (document.activeElement === last) {
                    e.preventDefault();
                    first?.focus();
                }
            }
        }
        el.addEventListener('keydown', trap);
        return () => el.removeEventListener('keydown', trap);
    }, [view]);

    function selectRelease(release: ReleaseInfo, releases: ReleaseInfo[]) {
        if (release.isCurrent) return;
        setView({ kind: 'confirm', release, releases });
    }

    async function confirmInstall(release: ReleaseInfo) {
        setView({ kind: 'installing' });
        const bridge = getUpdaterBridge();
        if (!bridge) {
            setView({ kind: 'error', message: "Bridge unavailable — please restart the app." });
            return;
        }
        try {
            await bridge.installVersion(release.tag);
            // installVersion triggers the download; the existing update-downloaded
            // handler will show the native "Restart now?" dialog.
            onClose();
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            setView({ kind: 'error', message: `Failed to start rollback: ${msg}` });
        }
    }

    function formatDate(dateStr: string | null): string {
        if (!dateStr) return '';
        try {
            return new Date(dateStr).toLocaleDateString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
            });
        } catch {
            return dateStr;
        }
    }

    return (
        <div
            className="version-picker-overlay"
            onClick={(e) => {
                if (e.target === e.currentTarget) onClose();
            }}
        >
            <div
                className="version-picker-dialog"
                role="dialog"
                aria-modal="true"
                aria-label="Roll back to a previous version"
                ref={dialogRef}
            >
                <div className="version-picker-header">
                    <h3>Roll back to a previous version</h3>
                    <button
                        className="version-picker-close"
                        onClick={onClose}
                        aria-label="Close"
                        type="button"
                    >
                        ×
                    </button>
                </div>

                <div className="version-picker-body">
                    {view.kind === 'loading' && (
                        <div className="version-picker-loading" aria-busy="true">
                            <span className="version-picker-spinner" />
                            Loading releases…
                        </div>
                    )}

                    {view.kind === 'error' && (
                        <div className="version-picker-error">
                            <p>{view.message}</p>
                            <p>
                                <a
                                    href="https://github.com/amd/gaia/releases"
                                    target="_blank"
                                    rel="noreferrer"
                                >
                                    Browse releases on GitHub
                                </a>
                            </p>
                        </div>
                    )}

                    {view.kind === 'list' && (
                        <ul className="version-picker-list" role="listbox" aria-label="Available releases">
                            {view.releases.map((r) => (
                                <li key={r.tag}>
                                    <button
                                        type="button"
                                        className="version-picker-row"
                                        data-version={r.version}
                                        aria-label={`${r.version}${r.isCurrent ? ' — installed' : ''}${r.isPinned ? ' — pinned' : ''}`}
                                        aria-disabled={r.isCurrent ? 'true' : 'false'}
                                        onClick={() => selectRelease(r, view.releases)}
                                        disabled={r.isCurrent}
                                    >
                                        <span className="vp-version">{r.version}</span>
                                        <span className="vp-date">{formatDate(r.date)}</span>
                                        {r.isCurrent && (
                                            <span className="vp-badge vp-badge-current">Installed</span>
                                        )}
                                        {r.isPinned && !r.isCurrent && (
                                            <span className="vp-badge vp-badge-pinned">Pinned</span>
                                        )}
                                        {!r.isCurrent && (
                                            <span className="vp-arrow" aria-hidden="true">›</span>
                                        )}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}

                    {view.kind === 'confirm' && (
                        <div className="version-picker-confirm">
                            <p>
                                This will <strong>downgrade to v{view.release.version}</strong> and
                                restart the app.
                            </p>
                            <p className="vp-confirm-note">
                                Auto-update will be paused after rollback so the app stays on
                                v{view.release.version}. You can resume updates from Settings → About.
                            </p>
                            <div className="vp-confirm-actions">
                                <button
                                    type="button"
                                    className="vp-btn-cancel"
                                    onClick={() => setView({ kind: 'list', releases: view.releases })}
                                >
                                    Back
                                </button>
                                <button
                                    type="button"
                                    className="vp-btn-confirm"
                                    onClick={() => confirmInstall(view.release)}
                                >
                                    Confirm downgrade &amp; restart
                                </button>
                            </div>
                        </div>
                    )}

                    {view.kind === 'installing' && (
                        <div className="version-picker-loading" aria-busy="true">
                            <span className="version-picker-spinner" />
                            Starting download…
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
