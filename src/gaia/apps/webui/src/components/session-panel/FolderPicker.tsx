// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Folder, Loader2, X } from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import type { BrowseResponse } from '../../types';
import { useT } from '../../i18n/useT';
import './SessionPanel.css';

const isHiddenName = (name: string) => name.startsWith('.');

/** Pick a folder on this PC to give the chat, by browsing or by pasting its path. */
export function FolderPicker({ onPick, onClose }: { onPick: (path: string) => void; onClose: () => void }) {
    const t = useT();
    const [listing, setListing] = useState<BrowseResponse | null>(null);
    const [typed, setTyped] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [showHidden, setShowHidden] = useState(false);
    useEscapeToClose(true, onClose);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, true);

    const open = useCallback((path?: string) => {
        setLoading(true);
        setError(null);
        Promise.resolve()
            .then(() => api.browseFiles(path))
            .then((res) => {
                const listing = expectShape(res, (r) => Array.isArray(r.entries), 'folder listing');
                setListing(listing);
                setTyped(listing.current_path);
            })
            .catch((err: unknown) => setError(err instanceof api.ApiError ? err.detail || err.message : String(err)))
            .finally(() => setLoading(false));
    }, []);
    useEffect(() => open(), [open]);

    const allFolders = listing?.entries.filter((e) => e.type === 'folder') ?? [];
    const folders = showHidden ? allFolders : allFolders.filter((f) => !isHiddenName(f.name));
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                ref={dialogRef}
                className="modal-panel sp-picker"
                role="dialog"
                aria-modal="true"
                aria-label={t('pick.label')}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="modal-header">
                    <h3>{t('pick.title')}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label={t('sp.closeShort')}>
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body sp-picker-body">
                    <p className="sp-meta">{t('pick.note')}</p>
                    <form
                        className="sp-picker-path"
                        onSubmit={(e) => { e.preventDefault(); if (typed.trim()) open(typed.trim()); }}
                    >
                        <input
                            value={typed}
                            onChange={(e) => setTyped(e.target.value)}
                            aria-label={t('pick.path')}
                            spellCheck={false}
                            dir="ltr"
                        />
                        <button type="submit" className="sp-action">{t('pick.go')}</button>
                    </form>
                    {listing && (
                        <div className="sp-picker-quick">
                            {listing.quick_links.map((q) => (
                                <button key={q.path} type="button" className="sp-action" onClick={() => open(q.path)}>{q.name}</button>
                            ))}
                            <label className="sp-picker-hidden">
                                <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
                                {t('pick.showHidden')}
                            </label>
                        </div>
                    )}
                    <ul className="sp-picker-list" aria-busy={loading}>
                        {listing?.parent_path && (
                            <li>
                                <button type="button" onClick={() => open(listing.parent_path ?? undefined)}>
                                    <ArrowUp size={13} aria-hidden="true" /> {t('pick.up')}
                                </button>
                            </li>
                        )}
                        {folders.map((f) => (
                            <li key={f.path}>
                                <button type="button" onClick={() => open(f.path)}>
                                    <Folder size={13} aria-hidden="true" /> {f.name}
                                </button>
                            </li>
                        ))}
                        {listing && folders.length === 0 && (
                            <li className="sp-meta">
                                {t(allFolders.length > 0 ? 'pick.onlyHidden' : 'pick.none')}
                            </li>
                        )}
                        {loading && <li className="sp-meta"><Loader2 size={12} className="spin" aria-hidden="true" /> {t('pick.loading')}</li>}
                    </ul>
                    {error && <p className="sp-error" role="alert">{error}</p>}
                </div>
                <div className="sp-picker-actions">
                    <button type="button" className="btn-secondary" onClick={onClose}>{t('sp.cancel')}</button>
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={!listing}
                        onClick={() => listing && onPick(listing.current_path)}
                    >
                        {t('pick.use')}
                    </button>
                </div>
            </div>
        </div>
    );
}
