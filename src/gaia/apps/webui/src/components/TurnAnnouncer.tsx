// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { useTurnEnd } from '../hooks/useTurnEnd';
import { useChatStore } from '../stores/chatStore';
import { useT } from '../i18n/useT';
import { isErrorContent } from './MessageBubble';

/**
 * Tells a screen reader when a turn ends, once and briefly: the streaming text
 * itself is not in a live region, so tokens never chatter.
 */
const lastReplyId = () => [...useChatStore.getState().messages].reverse().find((m) => m.role === 'assistant')?.id;

export function TurnAnnouncer({ busy }: { busy: boolean }) {
    const t = useT();
    const [note, setNote] = useState<{ n: number; text: string }>({ n: 0, text: '' });
    // A turn that ends on the reply it started with (a refused queued send) produced nothing new.
    const startReply = useRef(lastReplyId());
    useEffect(() => {
        if (busy) startReply.current = lastReplyId();
    }, [busy]);
    useTurnEnd(busy, () => {
        const messages = useChatStore.getState().messages;
        const last = messages[messages.length - 1];
        if (last?.role !== 'assistant' || last.id === startReply.current || (last.stopState === 'stopped' && !last.stopError)) return;
        const failed = last.failed === true || isErrorContent(last.content);
        setNote((prev) => ({ n: prev.n + 1, text: t(failed ? 'live.replyFailed' : 'live.replyReady') }));
    });
    // A new node each turn, so the same sentence is read again after back-to-back turns.
    return (
        <div className="turn-announcer" role="status" aria-live="polite" aria-atomic="true">
            <span key={note.n}>{note.text}</span>
        </div>
    );
}
