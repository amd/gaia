// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useT } from '../i18n/useT';
import { shownTitle } from '../utils/sessionTitle';

/** The title index.html ships, shown when no chat is open. */
const APP_TITLE = 'GAIA Agent UI';

/** Names the open chat in the window and tab title, so chats can be told apart in a window list. */
export function useWindowTitle(): void {
    const t = useT();
    const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
    const title = session?.title;
    const kind = session?.kind;
    useEffect(() => {
        const shown = title?.trim() ? shownTitle(t, { title: title.trim(), kind }) : '';
        document.title = shown ? `${shown} - GAIA` : APP_TITLE;
    }, [title, kind, t]);
}
