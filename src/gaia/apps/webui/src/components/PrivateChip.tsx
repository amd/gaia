// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { getMemorySettings } from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';

/** New-chat toggle: the chat is created private, so not even its first message is remembered. */
export function PrivateChip({ disabled }: { disabled?: boolean }) {
    const on = useChatStore((s) => s.draftPrivate);
    const setOn = useChatStore((s) => s.setDraftPrivate);
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const [memoryEnabled, setMemoryEnabled] = useState<boolean | null>(null);
    const [readFailed, setReadFailed] = useState(false);

    // Re-read on close: Settings can switch memory off while this screen stays mounted.
    useEffect(() => {
        if (settingsOpen) return;
        let cancelled = false;
        getMemorySettings()
            .then((s) => {
                if (cancelled) return;
                setMemoryEnabled(s.memory_enabled);
                setReadFailed(false);
            })
            .catch((err) => {
                if (cancelled) return;
                log.ui.error('Could not read the memory setting for the Private chip', err);
                setMemoryEnabled(null);
                setReadFailed(true);
            });
        return () => { cancelled = true; };
    }, [settingsOpen]);

    // A hidden Private choice would make the next chat private for good, unseen.
    useEffect(() => {
        if (memoryEnabled === false && on) setOn(false);
    }, [memoryEnabled, on, setOn]);

    if (memoryEnabled === false) {
        return (
            <button
                type="button"
                className="composer-chip is-unavailable"
                disabled
                aria-label="Memory off"
                title="Memory is off in Settings, so no chat is saved to memory"
            >
                <EyeOff size={13} aria-hidden="true" />
                <span className="composer-chip-label" dir="auto">Memory off</span>
            </button>
        );
    }

    return (
        <button
            type="button"
            className={`composer-chip${on ? ' is-on' : ''}`}
            onClick={() => setOn(!on)}
            disabled={disabled}
            aria-label={on ? 'Turn off private chat' : 'Turn on private chat'}
            title={on
                ? "Turn off private chat. This chat won't be saved to memory."
                : readFailed
                    ? "Couldn't read the memory setting. Turn on private chat to keep this chat out of memory either way."
                    : "Turn on private chat. It won't be saved to memory."}
        >
            {on ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
            <span className="composer-chip-label" dir="auto">{on ? 'Private' : readFailed ? 'Memory unknown' : 'Memory on'}</span>
        </button>
    );
}
