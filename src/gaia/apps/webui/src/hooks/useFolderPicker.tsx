// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { FolderPicker } from '../components/session-panel/FolderPicker';
import * as api from '../services/api';

/**
 * Pick a folder for a chat: the OS picker in the desktop app, GAIA's own popup in a browser.
 * Render `picker` where the popup belongs; it is null in the desktop app.
 */
export function useFolderPicker(onPick: (path: string) => void) {
    const [popupOpen, setPopupOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const open = (defaultPath?: string) => {
        setError(null);
        const native = window.gaiaAPI?.dialog;
        if (!native) {
            setPopupOpen(true);
            return;
        }
        native.pickFolder(defaultPath)
            .catch((err: unknown) => {
                setError(`Couldn't open the folder picker: ${err instanceof Error ? err.message : String(err)}`);
                return null;
            })
            .then(async (path) => {
                if (!path) return;
                // The OS dialog accepts any folder; ask the server, as GAIA's own picker does.
                const listing = await api.browseFiles(path);
                if (listing.folder_refusal) setError(listing.folder_refusal);
                else onPick(path);
            })
            .catch((err: unknown) => setError(err instanceof api.ApiError ? err.detail || err.message : String(err)));
    };

    const picker = popupOpen
        ? <FolderPicker onPick={(path) => { setPopupOpen(false); onPick(path); }} onClose={() => setPopupOpen(false)} />
        : null;
    return { open, picker, error };
}
