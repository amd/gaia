// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Popover } from './Popover';
import type { ContextUsage } from '../types';
import './ContextMeter.css';

export function formatTokens(n: number): string {
    if (n < 1000) return String(n);
    return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
}

const PARTS = [
    { key: 'system', label: 'System prompt' },
    { key: 'tools', label: 'Tools' },
    { key: 'documents', label: 'Documents' },
    { key: 'history', label: 'Conversation' },
    { key: 'query', label: 'Your message' },
] as const;

/** How full the model's context window is, with a breakdown on click. */
export function ContextMeter({ usage, unavailable }: { usage: ContextUsage | null; unavailable?: string | null }) {
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    if (!usage) {
        if (!unavailable) return null;
        return (
            <span className="context-meter is-empty" title={unavailable}>
                <span className="context-meter-bar" aria-hidden="true"><span style={{ width: 0 }} /></span>
                <span className="context-meter-label">Context</span>
            </span>
        );
    }
    const pct = Math.min(100, Math.round((usage.used / usage.window) * 100));
    const warn = usage.at_risk.length > 0;
    const free = Math.max(0, usage.window - usage.used - usage.reply_reserve);
    return (
        <div className="composer-chip-wrap context-meter-wrap">
            <button
                ref={anchor}
                type="button"
                className={`context-meter${warn ? ' is-warning' : ''}`}
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                aria-label={`Context ${pct}% full: ${formatTokens(usage.used)} of ${formatTokens(usage.window)} tokens`}
                title="How full the model's context window is"
            >
                <span className="context-meter-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
                <span className="context-meter-label">{pct}%</span>
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Context window" align="end">
                <div className="context-meter-panel">
                    <div className="context-meter-head">
                        <strong>{formatTokens(usage.used)}</strong> of {formatTokens(usage.window)} tokens
                    </div>
                    <div className="context-meter-stack" aria-hidden="true">
                        {PARTS.map(({ key }) => (
                            <span key={key} className={`part-${key}`} style={{ width: `${(usage[key] / usage.window) * 100}%` }} />
                        ))}
                    </div>
                    <dl className="context-meter-rows">
                        {PARTS.filter(({ key }) => key !== 'query' || usage.query > 0).map(({ key, label }) => (
                            <div key={key} className="context-meter-row">
                                <dt><span className={`swatch part-${key}`} aria-hidden="true" />{label}</dt>
                                <dd>{formatTokens(usage[key])}</dd>
                            </div>
                        ))}
                        <div className="context-meter-row is-quiet">
                            <dt>Kept free for the reply</dt>
                            <dd>{formatTokens(usage.reply_reserve)}</dd>
                        </div>
                        <div className="context-meter-row is-quiet">
                            <dt>Free</dt>
                            <dd>{formatTokens(free)}</dd>
                        </div>
                    </dl>
                    <p className="context-meter-note">
                        {usage.turns_in_view} earlier {usage.turns_in_view === 1 ? 'turn is' : 'turns are'} in view
                        {usage.turns_hidden > 0 && `; ${usage.turns_hidden} older ${usage.turns_hidden === 1 ? 'is' : 'are'} out of view`}.
                        {' '}Estimates. GAIA never summarises your chat; older turns stay saved and can be recalled.
                    </p>
                </div>
            </Popover>
        </div>
    );
}

/** Says which turns leave the model's view next, before they go. */
export function ContextWarning({ usage }: { usage: ContextUsage | null }) {
    if (!usage || usage.at_risk.length === 0) return null;
    const n = usage.at_risk.length;
    return (
        <div className="context-warning" role="status">
            <AlertTriangle size={13} aria-hidden="true" />
            <span>
                The conversation is filling the model's context. A long next reply pushes out the
                oldest {n === 1 ? 'turn' : `${n} turns`}:{' '}
                {usage.at_risk.map((q, i) => (
                    <span key={i}>{i > 0 && ', '}<q>{q}</q></span>
                ))}
                . They stay saved, and you can ask GAIA to recall them.
            </span>
        </div>
    );
}
