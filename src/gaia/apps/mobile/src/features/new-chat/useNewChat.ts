// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useSyncExternalStore } from 'react';

import { describeError, useT } from '@/i18n/useT';
import { useChatStreamStore } from '@/state/chatStreams';

import { createFirstSend, type FirstSendDeps } from './firstSend';

export type NewChatDeps = Pick<FirstSendDeps, 'createSession' | 'deliver' | 'flush' | 'queueHeld' | 'takeHeld'>;

export function useFirstSend(deps: NewChatDeps, initialText: string) {
    const t = useT();
    const flowDeps = (): FirstSendDeps => ({
        ...deps,
        describeError: (err) => describeError(err, t),
        firstNotSent: t('newChat.firstNotSent'),
    });
    // The controller owns the box text, so it must outlive re-renders.
    const [send] = useState(() => createFirstSend(flowDeps(), initialText));
    useEffect(() => {
        send.setDeps(flowDeps());
    });
    const state = useSyncExternalStore(send.subscribe, send.getState);
    const waiting = state.waiting;
    const turn = useChatStreamStore((s) => (waiting ? s.turns[waiting] : undefined));
    useEffect(() => {
        if (waiting) send.observe(turn);
    }, [waiting, turn, send]);
    return { state, setText: send.setText, submit: send.submit };
}
