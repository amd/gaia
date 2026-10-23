// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { EyeOff } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';

/** New-chat toggle: the chat is created private, so not even its first message is remembered. */
export function PrivateChip({ disabled }: { disabled?: boolean }) {
    const on = useChatStore((s) => s.draftPrivate);
    const setOn = useChatStore((s) => s.setDraftPrivate);
    return (
        <button
            type="button"
            className={`composer-chip${on ? ' is-on' : ''}`}
            onClick={() => setOn(!on)}
            disabled={disabled}
            aria-pressed={on}
            aria-label="Private chat"
            title={on ? 'Private: nothing in this chat is saved to memory' : 'Start a private chat (not saved to memory)'}
        >
            <EyeOff size={13} aria-hidden="true" />
            <span className="composer-chip-label">Private</span>
        </button>
    );
}
