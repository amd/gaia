// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { isMessageKey, type MessageKey } from '../../i18n/registry';
import { useT, type Translate } from '../../i18n/useT';
import { UnsupportedCard } from './UnsupportedCard';

/**
 * ConfirmationCard — a `needs_confirmation` the chat cannot answer.
 *
 * The relay turns a confirmation into the permission prompt above the message
 * box whenever the agent can wait for an answer. This card is what is left
 * when it can't: an agent too old to take one has already ended the run, so
 * the step did not happen and there is nothing here to approve.
 *
 * Rendering safety: `summary` embeds tool args that can come from email or
 * web content. It must only ever render as a React text node — never through
 * ReactMarkdown/SafeMarkdown or any innerHTML path — and the tool's name is
 * derived from `action` alone, never by parsing `summary`.
 */

interface ConfirmationPayload {
    action: string;
    summary: string;
}

function isConfirmationPayload(value: unknown): value is ConfirmationPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return typeof v.action === 'string' && typeof v.summary === 'string';
}

/** The step in the UI language ("Writing file"); a tool with no entry reads as its own words. */
function stepName(action: string, t: Translate): string {
    const key = `tool.${action}.active`;
    if (isMessageKey(key)) return t(key as MessageKey);
    const words = action.replace(/[_-]+/g, ' ').trim();
    return words ? t('tool.named.active', { what: words }) : t('tool.default.active');
}

export function ConfirmationCard({ data }: { data: unknown }) {
    const t = useT();
    const [dismissed, setDismissed] = useState(false);

    if (!isConfirmationPayload(data)) {
        return <UnsupportedCard variant="invalid" render="needs_confirmation" data={data} />;
    }

    const step = stepName(data.action, t);

    if (dismissed) {
        return (
            <div className="render-confirmation render-confirmation--dismissed">
                <span className="render-confirmation__dismissed-note">
                    {t('confirmCard.dismissed', { step })}
                </span>
            </div>
        );
    }

    return (
        <div className="render-confirmation">
            <div className="render-confirmation__header">
                <span className="render-confirmation__badge">{t('confirmCard.badge')}</span>
                <span className="render-confirmation__label">{step}</span>
            </div>
            {/* Plain text node only — the summary can carry outside content. */}
            <pre className="render-confirmation__summary" dir="auto">{data.summary}</pre>
            <div className="render-confirmation__footer">
                <span className="render-confirmation__note">{t('confirmCard.agentTooOld')}</span>
                <button
                    type="button"
                    className="render-confirmation__dismiss"
                    onClick={() => setDismissed(true)}
                >
                    {t('confirmCard.dismiss')}
                </button>
            </div>
        </div>
    );
}
