// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { Feather } from 'lucide-react';
import { useT } from '../i18n/useT';
import { answeringAgent, useActiveAgentStore } from '../stores/activeAgentStore';
import { useChatStore } from '../stores/chatStore';

/** Says so while the Fast and light agent answers; the default agent shows nothing. */
export function AgentChip() {
    const t = useT();
    const answering = useActiveAgentStore(answeringAgent);
    const refresh = useActiveAgentStore((s) => s.refresh);
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const streaming = useChatStore((s) => s.isStreaming);
    const openSettings = useChatStore((s) => s.openSettings);

    const wasStreaming = useRef(streaming);

    // Re-read on close: Settings is where the agent is switched.
    useEffect(() => {
        if (!settingsOpen) void refresh();
    }, [settingsOpen, refresh]);

    // And after each answer: a terminal session can switch the shared agent meanwhile.
    useEffect(() => {
        if (wasStreaming.current && !streaming) void refresh();
        wasStreaming.current = streaming;
    }, [streaming, refresh]);

    if (answering !== 'gaiacpp') return null;
    return (
        <button
            type="button"
            className="composer-chip composer-chip-agent"
            onClick={() => openSettings('model')}
            title={t('composer.agentFastTitle')}
        >
            <Feather size={13} aria-hidden="true" />
            <span className="composer-chip-label" dir="auto">{t('composer.agentFast')}</span>
        </button>
    );
}
