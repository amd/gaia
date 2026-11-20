// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { AccessibilityInfo } from 'react-native';

import type { Message } from '@/api/client.types';
import type { ChatStream } from '@/data';
import { useT } from '@/i18n/useT';

import { savedActivity } from './activity/savedActivity';

/** Calls `onEnd` each time `busy` goes from true to false. Webui's hook of this name can't be imported: its own node_modules would load a second React. */
export function useTurnEnd(busy: boolean, onEnd: () => void): void {
    const was = useRef(busy);
    const cb = useRef(onEnd);
    useEffect(() => {
        cb.current = onEnd;
    });
    useEffect(() => {
        const ended = was.current && !busy;
        was.current = busy;
        if (ended) cb.current();
    }, [busy]);
}

const lastReplyId = (messages: readonly Message[]) => messages.findLast((m) => m.role === 'assistant')?.id;

/** Tells a screen reader once when a turn ends; streaming text is never announced. */
export function useTurnAnnouncer(stream: ChatStream, saved: readonly Message[]): void {
    const t = useT();
    const busy = stream.status !== 'idle';
    // A turn that ends on the reply it started with (a refused queued send) produced nothing new.
    const startReply = useRef(lastReplyId(saved));
    useEffect(() => {
        if (busy) startReply.current = lastReplyId(saved);
        // Only the start of a turn matters, not each saved change during it.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [busy]);
    useTurnEnd(busy, () => {
        if (stream.stopState === 'stopped' && !stream.stopError) return;
        const last = saved.at(-1);
        const reply = last?.role === 'assistant' && last.id !== startReply.current ? last : null;
        const failed = stream.failed === true || stream.error !== null;
        if (!reply && !failed) return;
        const broken = failed || (reply !== null && (reply.failed === true || savedActivity(reply).isError));
        AccessibilityInfo.announceForAccessibility(t(broken ? 'live.replyFailed' : 'live.replyReady'));
    });
}
