// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Clock, CornerDownRight, X } from 'lucide-react';
import type { QueuedMessage } from '../utils/messageQueue';
import './QueuedMessages.css';

const STATE_LABEL = {
    sending: 'Adding to the running reply…',
    delivered: 'Added to the running reply. GAIA reads it at its next step.',
    queued: 'Queued. Sends when this reply ends.',
} as const;

interface QueuedMessagesProps {
    items: QueuedMessage[];
    onRemove: (id: number) => void;
    /** Hand a queued message to the running reply instead; absent when no reply can take one. */
    onSteer?: (id: number) => void;
}

/** Messages typed while GAIA answers, above the composer, oldest first. */
export function QueuedMessages({ items, onRemove, onSteer }: QueuedMessagesProps) {
    if (items.length === 0) return null;
    return (
        <ul className="queued-messages" aria-label="Messages waiting to send" aria-live="polite">
            {items.map((item) => (
                <li key={item.id} className={`queued-message is-${item.state}`}>
                    {item.state === 'queued'
                        ? <Clock size={12} aria-hidden="true" />
                        : <CornerDownRight size={12} aria-hidden="true" />}
                    <span className="queued-message-text" title={item.text}>{item.text}</span>
                    <span className="queued-message-state">{STATE_LABEL[item.state]}</span>
                    {item.state === 'queued' && onSteer && (
                        <button
                            type="button"
                            className="queued-message-steer"
                            onClick={() => onSteer(item.id)}
                            title="GAIA reads it at its next step, without starting over. It may not answer it separately."
                        >
                            Add to this reply
                        </button>
                    )}
                    {item.state === 'queued' && (
                        <button
                            type="button"
                            className="queued-message-remove"
                            onClick={() => onRemove(item.id)}
                            aria-label={`Remove queued message: ${item.text}`}
                            title="Remove"
                        >
                            <X size={12} />
                        </button>
                    )}
                </li>
            ))}
        </ul>
    );
}
