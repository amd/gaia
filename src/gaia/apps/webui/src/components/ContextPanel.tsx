// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import * as api from '../services/api';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { cloudProviderName } from '../stores/modelStore';
import { log } from '../utils/logger';
import type { ChatPerformance, ContextUsage, TurnPerformance } from '../types';
import { ref } from '../i18n/registry';
import { tEn, useT, type Translate } from '../i18n/useT';
import './ContextPanel.css';

const exact = (n: number) => n.toLocaleString('en-US');

/** "1.2K", with the exact count on hover. */
function Tokens({ n }: { n: number | null | undefined }) {
    const t = useT();
    if (n == null) return <span className="cp-unknown">{t('ctx.unknown')}</span>;
    const short = n < 1000
        ? String(n)
        : n < 1_000_000
            ? `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`
            : `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
    return <span title={t('ctx.tokens', { n: exact(n) })} dir="ltr">{short}</span>;
}

/** "1.2 s", with milliseconds on hover. */
function Seconds({ s }: { s: number | null | undefined }) {
    const t = useT();
    if (s == null) return <span className="cp-unknown">{t('ctx.unknown')}</span>;
    const short = s < 0.1 ? (s === 0 ? '0 s' : '<0.1 s') : s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
    return <span title={`${(s * 1000).toFixed(0)} ms`} dir="ltr">{short}</span>;
}

function Rate({ r }: { r: number | null | undefined }) {
    const t = useT();
    if (r == null) return <span className="cp-unknown">{t('ctx.unknown')}</span>;
    return <span title={t('ctx.rateTitle', { n: r })}>{t('ctx.rate', { n: Math.round(r).toLocaleString('en-US') })}</span>;
}

const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);
/** A share that rounds to nothing but isn't nothing reads "<1", never "0". */
const pctLabel = (part: number, whole: number) => {
    const p = pct(part, whole);
    return p === 0 && part > 0 ? '<1' : String(p);
};
/** Rounded down, so 99.7% reuse never reads as "100%". */
const hitRate = (cached: number | null, total: number | null) =>
    total && cached != null ? Math.floor((cached / total) * 100) : null;

/** Below this, time to first token is mostly fixed cost, not reading speed. */
const MIN_TOKENS_TO_RATE = 256;

function placement(now: ChatPerformance['now'], t: Translate = tEn): string {
    if (now.provider === 'unknown') return t('ctx.unknown');
    if (now.provider === 'cloud') return t('ctx.cloud');
    if (now.provider !== 'local') return t('ctx.cloudVia', { provider: cloudProviderName(now.provider) });
    if (!now.device) return now.loaded === false ? t('ctx.notLoadedNow') : t('ctx.unknown');
    const device = now.device.toUpperCase();
    const backend = now.backend === 'npu' ? 'FLM' : now.backend;
    return backend && backend !== now.device
        ? `${device} · ${backend.charAt(0).toUpperCase()}${backend.slice(1)}`
        : device;
}

function Row({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
    return (
        <div className="cp-row" title={hint}>
            <dt>{label}</dt>
            <dd>{children}</dd>
        </div>
    );
}

/** Model time against GAIA's own time; the two add up to the wall time. */
function TimeSplit({ t: time }: { t: TurnPerformance['time'] }) {
    const t = useT();
    if (time.model_seconds == null || time.gaia_seconds == null) {
        return (
            <Row label={t('ctx.took')}><Seconds s={time.wall_seconds} /> · {t.bare('ctx.split')} <span className="cp-unknown">{t('ctx.unknown')}</span></Row>
        );
    }
    const parts = [
        { key: 'model', label: t('ctx.inModel'), s: time.model_seconds },
        { key: 'tools', label: t('ctx.runningTools'), s: time.tool_seconds },
        { key: 'waiting', label: t('ctx.waitingOnYou'), s: time.waiting_seconds },
        { key: 'gaia', label: t('ctx.gaiaItself'), s: time.own_seconds ?? 0 },
    ];
    return (
        <>
            <Row label={t('ctx.took')} hint={t('ctx.tookHint')}>
                <Seconds s={time.wall_seconds} />
            </Row>
            <div className="cp-stack" aria-hidden="true">
                {parts.map((p) => (
                    <span key={p.key} className={`cp-time-${p.key}`} style={{ width: `${pct(p.s, time.wall_seconds)}%` }} />
                ))}
            </div>
            <dl className="cp-rows cp-indent">
                <Row label={t('ctx.inModel')}><Seconds s={time.model_seconds} /></Row>
                <Row label={t('ctx.inGaia')} hint={t('ctx.inGaiaHint')}>
                    <Seconds s={time.gaia_seconds} />
                </Row>
                {parts.slice(1).filter((p) => p.s > 0).map((p) => (
                    <Row key={p.key} label={`· ${p.label}`}>
                        <span className={`swatch cp-time-${p.key}`} aria-hidden="true" /> <Seconds s={p.s} />
                    </Row>
                ))}
            </dl>
        </>
    );
}

/** "unknown", with the reason on hover when one is known. */
function Unknown({ why }: { why?: string }) {
    const t = useT();
    return <span className="cp-unknown" title={why}>{t('ctx.unknown')}</span>;
}

const SOURCE_HINT = {
    server: 'ctx.src.server',
    client: 'ctx.src.client',
} as const;

function sourceHint(source: 'server' | 'client' | null | undefined, t: Translate): string | undefined {
    return source ? t(SOURCE_HINT[source]) : undefined;
}

function TurnStats({ turn }: { turn: TurnPerformance }) {
    const t = useT();
    const hit = hitRate(turn.tokens_cached, turn.tokens_in);
    const why = turn.unknown ?? {};
    const toolList = turn.tools.map((x) => `${x.name ?? t('ctx.toolOutside')}: ${x.seconds ?? '?'} s`).join('\n');
    return (
        <>
            <TimeSplit t={turn.time} />
            <dl className="cp-rows">
                <Row label={t('ctx.firstWord')} hint={sourceHint(turn.first_token_source, t)}>
                    {turn.first_token_seconds == null
                        ? <Unknown why={why.first_token_seconds} />
                        : <>{t.bare('ctx.in')} <Seconds s={turn.first_token_seconds} /></>}
                </Row>
                <Row label={t('ctx.readingPrompt')} hint={t('ctx.readingHint')}>
                    {turn.tokens_new != null && turn.tokens_new < MIN_TOKENS_TO_RATE
                        ? <span title={t('ctx.tooFewTitle')}>
                            {t('ctx.onlyNew', { n: turn.tokens_new })}
                        </span>
                        : turn.prefill_tokens_per_second == null
                            ? <Unknown why={why.prefill_tokens_per_second} />
                            : <>
                                <Rate r={turn.prefill_tokens_per_second} />
                                {turn.prefill_source === 'client' && (
                                    <span className="cp-chip" title={t('ctx.approxTitle')}>{t('ctx.approx')}</span>
                                )}
                            </>}
                </Row>
                <Row label={t('ctx.writing')} hint={sourceHint(turn.generation_source, t) ?? t('ctx.writingHint')}>
                    {turn.generation_tokens_per_second == null
                        ? <Unknown why={why.generation_tokens_per_second} />
                        : <Rate r={turn.generation_tokens_per_second} />}
                </Row>
                <Row label={t('ctx.tokensInOut')} hint={why.tokens}>
                    <span dir="ltr"><Tokens n={turn.tokens_in} /> → <Tokens n={turn.tokens_out} /></span>
                </Row>
                <Row label={t('ctx.cache')} hint={t('ctx.cacheHint')}>
                    {turn.tokens_cached == null
                        ? <Unknown why={why.tokens_cached} />
                        : <><Tokens n={turn.tokens_cached} />{hit != null && ` (${hit}%)`}</>}
                </Row>
                <Row label={t('ctx.modelCalls')}>{turn.model_calls}</Row>
                <Row label={t('ctx.toolCalls')} hint={toolList || undefined}>
                    {turn.tool_calls === 0 && !turn.tool_seconds_total
                        ? t('ctx.none')
                        : <>{turn.tool_calls} · <Seconds s={turn.tool_seconds_total ?? 0} /></>}
                </Row>
                <Row label={t('ctx.loads')}>
                    {turn.loads.length === 0 ? t('ctx.none') : (
                        <span title={turn.loads.map((l) => `${l.model}${l.replaced?.length ? ` replaced ${l.replaced.join(', ')}` : ''}: ${l.seconds} s`).join('\n')}>
                            {turn.loads.length} · <Seconds s={turn.loads.reduce((a, l) => a + l.seconds, 0)} />
                        </span>
                    )}
                </Row>
                <Row label={t('rail.cost')}><Cost cost={turn.cost} /></Row>
            </dl>
        </>
    );
}

function Cost({ cost }: { cost: TurnPerformance['cost'] }) {
    const t = useT();
    if (cost.kind === 'local') return <span title={t('ctx.costLocalTitle')}>{t('ctx.costLocal')}</span>;
    if (cost.kind === 'estimate' && cost.usd != null) {
        return (
            <span title={t('ctx.listPriceTitle', { usd: cost.usd })}>
                {t('ctx.about', { usd: cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2) })}
            </span>
        );
    }
    return <span className="cp-unknown">{t('ctx.unknown')}</span>;
}

function ChatTotals({ totals }: { totals: ChatPerformance['totals'] }) {
    const t = useT();
    const hit = hitRate(totals.tokens_cached, totals.tokens_in);
    const unknown = <span className="cp-unknown">{t('ctx.unknown')}</span>;
    return (
        <dl className="cp-rows">
            <Row label={t('ctx.turns')}>{totals.turns}</Row>
            <Row label={t('ctx.totalTime')}><Seconds s={totals.wall_seconds} /></Row>
            <Row label={t('ctx.inModel')}><Seconds s={totals.model_seconds} /></Row>
            <Row label={t('ctx.inGaia')}><Seconds s={totals.gaia_seconds} /></Row>
            <Row label={t('ctx.tokensInOut')}><span dir="ltr"><Tokens n={totals.tokens_in} /> → <Tokens n={totals.tokens_out} /></span></Row>
            <Row label={t('ctx.cache')}>
                {totals.tokens_cached == null ? unknown : <><Tokens n={totals.tokens_cached} />{hit != null && ` (${hit}%)`}</>}
            </Row>
            <Row label={t('ctx.toolCalls')}>{totals.tool_calls ?? unknown}</Row>
            <Row label={t('ctx.loads')}>{totals.loads === 0 ? t('ctx.none') : <>{totals.loads} · <Seconds s={totals.load_seconds} /></>}</Row>
            <Row label={t('rail.cost')}>
                {totals.usd == null
                    ? unknown
                    : totals.unpriced_turns > 0
                    ? <span title={t('ctx.unpricedTitle', { n: totals.unpriced_turns })}>{t('ctx.unpriced', { usd: totals.usd.toFixed(2), n: totals.unpriced_turns })}</span>
                    : totals.usd === 0 ? '$0' : t('ctx.about', { usd: totals.usd.toFixed(2) })}
            </Row>
        </dl>
    );
}

/** What is live in the window, and what the agent reports it moved out (summaries only once that exists). */
function BeyondTheWindow({ usage }: { usage: ContextUsage }) {
    const t = useT();
    const away = usage.context_state?.put_away;
    const folded = usage.context_state?.summarized;
    return (
        <div className="cp-full">
            <dl className="cp-rows">
                <Row label={t('ctx.live')}><Tokens n={usage.used} /></Row>
                {away && away.results > 0 && (
                    <Row
                        label={t('ctx.putAway')}
                        hint={t('ctx.putAwayHint')}
                    >
                        {t.plural('ctx.results', away.results)} · <Tokens n={away.tokens} />
                    </Row>
                )}
                {folded && folded.turns > 0 && (
                    <Row label={t('ctx.summarized')} hint={t('ctx.summarizedHint')}>
                        {t.plural('ctx.earlier', folded.turns)} · <Tokens n={folded.tokens} />
                        {' → '}<Tokens n={folded.summary_tokens} />
                    </Row>
                )}
            </dl>
            {folded && folded.turns > 0 && (
                <p className="cp-note cp-summarized">{t('ctx.summarizedNote')}</p>
            )}
            {usage.at_risk.length > 0 && (
                <p className="cp-warning" role="status">{t('ctx.filling')}</p>
            )}
        </div>
    );
}

interface ContextDetailsProps {
    sessionId: string;
    /** A reply is running; the details refresh once it ends. */
    busy: boolean;
    /** The measured usage, so the meter shows the same total as the details. */
    onUsage?: (usage: ContextUsage) => void;
}

/** What fills the context window and where each turn's time went. Fetches only while shown. */
export function ContextDetails({ sessionId, busy, onUsage }: ContextDetailsProps) {
    const t = useT();
    const [usage, setUsage] = useState<ContextUsage | null>(null);
    const [unavailable, setUnavailable] = useState<string | null>(null);
    const [perf, setPerf] = useState<ChatPerformance | null>(null);
    const [usageError, setUsageError] = useState<string | null>(null);
    const [perfError, setPerfError] = useState<string | null>(null);
    const [scope, setScope] = useState<'turn' | 'chat'>('turn');
    const onUsageRef = useRef(onUsage);
    onUsageRef.current = onUsage;

    // Each section loads on its own, so one failing endpoint never blanks the other.
    const load = useCallback(() => {
        let stale = false;
        const failed = (what: string, setFailure: (msg: string) => void) => (err: unknown) => {
            if (stale) return;
            log.chat.error(`Could not read the ${what} for session=${sessionId}`, err);
            setFailure(err instanceof Error ? err.message : String(err));
        };
        api.getContextUsage(sessionId)
            .then((ctx) => {
                if (stale) return;
                if (ctx.available) {
                    setUsage(ctx);
                    setUnavailable(null);
                    onUsageRef.current?.(ctx);
                } else {
                    setUsage(null);
                    setUnavailable(ctx.reason);
                }
                setUsageError(null);
            })
            .catch(failed('context usage', setUsageError));
        api.getChatPerformance(sessionId)
            .then((p) => {
                if (stale) return;
                setPerf(p);
                setPerfError(null);
            })
            .catch(failed('performance', setPerfError));
        return () => { stale = true; };
    }, [sessionId]);

    // On open, and once each reply ends; never on a timer.
    useEffect(() => load(), [load]);
    const wasBusy = useRef(busy);
    useEffect(() => {
        const ended = wasBusy.current && !busy;
        wasBusy.current = busy;
        return ended ? load() : undefined;
    }, [busy, load]);

    const last = perf?.turns[perf.turns.length - 1] ?? null;
    // A newer answer with no record (e.g. a kept "Retry with") makes the last turn unknown.
    const latest = last && last.message_id === perf?.latest_message_id ? last : null;
    const free = usage ? Math.max(0, usage.window - usage.used) : 0;

    return (
        <>
            <section aria-labelledby="cp-context">
                <h3 id="cp-context">{t('ctx.context')}</h3>
                {usageError && <p className="cp-error" role="alert">{usageError}</p>}
                {!usage && !usageError && <p className="cp-note">{unavailable ?? t('ctx.measuring')}</p>}
                {usage && (
                    <>
                        <p className="cp-lead">
                            <strong>{t('ctx.used', { pct: pct(usage.used, usage.window) })}</strong>
                            {' · '}<Tokens n={usage.used} /> {t.bare('ctx.of')} <Tokens n={usage.window} /> {t.bare('ctx.tokensWord')}
                            {usage.model && <> · <span className="cp-model" dir="ltr">{usage.model}</span></>}
                            {usage.model_window != null && usage.model_window > usage.window && (
                                <span className="cp-window-note" title={t('ctx.windowTitle')}>
                                    {' '}· {t.bare('ctx.gaiaUses')} <Tokens n={usage.window} /> {t.bare('ctx.ofModel')} <Tokens n={usage.model_window} />
                                </span>
                            )}
                            {usage.tokenizer && !usage.tokenizer.exact && (
                                <span className="cp-chip" title={usage.tokenizer.reason}>{t('ctx.approx')}</span>
                            )}
                        </p>
                        <div className="cp-stack" aria-hidden="true">
                            {(usage.categories ?? []).map((c) => (
                                <span key={c.key} className={`cp-cat-${c.key}`} style={{ width: `${(c.tokens / usage.window) * 100}%` }} />
                            ))}
                        </div>
                        <dl className="cp-rows">
                            {(usage.categories ?? []).map((c) => (
                                <div key={c.key} className="cp-row">
                                    <dt><span className={`swatch cp-cat-${c.key}`} aria-hidden="true" />{c.label}</dt>
                                    <dd><Tokens n={c.tokens} /> <span className="cp-pct">{pctLabel(c.tokens, usage.window)}%</span></dd>
                                </div>
                            ))}
                            <div className="cp-row is-quiet">
                                <dt><span className="swatch cp-cat-free" aria-hidden="true" />{t('ctx.freeRow')}</dt>
                                <dd><Tokens n={free} /> <span className="cp-pct">{pct(free, usage.window)}%</span></dd>
                            </div>
                            <div className="cp-row is-quiet cp-indent">
                                <dt>{t('ctx.reserve')}</dt>
                                <dd><Tokens n={Math.min(free, usage.reply_reserve)} /></dd>
                            </div>
                        </dl>
                        <p className="cp-note">
                            {usage.tokenizer?.exact
                                ? t('ctx.exactTokenizer', { model: usage.tokenizer.model })
                                : t('ctx.estimated', { reason: usage.tokenizer?.reason ?? ref('ctx.sharedTokenizer') })}
                            {latest?.last_prompt_tokens != null && (
                                <>{' '}{t('ctx.lastRequest', { n: exact(latest.last_prompt_tokens) })}</>
                            )}
                        </p>
                        {usage.tool_loading && usage.tool_loading.on_demand.length > 0 && (
                            <details className="cp-details">
                                <summary>
                                    {t('ctx.onDemand', { n: usage.tool_loading.on_demand.length })}
                                </summary>
                                <p className="cp-note">
                                    {t('ctx.sentNow', { n: usage.tool_loading.loaded.length })}
                                </p>
                                <ul className="cp-tools">
                                    {usage.tool_loading.on_demand.map((t) => <li key={t}>{t}</li>)}
                                </ul>
                            </details>
                        )}
                        <BeyondTheWindow usage={usage} />
                    </>
                )}
            </section>

            <section aria-labelledby="cp-hood">
                <div className="cp-section-head">
                    <h3 id="cp-hood">{t('ctx.hood')}</h3>
                    <div className="cp-toggle" role="group" aria-label={t('ctx.show')}>
                        <button type="button" aria-pressed={scope === 'turn'} onClick={() => setScope('turn')}>{t('ctx.lastTurn')}</button>
                        <button type="button" aria-pressed={scope === 'chat'} onClick={() => setScope('chat')}>{t('sp.title')}</button>
                    </div>
                </div>
                {perf && (
                    <dl className="cp-rows">
                        <Row label={t('env.runsOn')} hint={perf.now.error ?? undefined}>{placement(perf.now, t)}</Row>
                        {perf.now.provider === 'local' && (
                            <Row label={t('ctx.memory')} hint={t('ctx.memoryHint')}>
                                {perf.now.weights_gb == null
                                    ? <span className="cp-unknown">{t('ctx.unknown')}</span>
                                    : <>
                                        {t('ctx.gbModel', { gb: perf.now.weights_gb.toFixed(1) })}
                                        {perf.now.kv_cache_gb != null && ` + ${t('ctx.gbCache', { gb: perf.now.kv_cache_gb.toFixed(1) })}`}
                                    </>}
                                {' · '}
                                {perf.now.free_gb == null
                                    ? <span className="cp-unknown">{t('ctx.freeUnknown')}</span>
                                    : t('env.free', { gb: perf.now.free_gb.toFixed(1) })}
                            </Row>
                        )}
                    </dl>
                )}
                {perfError && <p className="cp-error" role="alert">{perfError}</p>}
                {!perf && !perfError && <p className="cp-note">{t('ctx.measuring')}</p>}
                {perf && scope === 'turn' && (latest
                    ? <TurnStats turn={latest} />
                    : <p className="cp-note">
                        {perf.latest_message_id == null
                            ? t('ctx.afterNext')
                            : <>{t.bare('ctx.latestAnswer')} <span className="cp-unknown">{t('ctx.unknown')}</span>. {t.bare('ctx.notMeasured')}</>}
                    </p>)}
                {perf && scope === 'chat' && <ChatTotals totals={perf.totals} />}
            </section>
        </>
    );
}

interface ContextPanelProps {
    sessionId: string;
    /** A reply is running; the panel refreshes once it ends. */
    busy: boolean;
    onClose: () => void;
    /** The measured usage, so the meter shows the same total as the panel. */
    onUsage?: (usage: ContextUsage) => void;
}

/** The context details as a sheet of their own, for hosts without the session panel. */
export function ContextPanel({ sessionId, busy, onClose, onUsage }: ContextPanelProps) {
    const t = useT();
    const closeRef = useRef<HTMLButtonElement>(null);
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;

    // Mount only: re-running on each parent render would pull focus back to Close.
    useEffect(() => {
        closeRef.current?.focus();
    }, []);
    const close = useCallback(() => onCloseRef.current(), []);
    useEscapeToClose(true, close);
    const sheetRef = useRef<HTMLElement>(null);
    useFocusTrap(sheetRef, true);

    return (
        <div className="cp-overlay" onClick={onClose}>
            <aside
                ref={sheetRef}
                className="cp-sheet"
                role="dialog"
                aria-modal="true"
                aria-label={t('ctx.sheetAria')}
                onClick={(e) => e.stopPropagation()}
            >
                <header className="cp-header">
                    <h2>{t('sp.section.context')}</h2>
                    <button ref={closeRef} type="button" className="cp-icon" onClick={onClose} aria-label={t('sp.closeShort')}>
                        <X size={16} />
                    </button>
                </header>
                <div className="cp-body">
                    <ContextDetails sessionId={sessionId} busy={busy} onUsage={onUsage} />
                </div>
            </aside>
        </div>
    );
}
