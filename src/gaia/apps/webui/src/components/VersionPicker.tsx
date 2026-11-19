// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import type { ReleaseInfo } from '../hooks/useUpdateStatus';
import { updateErrorReason } from '../hooks/updateError';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { dateLocale, useT } from '../i18n/useT';
import './VersionPicker.css';

interface VersionPickerProps {
    onClose: () => void;
    /** This build can't install a version itself (macOS): link to its release page. */
    manualInstall?: boolean;
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
 * Modal dialog that lets the user pick an exact GAIA release (newer or older)
 * and install it; the app is then pinned to that version.
 *
 * Accessibility: role=dialog, focus trap, Esc to close, keyboard-navigable rows.
 */
export function VersionPicker({ onClose, manualInstall = false }: VersionPickerProps) {
    const t = useT();
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

    useEscapeToClose(true, onClose);
    useFocusTrap(dialogRef, true);

    // Move focus into the dialog ONCE on mount. Doing this per view change
    // would yank focus mid-flow (list → confirm → installing).
    useEffect(() => {
        const el = dialogRef.current;
        if (!el) return;
        const focusable = el.querySelector<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        focusable?.focus();
    }, []);

    // Focus trap — recompute focusable elements live so it keeps working
    // across view changes without stealing focus.
    useEffect(() => {
        const el = dialogRef.current;
        if (!el) return;

        function trap(e: KeyboardEvent) {
            if (e.key !== 'Tab' || !el) return;
            const focusable = el.querySelectorAll<HTMLElement>(
                'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
            );
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
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
    }, []);

    function selectRelease(release: ReleaseInfo, releases: ReleaseInfo[]) {
        if (release.isCurrent) return;
        setView({ kind: 'confirm', release, releases });
    }

    async function confirmInstall(release: ReleaseInfo) {
        setView({ kind: 'installing' });
        const bridge = getUpdaterBridge();
        if (!bridge) {
            setView({ kind: 'error', message: t('update.picker.noBridge') });
            return;
        }
        try {
            await bridge.installVersion(release.tag);
            // installVersion downloads, then restarts into it; running work asks first.
            onClose();
        } catch (err) {
            setView({ kind: 'error', message: t('update.picker.failed', { reason: updateErrorReason(err) }) });
        }
    }

    function formatDate(dateStr: string | null): string {
        if (!dateStr) return '';
        try {
            return new Date(dateStr).toLocaleDateString(dateLocale(t), {
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
                aria-label={t('update.picker.title')}
                ref={dialogRef}
            >
                <div className="version-picker-header">
                    <h3>{t('update.picker.title')}</h3>
                    <button
                        className="version-picker-close"
                        onClick={onClose}
                        aria-label={t('update.picker.close')}
                        type="button"
                    >
                        ×
                    </button>
                </div>

                <div className="version-picker-body">
                    {view.kind === 'loading' && (
                        <div className="version-picker-loading" aria-busy="true">
                            <span className="version-picker-spinner" />
                            {t('update.picker.loading')}
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
                                    {t('update.picker.browse')}
                                </a>
                            </p>
                        </div>
                    )}

                    {view.kind === 'list' && (
                        <ul className="version-picker-list" role="listbox" aria-label={t('update.picker.listAria')}>
                            {view.releases.map((r) => (
                                <li key={r.tag}>
                                    <button
                                        type="button"
                                        className="version-picker-row"
                                        data-version={r.version}
                                        aria-label={
                                            r.isCurrent
                                                ? t('update.picker.rowInstalled', { version: r.version })
                                                : r.isPinned
                                                  ? t('update.picker.rowPinned', { version: r.version })
                                                  : r.version
                                        }
                                        aria-disabled={r.isCurrent ? 'true' : 'false'}
                                        onClick={() => selectRelease(r, view.releases)}
                                        disabled={r.isCurrent}
                                    >
                                        <span className="vp-version">{r.version}</span>
                                        <span className="vp-date">{formatDate(r.date)}</span>
                                        {r.isCurrent && (
                                            <span className="vp-badge vp-badge-current">{t('update.picker.installed')}</span>
                                        )}
                                        {r.isPinned && !r.isCurrent && (
                                            <span className="vp-badge vp-badge-pinned">{t('update.picker.pinned')}</span>
                                        )}
                                        {!r.isCurrent && (
                                            <span className="vp-arrow" aria-hidden="true">›</span>
                                        )}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}

                    {view.kind === 'list' && (
                        <p className="version-picker-more">
                            {t('update.picker.recent', { n: view.releases.length })}{' '}
                            <a
                                href="https://github.com/amd/gaia/releases"
                                target="_blank"
                                rel="noreferrer"
                            >
                                {t('update.picker.browseAll')}
                            </a>
                        </p>
                    )}

                    {view.kind === 'confirm' && (
                        <div className="version-picker-confirm">
                            {manualInstall ? (
                                <p>{t('update.picker.manual', { version: view.release.version })}</p>
                            ) : (
                                <>
                                    <p>
                                        {t('update.picker.confirm', { version: view.release.version })}
                                    </p>
                                    <p className="vp-confirm-note">
                                        {t('update.picker.confirmNote', { version: view.release.version })}
                                    </p>
                                </>
                            )}
                            <div className="vp-confirm-actions">
                                <button
                                    type="button"
                                    className="vp-btn-cancel"
                                    onClick={() => setView({ kind: 'list', releases: view.releases })}
                                >
                                    {t('update.picker.back')}
                                </button>
                                {manualInstall ? (
                                    <a
                                        className="vp-btn-confirm"
                                        href={view.release.notesUrl ?? 'https://github.com/amd/gaia/releases'}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                    >
                                        {t('update.picker.openRelease')}
                                    </a>
                                ) : (
                                    <button
                                        type="button"
                                        className="vp-btn-confirm"
                                        onClick={() => confirmInstall(view.release)}
                                    >
                                        {t('update.picker.install')}
                                    </button>
                                )}
                            </div>
                        </div>
                    )}

                    {view.kind === 'installing' && (
                        <div className="version-picker-loading" aria-busy="true">
                            <span className="version-picker-spinner" />
                            {t('update.picker.starting')}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
