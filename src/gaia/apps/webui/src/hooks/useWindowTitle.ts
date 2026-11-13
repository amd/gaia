// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { useChatStore } from '../stores/chatStore';

/** The title index.html ships, shown when no chat is open. */
const APP_TITLE = 'GAIA Agent UI';

/** Names the open chat in the window and tab title, so chats can be told apart in a window list. */
export function useWindowTitle(): void {
    const chatTitle = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId)?.title);
    useEffect(() => {
        document.title = chatTitle?.trim() ? `${chatTitle.trim()} - GAIA` : APP_TITLE;
    }, [chatTitle]);
}
