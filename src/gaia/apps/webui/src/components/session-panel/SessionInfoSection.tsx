// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Session: the facts a bug report or a log search needs — the chat's id, when
 * it started, its agent, and the GAIA and Lemonade versions behind it. Read
 * from what the app already holds, so opening it fetches nothing.
 */

import { useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';
import { requestErrorText } from '../../utils/requestErrors';
import { log } from '../../utils/logger';
import { PanelSection } from './PanelSection';
import { dateLocale, useT } from '../../i18n/useT';

function Fact({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="sp-row">
            <div className="sp-row-line">
                <span className="sp-row-label sp-quiet">{label}</span>
                <span className="sp-row-end">{children}</span>
            </div>
        </div>
    );
}

export function SessionInfoSection({ sessionId }: { sessionId: string }) {
    const t = useT();
    return (
        <PanelSection id="session" title={t('sp.section.session')}>
            <SessionFacts sessionId={sessionId} />
        </PanelSection>
    );
}

function SessionFacts({ sessionId }: { sessionId: string }) {
    const t = useT();
    const session = useChatStore((s) => s.sessions.find((x) => x.id === sessionId));
    const system = useChatStore((s) => s.systemStatus);
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const copy = () => {
        setError(null);
        Promise.resolve()
            .then(() => navigator.clipboard.writeText(sessionId))
            .then(() => setCopied(true))
            .catch((err) => {
                log.ui.warn(`Could not copy the id of session=${sessionId}`, err);
                setCopied(false);
                setError(t('session.copyFailed', { error: requestErrorText(err) }));
            });
    };

    const started = session?.created_at ? new Date(session.created_at) : null;
    const startedText = started && !Number.isNaN(started.getTime())
        ? started.toLocaleString(dateLocale(t), { dateStyle: 'medium', timeStyle: 'short' })
        : null;

    return (
        <div className="sp-rows">
            <Fact label={t('session.id')}>
                <code className="sp-mono" dir="ltr" title={sessionId}>{sessionId}</code>
                <button
                    type="button"
                    className="btn-icon-sm sp-info"
                    onClick={copy}
                    aria-label={t('session.copyId')}
                    title={copied ? t('code.copied') : t('session.copyId')}
                >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
            </Fact>
            {error && <span className="sp-error" role="alert">{error}</span>}
            {startedText && <Fact label={t('session.started')}><span>{startedText}</span></Fact>}
            {session?.agent_type && (
                <Fact label={t('session.agent')}><code className="sp-mono" dir="ltr">{session.agent_type}</code></Fact>
            )}
            {system?.version && (
                <Fact label={t('session.gaia')}><span dir="ltr">{system.version}</span></Fact>
            )}
            {system?.lemonade_version && (
                <Fact label={t('session.lemonade')}><span dir="ltr">{system.lemonade_version}</span></Fact>
            )}
        </div>
    );
}
