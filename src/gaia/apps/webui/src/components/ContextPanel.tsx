// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import * as api from '../services/api';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { cloudProviderName } from '../stores/modelStore';
import { log } from '../utils/logger';
import type { ChatPerformance, ContextUsage, TurnPerformance } from '../types';
import './ContextPanel.css';

const UNKNOWN = 'unknown';

const exact = (n: number) => n.toLocaleString('en-US');

/** "1.2K", with the exact count on hover. */
function Tokens({ n }: { n: number | null | undefined }) {
    if (n == null) return <span className="cp-unknown">{UNKNOWN}</span>;
    const short = n < 1000
        ? String(n)
        : n < 1_000_000
            ? `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`
            : `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
    return <span title={`${exact(n)} tokens`}>{short}</span>;
}

/** "1.2 s", with milliseconds on hover. */
function Seconds({ s }: { s: number | null | undefined }) {
    if (s == null) return <span className="cp-unknown">{UNKNOWN}</span>;
    const short = s < 0.1 ? (s === 0 ? '0 s' : '<0.1 s') : s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
    return <span title={`${(s * 1000).toFixed(0)} ms`}>{short}</span>;
}

function Rate({ r }: { r: number | null | undefined }) {
    if (r == null) return <span className="cp-unknown">{UNKNOWN}</span>;
    return <span title={`${r} tokens per second`}>{Math.round(r).toLocaleString('en-US')} tokens/s</span>;
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

function placement(now: ChatPerformance['now']): string {
    if (now.provider === 'unknown') return UNKNOWN;
    if (now.provider === 'cloud') return 'Cloud';
    if (now.provider !== 'local') return `Cloud · ${cloudProviderName(now.provider)}`;
    if (!now.device) return now.loaded === false ? 'Not loaded now' : UNKNOWN;
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
function TimeSplit({ t }: { t: TurnPerformance['time'] }) {
    if (t.model_seconds == null || t.gaia_seconds == null) {
        return (
            <Row label="Took"><Seconds s={t.wall_seconds} /> · split <span className="cp-unknown">{UNKNOWN}</span></Row>
        );
    }
    const parts = [
        { key: 'model', label: 'In the model', s: t.model_seconds },
        { key: 'tools', label: 'Running tools', s: t.tool_seconds },
        { key: 'waiting', label: 'Waiting on you', s: t.waiting_seconds },
        { key: 'gaia', label: 'GAIA itself', s: t.own_seconds ?? 0 },
    ];
    return (
        <>
            <Row label="Took" hint="From sending the message to the answer being saved">
                <Seconds s={t.wall_seconds} />
            </Row>
            <div className="cp-stack" aria-hidden="true">
                {parts.map((p) => (
                    <span key={p.key} className={`cp-time-${p.key}`} style={{ width: `${pct(p.s, t.wall_seconds)}%` }} />
                ))}
            </div>
            <dl className="cp-rows cp-indent">
                <Row label="In the model"><Seconds s={t.model_seconds} /></Row>
                <Row label="In GAIA" hint="Everything that was not the model: tools, waiting on you, and GAIA's own work">
                    <Seconds s={t.gaia_seconds} />
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
    return <span className="cp-unknown" title={why}>{UNKNOWN}</span>;
}

const SOURCE_HINT = {
    server: 'Reported by the model server',
    client: 'Measured by GAIA on the stream, network included',
} as const;

function sourceHint(source: 'server' | 'client' | null | undefined): string | undefined {
    return source ? SOURCE_HINT[source] : undefined;
}

function TurnStats({ turn }: { turn: TurnPerformance }) {
    const hit = hitRate(turn.tokens_cached, turn.tokens_in);
    const why = turn.unknown ?? {};
    const toolList = turn.tools.map((t) => `${t.name ?? 'tool work outside a step'}: ${t.seconds ?? '?'} s`).join('\n');
    return (
        <>
            <TimeSplit t={turn.time} />
            <dl className="cp-rows">
                <Row label="First word" hint={sourceHint(turn.first_token_source)}>
                    {turn.first_token_seconds == null
                        ? <Unknown why={why.first_token_seconds} />
                        : <>in <Seconds s={turn.first_token_seconds} /></>}
                </Row>
                <Row label="Reading the prompt" hint="Prefill: new prompt tokens over time to first token">
                    {turn.tokens_new != null && turn.tokens_new < MIN_TOKENS_TO_RATE
                        ? <span title="Too few new tokens to give a reading speed; the rest came from the cache">
                            only {turn.tokens_new} new tokens
                        </span>
                        : turn.prefill_tokens_per_second == null
                            ? <Unknown why={why.prefill_tokens_per_second} />
                            : <>
                                <Rate r={turn.prefill_tokens_per_second} />
                                {turn.prefill_source === 'client' && (
                                    <span className="cp-chip" title="Measured by GAIA from the first word, so it includes network and queue time">approx.</span>
                                )}
                            </>}
                </Row>
                <Row label="Writing the answer" hint={sourceHint(turn.generation_source) ?? 'Generation speed, weighted by tokens'}>
                    {turn.generation_tokens_per_second == null
                        ? <Unknown why={why.generation_tokens_per_second} />
                        : <Rate r={turn.generation_tokens_per_second} />}
                </Row>
                <Row label="Tokens in → out" hint={why.tokens}>
                    <Tokens n={turn.tokens_in} /> → <Tokens n={turn.tokens_out} />
                </Row>
                <Row label="Reused from cache" hint="Prompt tokens the server did not have to read again">
                    {turn.tokens_cached == null
                        ? <Unknown why={why.tokens_cached} />
                        : <><Tokens n={turn.tokens_cached} />{hit != null && ` (${hit}%)`}</>}
                </Row>
                <Row label="Model calls">{turn.model_calls}</Row>
                <Row label="Tool calls" hint={toolList || undefined}>
                    {turn.tool_calls === 0 && !turn.tool_seconds_total
                        ? 'none'
                        : <>{turn.tool_calls} · <Seconds s={turn.tool_seconds_total ?? 0} /></>}
                </Row>
                <Row label="Model loads">
                    {turn.loads.length === 0 ? 'none' : (
                        <span title={turn.loads.map((l) => `${l.model}${l.replaced?.length ? ` replaced ${l.replaced.join(', ')}` : ''}: ${l.seconds} s`).join('\n')}>
                            {turn.loads.length} · <Seconds s={turn.loads.reduce((a, l) => a + l.seconds, 0)} />
                        </span>
                    )}
                </Row>
                <Row label="Cost"><Cost cost={turn.cost} /></Row>
            </dl>
        </>
    );
}

function Cost({ cost }: { cost: TurnPerformance['cost'] }) {
    if (cost.kind === 'local') return <span title="Runs on this PC">$0 · on this PC</span>;
    if (cost.kind === 'estimate' && cost.usd != null) {
        return <span title={`$${cost.usd} at list price`}>about ${cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2)}</span>;
    }
    return <span className="cp-unknown">{UNKNOWN}</span>;
}

function ChatTotals({ totals }: { totals: ChatPerformance['totals'] }) {
    const hit = hitRate(totals.tokens_cached, totals.tokens_in);
    return (
        <dl className="cp-rows">
            <Row label="Turns measured">{totals.turns}</Row>
            <Row label="Total time"><Seconds s={totals.wall_seconds} /></Row>
            <Row label="In the model"><Seconds s={totals.model_seconds} /></Row>
            <Row label="In GAIA"><Seconds s={totals.gaia_seconds} /></Row>
            <Row label="Tokens in → out"><Tokens n={totals.tokens_in} /> → <Tokens n={totals.tokens_out} /></Row>
            <Row label="Reused from cache">
                {totals.tokens_cached == null ? <span className="cp-unknown">{UNKNOWN}</span> : <><Tokens n={totals.tokens_cached} />{hit != null && ` (${hit}%)`}</>}
            </Row>
            <Row label="Tool calls">{totals.tool_calls}</Row>
            <Row label="Model loads">{totals.loads === 0 ? 'none' : <>{totals.loads} · <Seconds s={totals.load_seconds} /></>}</Row>
            <Row label="Cost">
                {totals.unpriced_turns > 0
                    ? <span title={`${totals.unpriced_turns} turn(s) on a model with no known price`}>${totals.usd.toFixed(2)} + {totals.unpriced_turns} unpriced</span>
                    : totals.usd === 0 ? '$0' : `about $${totals.usd.toFixed(2)}`}
            </Row>
        </dl>
    );
}

/** What is live in the window, and what the agent reports it moved out (summaries only once that exists). */
function BeyondTheWindow({ usage }: { usage: ContextUsage }) {
    const away = usage.context_state?.put_away;
    const folded = usage.context_state?.summarized;
    return (
        <div className="cp-full">
            <dl className="cp-rows">
                <Row label="Live in the window"><Tokens n={usage.used} /></Row>
                {away && away.results > 0 && (
                    <Row
                        label="Put away"
                        hint="Older tool results moved out of the prompt; still readable"
                    >
                        {away.results} {away.results === 1 ? 'result' : 'results'} · <Tokens n={away.tokens} />
                    </Row>
                )}
                {folded && folded.turns > 0 && (
                    <Row label="Summarized" hint="Older turns folded into a running summary; the originals stay saved">
                        {folded.turns} earlier {folded.turns === 1 ? 'turn' : 'turns'} · <Tokens n={folded.tokens} />
                        {' → '}<Tokens n={folded.summary_tokens} />
                    </Row>
                )}
            </dl>
            {folded && folded.turns > 0 && (
                <p className="cp-note cp-summarized">Earlier messages summarized — still searchable.</p>
            )}
            {usage.at_risk.length > 0 && (
                <p className="cp-warning" role="status">
                    The window is filling up. Older tool results move out of the prompt and stay
                    readable, and the oldest turns may drop out of the model's view. They stay saved,
                    and you can ask GAIA to recall them.
                </p>
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
                <h3 id="cp-context">Context</h3>
                {usageError && <p className="cp-error" role="alert">{usageError}</p>}
                {!usage && !usageError && <p className="cp-note">{unavailable ?? 'Measuring…'}</p>}
                {usage && (
                    <>
                        <p className="cp-lead">
                            <strong>{pct(usage.used, usage.window)}% used</strong>
                            {' · '}<Tokens n={usage.used} /> of <Tokens n={usage.window} /> tokens
                            {usage.model && <> · <span className="cp-model">{usage.model}</span></>}
                            {usage.model_window != null && usage.model_window > usage.window && (
                                <span className="cp-window-note" title="GAIA admits this much of the window, so older turns drop out of view sooner than the model would need">
                                    {' '}· GAIA uses <Tokens n={usage.window} /> of the model's <Tokens n={usage.model_window} />
                                </span>
                            )}
                            {usage.tokenizer && !usage.tokenizer.exact && (
                                <span className="cp-chip" title={usage.tokenizer.reason}>approx.</span>
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
                                <dt><span className="swatch cp-cat-free" aria-hidden="true" />Free</dt>
                                <dd><Tokens n={free} /> <span className="cp-pct">{pct(free, usage.window)}%</span></dd>
                            </div>
                            <div className="cp-row is-quiet cp-indent">
                                <dt>of which kept for the reply</dt>
                                <dd><Tokens n={Math.min(free, usage.reply_reserve)} /></dd>
                            </div>
                        </dl>
                        <p className="cp-note">
                            {usage.tokenizer?.exact
                                ? `Each part counted with ${usage.tokenizer.model}'s own tokenizer.`
                                : `Estimated: ${usage.tokenizer?.reason ?? 'counted with a shared tokenizer.'}`}
                            {latest?.last_prompt_tokens != null && (
                                <>
                                    {' '}The last request carried{' '}
                                    <span title={`${exact(latest.last_prompt_tokens)} tokens`}>
                                        {exact(latest.last_prompt_tokens)}
                                    </span>{' '}
                                    tokens as the model server counted them, chat formatting included.
                                </>
                            )}
                        </p>
                        {usage.tool_loading && usage.tool_loading.on_demand.length > 0 && (
                            <details className="cp-details">
                                <summary>
                                    {usage.tool_loading.on_demand.length} more tools load only when needed
                                </summary>
                                <p className="cp-note">
                                    Sent now: {usage.tool_loading.loaded.length}. These cost nothing until a turn needs them:
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
                    <h3 id="cp-hood">Under the hood</h3>
                    <div className="cp-toggle" role="group" aria-label="Show">
                        <button type="button" aria-pressed={scope === 'turn'} onClick={() => setScope('turn')}>Last turn</button>
                        <button type="button" aria-pressed={scope === 'chat'} onClick={() => setScope('chat')}>This chat</button>
                    </div>
                </div>
                {perf && (
                    <dl className="cp-rows">
                        <Row label="Runs on" hint={perf.now.error ?? undefined}>{placement(perf.now)}</Row>
                        {perf.now.provider === 'local' && (
                            <Row label="Model memory" hint="Model weights + KV cache at this window; free memory now">
                                {perf.now.weights_gb == null
                                    ? <span className="cp-unknown">{UNKNOWN}</span>
                                    : <>
                                        {perf.now.weights_gb.toFixed(1)} GB model
                                        {perf.now.kv_cache_gb != null && ` + ${perf.now.kv_cache_gb.toFixed(1)} GB cache`}
                                    </>}
                                {' · '}
                                {perf.now.free_gb == null ? <span className="cp-unknown">free {UNKNOWN}</span> : `${perf.now.free_gb.toFixed(1)} GB free`}
                            </Row>
                        )}
                    </dl>
                )}
                {perfError && <p className="cp-error" role="alert">{perfError}</p>}
                {!perf && !perfError && <p className="cp-note">Measuring…</p>}
                {perf && scope === 'turn' && (latest
                    ? <TurnStats turn={latest} />
                    : <p className="cp-note">
                        {perf.latest_message_id == null
                            ? "Shown after GAIA's next answer in this chat."
                            : <>Latest answer: <span className="cp-unknown">{UNKNOWN}</span>. It was not measured.</>}
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
    const closeRef = useRef<HTMLButtonElement>(null);
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;

    // Mount only: re-running on each parent render would pull focus back to Close.
    useEffect(() => {
        closeRef.current?.focus();
    }, []);
    const close = useCallback(() => onCloseRef.current(), []);
    useEscapeToClose(true, close);

    return (
        <div className="cp-overlay" onClick={onClose}>
            <aside
                className="cp-sheet"
                role="dialog"
                aria-modal="true"
                aria-label="Context and performance"
                onClick={(e) => e.stopPropagation()}
            >
                <header className="cp-header">
                    <h2>Context &amp; performance</h2>
                    <button ref={closeRef} type="button" className="cp-icon" onClick={onClose} aria-label="Close">
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
