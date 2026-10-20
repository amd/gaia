// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ArrowUp, Folder, Loader2, X } from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import type { BrowseResponse } from '../../types';
import './SessionPanel.css';

interface FolderPickerProps {
    onPick: (path: string) => void;
    onClose: () => void;
    /** Heading; adding a folder to a chat by default. */
    title?: string;
    /** The dialog's accessible name. */
    label?: string;
    note?: string;
}

/** Pick a folder on this PC to give the chat, by browsing or by pasting its path. */
export function FolderPicker({
    onPick,
    onClose,
    title = 'Add a folder',
    label = 'Add a folder to this chat',
    note = 'GAIA will be able to read and write everything in the folder you pick, in this chat only.',
}: FolderPickerProps) {
    const [listing, setListing] = useState<BrowseResponse | null>(null);
    const [typed, setTyped] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    useEscapeToClose(true, onClose);

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

    const folders = listing?.entries.filter((e) => e.type === 'folder') ?? [];
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-panel sp-picker"
                role="dialog"
                aria-modal="true"
                aria-label={label}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="modal-header">
                    <h3>{title}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body sp-picker-body">
                    <p className="sp-meta">{note}</p>
                    <form
                        className="sp-picker-path"
                        onSubmit={(e) => { e.preventDefault(); if (typed.trim()) open(typed.trim()); }}
                    >
                        <input
                            value={typed}
                            onChange={(e) => setTyped(e.target.value)}
                            aria-label="Folder path"
                            spellCheck={false}
                        />
                        <button type="submit" className="sp-action">Go</button>
                    </form>
                    {listing && (
                        <div className="sp-picker-quick">
                            {listing.quick_links.map((q) => (
                                <button key={q.path} type="button" className="sp-action" onClick={() => open(q.path)}>{q.name}</button>
                            ))}
                        </div>
                    )}
                    <ul className="sp-picker-list" aria-busy={loading}>
                        {listing?.parent_path && (
                            <li>
                                <button type="button" onClick={() => open(listing.parent_path ?? undefined)}>
                                    <ArrowUp size={13} aria-hidden="true" /> Up one level
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
                        {listing && folders.length === 0 && <li className="sp-meta">No folders inside.</li>}
                        {loading && <li className="sp-meta"><Loader2 size={12} className="spin" aria-hidden="true" /> Loading…</li>}
                    </ul>
                    {error && <p className="sp-error" role="alert">{error}</p>}
                </div>
                <div className="sp-picker-actions">
                    <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={!listing}
                        onClick={() => listing && onPick(listing.current_path)}
                    >
                        Use this folder
                    </button>
                </div>
            </div>
        </div>
    );
}
