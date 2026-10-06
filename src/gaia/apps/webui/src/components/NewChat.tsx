// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { useAttachments } from '../hooks/useAttachments';
import { useChatStore } from '../stores/chatStore';
import { STARTING_REASON } from '../utils/constants';
import './NewChat.css';

interface NewChatProps {
    /** Creates the chat and sends the first message; rejects with the reason it could not. */
    onSend: (text: string) => Promise<void>;
}

/** An empty chat: just the composer. The chat is created on the first message. */
export function NewChat({ onSend }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    const [text, setText] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const attachments = useAttachments();

    const submit = useCallback(async () => {
        const message = attachments.compose(text.trim());
        if (!message || sending) return;
        setSending(true);
        setError(null);
        try {
            await onSend(message);
            setText('');
            attachments.clear();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSending(false);
        }
    }, [attachments, onSend, sending, text]);

    return (
        <main className="new-chat" aria-label="New chat">
            <div className="new-chat-inner">
                <Composer
                    value={text}
                    onChange={setText}
                    onSubmit={submit}
                    attachments={attachments}
                    disabledReason={starting ? STARTING_REASON : sending ? 'Starting the chat…' : null}
                    autoFocus
                    leftControls={<PermissionModeChip sessionId={null} />}
                    rightControls={<ModelChip />}
                />
                {error && <p className="new-chat-error" role="alert">{error}</p>}
            </div>
        </main>
    );
}
