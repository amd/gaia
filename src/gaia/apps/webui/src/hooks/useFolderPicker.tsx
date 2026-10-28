// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { FolderPicker } from '../components/session-panel/FolderPicker';

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
            .then((path) => { if (path) onPick(path); })
            .catch((err: unknown) =>
                setError(`Couldn't open the folder picker: ${err instanceof Error ? err.message : String(err)}`));
    };

    const picker = popupOpen
        ? <FolderPicker onPick={(path) => { setPopupOpen(false); onPick(path); }} onClose={() => setPopupOpen(false)} />
        : null;
    return { open, picker, error };
}
