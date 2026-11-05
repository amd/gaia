// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore, selectChipModel } from '../stores/modelStore';
import { dateLocale, useT } from '../i18n/useT';
import { log } from '../utils/logger';
import type { ChatSpending } from '../types';
import './ContextMeter.css';
import './SpendMeter.css';

/** "$0.04"; a share of a cent keeps four places so it never reads as "$0.00". */
export function formatUsd(usd: number): string {
    return `$${usd > 0 && usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2)}`;
}

/** The start of the viewer's local day, as the instant the backend compares against. */
export function startOfLocalDay(now: Date = new Date()): string {
    const day = new Date(now);
    day.setHours(0, 0, 0, 0);
    return day.toISOString();
}

interface SpendMeterProps {
    sessionId: string;
    /** A reply is running; the totals refresh once it ends. */
    busy: boolean;
}

/** This chat's and today's cloud spending, shown only while a priced cloud model is in use. */
export function SpendMeter({ sessionId, busy }: SpendMeterProps) {
    const t = useT();
    const active = useModelStore((s) => s.active);
    const model = useChatStore((s) => selectChipModel(active, s.sessions, s.currentSessionId));
    const openSettings = useChatStore((s) => s.openSettings);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [spend, setSpend] = useState<ChatSpending | null>(null);
    const close = useCallback(() => setOpen(false), []);

    const load = useCallback(() => {
        let stale = false;
        void (async () => {
            try {
                const s = await api.getChatSpending(sessionId, startOfLocalDay(), model ?? null);
                if (!stale) setSpend(s ?? null);
            } catch (err) {
                if (!stale) log.chat.error(`Could not read cloud spending for session=${sessionId}`, err);
            }
        })();
        return () => { stale = true; };
    }, [sessionId, model]);

    useEffect(() => load(), [load]);
    const wasBusy = useRef(busy);
    useEffect(() => {
        const ended = wasBusy.current && !busy;
        wasBusy.current = busy;
        return ended ? load() : undefined;
    }, [busy, load]);
    // Opening re-reads, so a chat left open past midnight starts a new "today".
    useEffect(() => (open ? load() : undefined), [open, load]);

    if (!spend?.paid || spend.model !== (model ?? null)) return null;
    const { chat, today } = spend;
    const answers = [...chat.items].reverse();
    const at = (iso: string) => new Date(iso).toLocaleTimeString(dateLocale(t), { hour: 'numeric', minute: '2-digit' });

    return (
        <div className="composer-chip-wrap spend-meter-wrap">
            <button
                ref={anchor}
                type="button"
                className="context-meter spend-meter"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                aria-label={t('spend.aria', { chat: formatUsd(chat.usd), today: formatUsd(today.usd) })}
                title={t('spend.title')}
            >
                <bdi dir="ltr">{formatUsd(chat.usd)}</bdi>
                <span className="spend-meter-today">
                    · {t.bare('spend.today')} <bdi dir="ltr">{formatUsd(today.usd)}</bdi>
                </span>
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label={t('spend.window')} align="end">
                <div className="context-meter-panel spend-meter-panel">
                    <dl className="context-meter-rows">
                        <div className="context-meter-row">
                            <dt>{t('spend.thisChat')}</dt>
                            <dd><bdi dir="ltr">{formatUsd(chat.usd)}</bdi></dd>
                        </div>
                        <div className="context-meter-row">
                            <dt>{t.plural('spend.todayChats', today.chats)}</dt>
                            <dd><bdi dir="ltr">{formatUsd(today.usd)}</bdi></dd>
                        </div>
                    </dl>
                    {answers.length > 0 && (
                        <>
                            <div className="spend-meter-heading">{t('spend.perAnswer')}</div>
                            <dl className="context-meter-rows spend-meter-answers">
                                {answers.map((a) => (
                                    <div key={a.message_id} className="context-meter-row">
                                        <dt><bdi dir="ltr">{at(a.created_at)}</bdi></dt>
                                        <dd>
                                            {a.kind === 'estimate' && a.usd != null
                                                ? <bdi dir="ltr">{formatUsd(a.usd)}</bdi>
                                                : <span className="spend-meter-unknown">{t(a.kind === 'unreported' ? 'ctx.costUnreported' : 'ctx.unknown')}</span>}
                                        </dd>
                                    </div>
                                ))}
                            </dl>
                        </>
                    )}
                    <p className="context-meter-note">{t('spend.note')}</p>
                    <button
                        type="button"
                        className="context-meter-details"
                        onClick={() => { setOpen(false); openSettings('model'); }}
                    >
                        {t('spend.month')}
                    </button>
                </div>
            </Popover>
        </div>
    );
}
