// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { chatDraftKey, readSavedDraft, writeSavedDraft } from '../utils/savedDraft';

/**
 * A text box's contents, kept in localStorage per `key`, so a message being
 * typed survives the app restarting (an update, a crash). Sending clears it.
 * A null key keeps it in memory only (a private chat). The user's own saved
 * words win over `initial`, a suggested prefill.
 */
export function useSavedDraft(key: string | null, initial = ''): [string, Dispatch<SetStateAction<string>>] {
    const [value, setValue] = useState(() => (key && readSavedDraft(key)) || initial);
    const keyRef = useRef(key);

    useEffect(() => {
        if (keyRef.current === key) return;
        keyRef.current = key;
        setValue(key ? readSavedDraft(key) : '');
    }, [key]);

    useEffect(() => {
        if (key && keyRef.current === key) writeSavedDraft(key, value);
    }, [key, value]);

    return [value, setValue];
}

/** A chat's composer draft; a private chat's stays in memory only. */
export function useChatDraft(sessionId: string, isPrivate: boolean | undefined) {
    return useSavedDraft(isPrivate ? null : chatDraftKey(sessionId));
}
