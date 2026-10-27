// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { getMemorySettings } from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';

/** New-chat toggle: the chat is created private, so not even its first message is remembered. */
export function PrivateChip({ disabled }: { disabled?: boolean }) {
    const on = useChatStore((s) => s.draftPrivate);
    const setOn = useChatStore((s) => s.setDraftPrivate);
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const [memoryEnabled, setMemoryEnabled] = useState<boolean | null>(null);

    // Re-read on close: Settings can switch memory off while this screen stays mounted.
    useEffect(() => {
        if (settingsOpen) return;
        let cancelled = false;
        getMemorySettings()
            .then((s) => { if (!cancelled) setMemoryEnabled(s.memory_enabled); })
            .catch((err) => console.error('[PrivateChip] could not read memory settings:', err));
        return () => { cancelled = true; };
    }, [settingsOpen]);

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
                <span className="composer-chip-label">Memory off</span>
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
                : "Turn on private chat. It won't be saved to memory."}
        >
            {on ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
            <span className="composer-chip-label">{on ? 'Private' : 'Memory on'}</span>
        </button>
    );
}
