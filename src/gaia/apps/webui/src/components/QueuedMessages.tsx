// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertCircle, Clock, CornerDownRight, RotateCw, X } from 'lucide-react';
import { useT, type Translate } from '../i18n/useT';
import type { QueuedMessage } from '../utils/messageQueue';
import './QueuedMessages.css';

const STATE_LABEL = {
    sending: 'queue.sending',
    delivered: 'queue.delivered',
    queued: 'queue.queued',
} as const;

interface QueuedMessagesProps {
    items: QueuedMessage[];
    onRemove: (id: number) => void;
    /** Hand a queued message to the running reply instead; absent when no reply can take one. */
    onSteer?: (id: number) => void;
    /** Put a message that failed to send back in line. */
    onRetry: (id: number) => void;
    /** A reply is running in this chat, so queued messages wait for it. */
    replying?: boolean;
    /** The chat refused the last try as busy, so the queue waits and tries again. */
    busyRetry?: boolean;
}

/** What a queued message is waiting on: this reply, a busy chat, or nothing. */
export function queuedLabel(t: Translate, replying: boolean, busyRetry: boolean): string {
    if (replying) return t(STATE_LABEL.queued);
    return t(busyRetry ? 'queue.busyRetry' : 'queue.sendingNext');
}

/** Messages typed while GAIA answers, above the composer, oldest first. */
export function QueuedMessages({ items, onRemove, onSteer, onRetry, replying = true, busyRetry = false }: QueuedMessagesProps) {
    const t = useT();
    // A message going out right now already shows in the chat.
    const shown = items.filter((m) => m.state !== 'starting');
    if (shown.length === 0) return null;
    return (
        <ul className="queued-messages" aria-label={t('queue.listAria')} aria-live="polite">
            {shown.map((item) => (
                <li key={item.id} className={`queued-message is-${item.state}`}>
                    {item.state === 'failed'
                        ? <AlertCircle size={12} aria-hidden="true" />
                        : item.state === 'queued'
                            ? <Clock size={12} aria-hidden="true" />
                            : <CornerDownRight size={12} aria-hidden="true" />}
                    <span className="queued-message-text" dir="auto" title={item.text}>{item.text}</span>
                    <span className="queued-message-state" title={item.error}>
                        {item.state === 'failed'
                            ? (item.error ? t('queue.notSentWhy', { error: item.error }) : t('queue.notSent'))
                            : item.state === 'queued'
                                ? (replying && item.refused ? t('queue.steerRefused') : queuedLabel(t, replying, busyRetry))
                                : t(STATE_LABEL[item.state as keyof typeof STATE_LABEL])}
                    </span>
                    {item.state === 'failed' && (
                        <button
                            type="button"
                            className="queued-message-steer"
                            onClick={() => onRetry(item.id)}
                            aria-label={t('queue.sendAgainAria', { text: item.text })}
                        >
                            <RotateCw size={11} aria-hidden="true" /> {t('queue.sendAgain')}
                        </button>
                    )}
                    {item.state === 'queued' && onSteer && (
                        <button
                            type="button"
                            className="queued-message-steer"
                            onClick={() => onSteer(item.id)}
                            title={t('queue.steerTitle')}
                        >
                            {t('queue.steer')}
                        </button>
                    )}
                    {(item.state === 'queued' || item.state === 'failed') && (
                        <button
                            type="button"
                            className="queued-message-remove"
                            onClick={() => onRemove(item.id)}
                            aria-label={t('queue.removeAria', { text: item.text })}
                            title={t('queue.remove')}
                        >
                            <X size={12} />
                        </button>
                    )}
                </li>
            ))}
        </ul>
    );
}
