// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { Popover } from './Popover';
import { ContextPanel } from './ContextPanel';
import { pluralKey } from '../i18n/registry';
import { useT } from '../i18n/useT';
import type { ContextUsage } from '../types';
import './ContextMeter.css';

export function formatTokens(n: number): string {
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
    return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

const PARTS = [
    { key: 'system', label: 'ctx.meter.part.system' },
    { key: 'tools', label: 'ctx.meter.part.tools' },
    { key: 'documents', label: 'ctx.meter.part.documents' },
    { key: 'history', label: 'ctx.meter.part.history' },
    { key: 'query', label: 'ctx.meter.part.query' },
] as const;

interface ContextMeterProps {
    usage: ContextUsage | null;
    unavailable?: string | null;
    /** With a session, the popover links to the full Context & performance panel. */
    sessionId?: string;
    busy?: boolean;
    onUsage?: (usage: ContextUsage) => void;
    /** Show the details somewhere else (the session panel) instead of a sheet. */
    onDetails?: () => void;
}

/** How full the model's context window is, with a breakdown on click. */
export function ContextMeter({ usage, unavailable, sessionId, busy = false, onUsage, onDetails }: ContextMeterProps) {
    const t = useT();
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [details, setDetails] = useState(false);
    const panel = details && sessionId
        ? <ContextPanel sessionId={sessionId} busy={busy} onUsage={onUsage} onClose={() => setDetails(false)} />
        : null;
    if (!usage) {
        if (!unavailable) return null;
        // A dash, not an empty bar: nothing is measured yet, and the title says when it will be.
        return (
            <span className="context-meter is-empty" title={unavailable} aria-label={`${t('ctx.context')}: ${unavailable}`}>
                <span className="context-meter-label">{t('ctx.context')}</span>
                <span aria-hidden="true">–</span>
            </span>
        );
    }
    const pct = Math.min(100, Math.round((usage.used / usage.window) * 100));
    const warn = usage.at_risk.length > 0;
    const free = Math.max(0, usage.window - usage.used - usage.reply_reserve);
    // The meter fills against GAIA's admitted window, which can be far below a cloud model's own.
    const modelWindow = usage.model_window != null && usage.model_window > usage.window ? usage.model_window : null;
    const cap = modelWindow ? { gaia: formatTokens(usage.window), model: formatTokens(modelWindow) } : null;
    // A new chat already reads 16-36%: say that most of it is GAIA's own set-up, not the user's doing.
    const setup = usage.system + usage.tools;
    const mostlySetup = usage.used > 0 && setup / usage.used >= 0.6;
    const setupNote = mostlySetup
        ? t('ctx.meter.mostlySetup', { setup: formatTokens(setup), chat: formatTokens(usage.history + usage.query + usage.documents) })
        : null;
    return (
        <div className="composer-chip-wrap context-meter-wrap">
            <button
                ref={anchor}
                type="button"
                className={`context-meter${warn ? ' is-warning' : ''}`}
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                aria-label={t.compose((say) => {
                    const fill = say('ctx.meter.aria', { pct, used: formatTokens(usage.used), window: formatTokens(usage.window) });
                    return cap ? `${fill} (${say('ctx.meter.capNote', cap)})` : fill;
                })}
                title={[cap ? t('ctx.meter.titleCapped', cap) : t('ctx.meter.title'), setupNote].filter(Boolean).join('\n')}
            >
                <span className="context-meter-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
                <span className="context-meter-label"><bdi dir="ltr">{pct}%</bdi></span>
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label={t('ctx.meter.window')} align="end">
                <div className="context-meter-panel">
                    <div className="context-meter-head">
                        <strong><bdi dir="ltr">{formatTokens(usage.used)}</bdi></strong> {t.bare('ctx.of')} <bdi dir="ltr">{formatTokens(usage.window)}</bdi> {t.bare('ctx.tokensWord')}
                        {cap && (
                            <span className="context-meter-cap" title={t('ctx.windowTitle')}>
                                {' · '}{t.bare('ctx.gaiaUses')} <bdi dir="ltr">{cap.gaia}</bdi>
                                {' '}{t.bare('ctx.ofModel')} <bdi dir="ltr">{cap.model}</bdi>
                            </span>
                        )}
                    </div>
                    {setupNote && <p className="context-meter-note is-setup">{setupNote}</p>}
                    <div className="context-meter-stack" aria-hidden="true">
                        {PARTS.map(({ key }) => (
                            <span key={key} className={`part-${key}`} style={{ width: `${(usage[key] / usage.window) * 100}%` }} />
                        ))}
                    </div>
                    <dl className="context-meter-rows">
                        {PARTS.filter(({ key }) => key !== 'query' || usage.query > 0).map(({ key, label }) => (
                            <div key={key} className="context-meter-row">
                                <dt><span className={`swatch part-${key}`} aria-hidden="true" />{t(label)}</dt>
                                <dd><bdi dir="ltr">{formatTokens(usage[key])}</bdi></dd>
                            </div>
                        ))}
                        <div className="context-meter-row is-quiet">
                            <dt>{t('ctx.meter.reserve')}</dt>
                            <dd><bdi dir="ltr">{formatTokens(usage.reply_reserve)}</bdi></dd>
                        </div>
                        <div className="context-meter-row is-quiet">
                            <dt>{t('ctx.freeRow')}</dt>
                            <dd><bdi dir="ltr">{formatTokens(free)}</bdi></dd>
                        </div>
                    </dl>
                    <p className="context-meter-note">
                        {t.compose((say) => [
                            say(pluralKey('ctx.meter.inView', usage.turns_in_view), { n: usage.turns_in_view }),
                            usage.turns_hidden > 0 ? say(pluralKey('ctx.meter.hidden', usage.turns_hidden), { n: usage.turns_hidden }) : '',
                            '. ',
                            say(usage.tokenizer?.exact ? 'ctx.meter.exact' : 'ctx.meter.estimates'),
                            ' ',
                            say('ctx.meter.saved'),
                        ].join(''))}
                    </p>
                    {sessionId && (
                        <button
                            type="button"
                            className="context-meter-details"
                            onClick={() => {
                                setOpen(false);
                                if (onDetails) onDetails();
                                else setDetails(true);
                            }}
                        >
                            {t('ctx.meter.details')}
                        </button>
                    )}
                </div>
            </Popover>
            {panel}
        </div>
    );
}

/** One line above the composer when older turns are about to leave the model's view; details on request. */
export function ContextWarning({ usage }: { usage: ContextUsage | null }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    // Dismissed for these turns only; another turn coming up to the edge shows it again.
    const [dismissed, setDismissed] = useState<string | null>(null);
    if (!usage || usage.at_risk.length === 0) return null;
    const key = usage.at_risk.join('\n');
    if (dismissed === key) return null;
    const n = usage.at_risk.length;
    return (
        <div className="context-warning" role="status">
            <AlertTriangle size={13} aria-hidden="true" />
            <div className="context-warning-body">
                <span>{t('ctx.warning.short')}</span>{' '}
                <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                    {open ? t('ctx.warning.hideDetails') : t('ctx.warning.details')}
                </button>
                {open && (
                    <p className="context-warning-details">
                        {t.plural('ctx.meter.warning', n)}{' '}
                        {usage.at_risk.map((q, i) => (
                            <span key={i}>{i > 0 && ', '}<q>{q}</q></span>
                        ))}
                        {'. '}{t('ctx.meter.warningSaved')}
                    </p>
                )}
            </div>
            <button
                type="button"
                className="btn-icon-sm context-warning-close"
                onClick={() => { setDismissed(key); setOpen(false); }}
                aria-label={t('ctx.warning.dismiss')}
                title={t('ctx.warning.dismiss')}
            >
                <X size={12} aria-hidden="true" />
            </button>
        </div>
    );
}
