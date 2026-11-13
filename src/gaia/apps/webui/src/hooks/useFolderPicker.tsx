// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { FolderPicker } from '../components/session-panel/FolderPicker';
import * as api from '../services/api';
import { useT } from '../i18n/useT';

const reason = (err: unknown) =>
    err instanceof api.ApiError ? err.detail || err.message : err instanceof Error ? err.message : String(err);

/**
 * Pick a folder for a chat: the OS picker in the desktop app, GAIA's own popup in a browser.
 * Render `picker` where the popup belongs; it is null in the desktop app.
 */
export function useFolderPicker(onPick: (path: string) => void) {
    const t = useT();
    const [popupOpen, setPopupOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const open = (defaultPath?: string) => {
        setError(null);
        const native = window.gaiaAPI?.dialog;
        if (!native) {
            setPopupOpen(true);
            return;
        }
        native.pickFolder(defaultPath).then(
            async (path) => {
                if (!path) return;
                // The OS dialog takes any folder: ask the rule adding it applies, as GAIA's own picker shows.
                let refusal: string | null;
                try {
                    ({ refusal } = await api.checkEnvironmentFolder(path));
                } catch (err) {
                    setError(t('pick.checkFailed', { error: reason(err) }));
                    return;
                }
                if (refusal) setError(refusal);
                else onPick(path);
            },
            (err: unknown) => setError(t('pick.openFailed', { error: reason(err) })),
        );
    };

    const picker = popupOpen
        ? <FolderPicker onPick={(path) => { setPopupOpen(false); onPick(path); }} onClose={() => setPopupOpen(false)} />
        : null;
    return { open, picker, error };
}
