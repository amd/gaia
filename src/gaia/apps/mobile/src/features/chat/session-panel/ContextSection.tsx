// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';

import type { ChatPerformance, ContextUsage } from '@/api/client.types';
import { useChatPerformance, useContextSection } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { useT, type Translate } from '@/i18n/useT';
import { Icon, Notice, Pressable, ProgressLine, Row, SegmentedControl, Spacer, Stack, Text } from '@/ui';
import { ChevronDown, ChevronRight } from '@/ui/icons';
import { isolate, ref } from '@webui/i18n/registry';
import type { TurnPerformance } from '@webui/types';
import {
    exact,
    hitRate,
    latestTurn,
    MIN_TOKENS_TO_RATE,
    pct,
    pctLabel,
    placement,
    secondsShort,
    tokensShort,
} from '@webui/utils/contextPanel.core';

import { failureReason } from '../header/failureText';
import { PanelSection } from './PanelSection';
import { useSessionPanelStore } from './panelStore';
import type { SectionProps } from './sections';

export function ContextSection({ sessionId, live }: SectionProps) {
    const t = useT();
    const open = useSessionPanelStore((s) => s.sections.context);
    const reading = useContextSection(sessionId, { enabled: open, live });
    const perf = useChatPerformance(sessionId, { enabled: open, live });
    return (
        <PanelSection id="context" title={t('sp.section.context')}>
            <Stack gap="lg">
                <WindowFill reading={reading} perf={perf.data} />
                <UnderTheHood perf={perf.data} error={perf.error} />
            </Stack>
        </PanelSection>
    );
}

/** A tap target that swaps a short value for its exact one, in place of the web's hover title. */
function Reveal({ short, full, tone }: { short: string; full: string; tone?: 'accent' | 'muted' }) {
    const [shown, setShown] = useState(false);
    return (
        <Pressable
            onPress={() => setShown((v) => !v)}
            accessibilityRole="button"
            accessibilityLabel={full}
            accessibilityState={{ expanded: shown }}
        >
            <Text tone={tone ?? 'accent'}>{isolate(shown ? full : short, 'ltr')}</Text>
        </Pressable>
    );
}

function Tokens({ n }: { n: number | null | undefined }) {
    const t = useT();
    if (n == null) return <Text tone="muted">{t('ctx.unknown')}</Text>;
    if (n < 1000) return <Text>{isolate(String(n), 'ltr')}</Text>;
    return <Reveal short={tokensShort(n)} full={t('ctx.tokens', { n: exact(n) })} />;
}

function Seconds({ s }: { s: number | null | undefined }) {
    const t = useT();
    if (s == null) return <Text tone="muted">{t('ctx.unknown')}</Text>;
    return <Reveal short={secondsShort(s)} full={`${(s * 1000).toFixed(0)} ms`} />;
}

function Rate({ r }: { r: number | null | undefined }) {
    const t = useT();
    if (r == null) return <Text tone="muted">{t('ctx.unknown')}</Text>;
    return <Reveal short={t('ctx.rate', { n: Math.round(r).toLocaleString('en-US') })} full={t('ctx.rateTitle', { n: r })} />;
}

/** "unknown", with the reason a tap away when one is known. */
function Unknown({ why }: { why?: string }) {
    const t = useT();
    return why ? <Reveal short={t('ctx.unknown')} full={why} tone="muted" /> : <Text tone="muted">{t('ctx.unknown')}</Text>;
}

/** Label at the start, value at the end; the value drops to its own line when they don't fit. */
function Line({ label, children, hint, indent }: { label: string; children: ReactNode; hint?: string; indent?: boolean }) {
    return (
        <Stack gap="xs">
            <Row wrap gap="sm">
                <Text tone="secondary">{indent ? `· ${label}` : label}</Text>
                <Spacer />
                <Row wrap gap="xs">
                    {children}
                </Row>
            </Row>
            {hint ? (
                <Text role="caption" tone="muted">
                    {hint}
                </Text>
            ) : null}
        </Stack>
    );
}

function Heading({ children }: { children: string }) {
    return (
        <Text weight="semibold" accessibilityRole="header">
            {children}
        </Text>
    );
}

function WindowFill({ reading, perf }: { reading: ReturnType<typeof useContextSection>; perf: ChatPerformance | undefined }) {
    const t = useT();
    const { usage, unavailable, error } = reading;
    return (
        <Stack gap="sm">
            <Heading>{t('ctx.context')}</Heading>
            {isPcRefusal(error) ? <PcOnlyNotice testID="context-pc-only" /> : error ? <Notice tone="danger" message={failureReason(error, t)} /> : null}
            {!usage && !error ? <Text tone="muted">{unavailable ?? t('ctx.measuring')}</Text> : null}
            {usage ? <Usage usage={usage} perf={perf} t={t} /> : null}
        </Stack>
    );
}

function Usage({ usage, perf, t }: { usage: ContextUsage; perf: ChatPerformance | undefined; t: Translate }) {
    const [toolsOpen, setToolsOpen] = useState(false);
    const free = Math.max(0, usage.window - usage.used);
    const latest = perf ? latestTurn(perf) : null;
    const away = usage.context_state?.put_away;
    const folded = usage.context_state?.summarized;
    const loading = usage.tool_loading;
    return (
        <Stack gap="sm">
            <ProgressLine value={usage.window > 0 ? usage.used / usage.window : 0} label={t('ctx.used', { pct: pct(usage.used, usage.window) })} />
            <Row wrap gap="xs">
                <Tokens n={usage.used} />
                <Text>{t.bare('ctx.of')}</Text>
                <Tokens n={usage.window} />
                <Text>{t.bare('ctx.tokensWord')}</Text>
                {usage.tokenizer && !usage.tokenizer.exact ? <Text tone="muted">{`· ${t.bare('ctx.approx')}`}</Text> : null}
            </Row>
            {usage.model ? <Text mono tone="muted">{isolate(usage.model, 'ltr')}</Text> : null}
            {usage.model_window != null && usage.model_window > usage.window ? (
                <Row wrap gap="xs">
                    <Text role="caption" tone="muted">{t.bare('ctx.gaiaUses')}</Text>
                    <Tokens n={usage.window} />
                    <Text role="caption" tone="muted">{t.bare('ctx.ofModel')}</Text>
                    <Tokens n={usage.model_window} />
                </Row>
            ) : null}

            {(usage.categories ?? []).map((c) => (
                <Line key={c.key} label={c.label}>
                    <Tokens n={c.tokens} />
                    <Text tone="muted">{`${pctLabel(c.tokens, usage.window)}%`}</Text>
                </Line>
            ))}
            <Line label={t('ctx.freeRow')}>
                <Tokens n={free} />
                <Text tone="muted">{`${pct(free, usage.window)}%`}</Text>
            </Line>
            <Line label={t('ctx.reserve')} indent>
                <Tokens n={Math.min(free, usage.reply_reserve)} />
            </Line>

            <Text role="caption" tone="muted">
                {usage.tokenizer?.exact
                    ? t('ctx.exactTokenizer', { model: isolate(usage.tokenizer.model, 'ltr') })
                    : t('ctx.estimated', { reason: usage.tokenizer?.reason ?? ref('ctx.sharedTokenizer') })}
                {latest?.last_prompt_tokens != null
                    ? ` ${t('ctx.lastRequest', { n: isolate(exact(latest.last_prompt_tokens), 'ltr') })}`
                    : ''}
            </Text>

            {loading && loading.on_demand.length > 0 ? (
                <Stack gap="xs">
                    <Pressable
                        onPress={() => setToolsOpen((v) => !v)}
                        accessibilityRole="button"
                        accessibilityState={{ expanded: toolsOpen }}
                    >
                        <Row gap="sm">
                            <Icon icon={toolsOpen ? ChevronDown : ChevronRight} size="sm" tone="muted" />
                            <Text weight="medium" tone="secondary">{t('ctx.onDemand', { n: loading.on_demand.length })}</Text>
                        </Row>
                    </Pressable>
                    {toolsOpen ? (
                        <Stack gap="xs">
                            <Text role="caption" tone="muted">{t('ctx.sentNow', { n: loading.loaded.length })}</Text>
                            {loading.on_demand.map((name) => (
                                <Text key={name} mono>{isolate(name, 'ltr')}</Text>
                            ))}
                        </Stack>
                    ) : null}
                </Stack>
            ) : null}

            <Line label={t('ctx.live')}>
                <Tokens n={usage.used} />
            </Line>
            {away && away.results > 0 ? (
                <Line label={t('ctx.putAway')} hint={t('ctx.putAwayHint')}>
                    <Text>{t.plural('ctx.results', away.results)}</Text>
                    <Text tone="muted">·</Text>
                    <Tokens n={away.tokens} />
                </Line>
            ) : null}
            {folded && folded.turns > 0 ? (
                <>
                    <Line label={t('ctx.summarized')} hint={t('ctx.summarizedHint')}>
                        <Text>{t.plural('ctx.earlier', folded.turns)}</Text>
                        <Text tone="muted">·</Text>
                        <Tokens n={folded.tokens} />
                        <Text tone="muted">→</Text>
                        <Tokens n={folded.summary_tokens} />
                    </Line>
                    <Text role="caption" tone="muted">{t('ctx.summarizedNote')}</Text>
                </>
            ) : null}
            {usage.at_risk.length > 0 ? <Notice tone="warning" message={t('ctx.filling')} /> : null}
        </Stack>
    );
}

function Cost({ cost, t }: { cost: TurnPerformance['cost']; t: Translate }) {
    if (cost.kind === 'local') return <Reveal short={t('mobileApp.ctx.costLocal')} full={t('mobileApp.ctx.costLocalTitle')} />;
    if (cost.kind === 'unreported') return <Text tone="muted">{t('ctx.costUnreported')}</Text>;
    if (cost.kind === 'estimate' && cost.usd != null) {
        const usd = cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2);
        return <Reveal short={t('ctx.about', { usd })} full={t('ctx.listPriceTitle', { usd: cost.usd })} />;
    }
    return <Text tone="muted">{t('ctx.unknown')}</Text>;
}

function TokensInOut({ tin, tout }: { tin: number | null; tout: number | null }) {
    return (
        <>
            <Tokens n={tin} />
            <Text tone="muted">→</Text>
            <Tokens n={tout} />
        </>
    );
}

function CacheValue({ cached, total }: { cached: number | null; total: number | null }) {
    const hit = hitRate(cached, total);
    return (
        <>
            <Tokens n={cached} />
            {hit != null ? <Text tone="muted">{`(${hit}%)`}</Text> : null}
        </>
    );
}

function TimeSplit({ time, t }: { time: TurnPerformance['time']; t: Translate }) {
    if (time.model_seconds == null || time.gaia_seconds == null) {
        return (
            <Line label={t('ctx.took')}>
                <Seconds s={time.wall_seconds} />
                <Text tone="muted">{`· ${t.bare('ctx.split')}`}</Text>
                <Text tone="muted">{t('ctx.unknown')}</Text>
            </Line>
        );
    }
    const parts = [
        { key: 'tools', label: t('ctx.runningTools'), s: time.tool_seconds },
        { key: 'waiting', label: t('ctx.waitingOnYou'), s: time.waiting_seconds },
        { key: 'gaia', label: t('ctx.gaiaItself'), s: time.own_seconds ?? 0 },
    ];
    return (
        <>
            <Line label={t('ctx.took')} hint={t('ctx.tookHint')}>
                <Seconds s={time.wall_seconds} />
            </Line>
            <Line label={t('ctx.inModel')} indent>
                <Seconds s={time.model_seconds} />
            </Line>
            <Line label={t('ctx.inGaia')} hint={t('ctx.inGaiaHint')} indent>
                <Seconds s={time.gaia_seconds} />
            </Line>
            {parts.filter((p) => p.s > 0).map((p) => (
                <Line key={p.key} label={p.label} indent>
                    <Seconds s={p.s} />
                </Line>
            ))}
        </>
    );
}

function TurnStats({ turn, t }: { turn: TurnPerformance; t: Translate }) {
    const why = turn.unknown ?? {};
    const toolList = turn.tools.map((x) => `${x.name ?? t('ctx.toolOutside')}: ${x.seconds ?? '?'} s`).join('\n');
    const loadList = turn.loads
        .map((l) => `${l.model}${l.replaced?.length ? ` replaced ${l.replaced.join(', ')}` : ''}: ${l.seconds} s`)
        .join('\n');
    return (
        <Stack gap="sm">
            <TimeSplit time={turn.time} t={t} />
            <Line label={t('ctx.firstWord')}>
                {turn.first_token_seconds == null ? (
                    <Unknown why={why.first_token_seconds} />
                ) : (
                    <>
                        <Text>{t.bare('ctx.in')}</Text>
                        <Seconds s={turn.first_token_seconds} />
                    </>
                )}
            </Line>
            <Line label={t('ctx.readingPrompt')} hint={t('ctx.readingHint')}>
                {turn.tokens_new != null && turn.tokens_new < MIN_TOKENS_TO_RATE ? (
                    <Reveal short={t('ctx.onlyNew', { n: turn.tokens_new })} full={t('ctx.tooFewTitle')} tone="muted" />
                ) : turn.prefill_tokens_per_second == null ? (
                    <Unknown why={why.prefill_tokens_per_second} />
                ) : (
                    <>
                        <Rate r={turn.prefill_tokens_per_second} />
                        {turn.prefill_source === 'client' ? <Reveal short={t('ctx.approx')} full={t('ctx.approxTitle')} tone="muted" /> : null}
                    </>
                )}
            </Line>
            <Line label={t('ctx.writing')}>
                {turn.generation_tokens_per_second == null ? <Unknown why={why.generation_tokens_per_second} /> : <Rate r={turn.generation_tokens_per_second} />}
            </Line>
            <Line label={t('ctx.tokensInOut')}>
                <TokensInOut tin={turn.tokens_in} tout={turn.tokens_out} />
            </Line>
            <Line label={t('ctx.cache')} hint={t('ctx.cacheHint')}>
                {turn.tokens_cached == null ? <Unknown why={why.tokens_cached} /> : <CacheValue cached={turn.tokens_cached} total={turn.tokens_in} />}
            </Line>
            <Line label={t('ctx.modelCalls')}>
                <Text>{String(turn.model_calls)}</Text>
            </Line>
            <Line label={t('ctx.toolCalls')}>
                {turn.tool_calls === 0 && !turn.tool_seconds_total ? (
                    <Text>{t('ctx.none')}</Text>
                ) : (
                    <>
                        {toolList ? <Reveal short={String(turn.tool_calls)} full={toolList} /> : <Text>{String(turn.tool_calls)}</Text>}
                        <Text tone="muted">·</Text>
                        <Seconds s={turn.tool_seconds_total ?? 0} />
                    </>
                )}
            </Line>
            <Line label={t('ctx.loads')}>
                {turn.loads.length === 0 ? (
                    <Text>{t('ctx.none')}</Text>
                ) : (
                    <>
                        <Reveal short={String(turn.loads.length)} full={loadList} />
                        <Text tone="muted">·</Text>
                        <Seconds s={turn.loads.reduce((a, l) => a + l.seconds, 0)} />
                    </>
                )}
            </Line>
            <Line label={t('rail.cost')}>
                <Cost cost={turn.cost} t={t} />
            </Line>
        </Stack>
    );
}

function ChatTotals({ totals, t }: { totals: ChatPerformance['totals']; t: Translate }) {
    return (
        <Stack gap="sm">
            <Line label={t('ctx.turns')}>
                <Text>{String(totals.turns)}</Text>
            </Line>
            <Line label={t('ctx.totalTime')}>
                <Seconds s={totals.wall_seconds} />
            </Line>
            <Line label={t('ctx.inModel')}>
                <Seconds s={totals.model_seconds} />
            </Line>
            <Line label={t('ctx.inGaia')}>
                <Seconds s={totals.gaia_seconds} />
            </Line>
            <Line label={t('ctx.tokensInOut')}>
                <TokensInOut tin={totals.tokens_in} tout={totals.tokens_out} />
            </Line>
            <Line label={t('ctx.cache')}>
                {totals.tokens_cached == null ? <Text tone="muted">{t('ctx.unknown')}</Text> : <CacheValue cached={totals.tokens_cached} total={totals.tokens_in} />}
            </Line>
            <Line label={t('ctx.toolCalls')}>
                {totals.tool_calls == null ? <Text tone="muted">{t('ctx.unknown')}</Text> : <Text>{String(totals.tool_calls)}</Text>}
            </Line>
            <Line label={t('ctx.loads')}>
                {totals.loads === 0 ? (
                    <Text>{t('ctx.none')}</Text>
                ) : (
                    <>
                        <Text>{String(totals.loads)}</Text>
                        <Text tone="muted">·</Text>
                        <Seconds s={totals.load_seconds} />
                    </>
                )}
            </Line>
            <Line label={t('rail.cost')}>
                {totals.usd == null ? (
                    <Text tone="muted">{t('ctx.unknown')}</Text>
                ) : totals.unpriced_turns > 0 ? (
                    <Reveal short={t('ctx.unpriced', { usd: totals.usd.toFixed(2), n: totals.unpriced_turns })} full={t('ctx.unpricedTitle', { n: totals.unpriced_turns })} />
                ) : totals.usd === 0 ? (
                    <Text>$0</Text>
                ) : (
                    <Text>{t('ctx.about', { usd: totals.usd.toFixed(2) })}</Text>
                )}
            </Line>
        </Stack>
    );
}

function UnderTheHood({ perf, error }: { perf: ChatPerformance | undefined; error: unknown }) {
    const t = useT();
    const scope = useSessionPanelStore((s) => s.contextScope);
    const setScope = useSessionPanelStore((s) => s.setContextScope);
    const latest = perf ? latestTurn(perf) : null;
    return (
        <Stack gap="sm">
            <Heading>{t('ctx.hood')}</Heading>
            <SegmentedControl
                accessibilityLabel={t('ctx.show')}
                options={[
                    { value: 'turn', label: t.bare('ctx.lastTurn'), accessibilityLabel: t('ctx.lastTurn') },
                    { value: 'chat', label: t.bare('sp.title'), accessibilityLabel: t('sp.title') },
                ]}
                value={scope}
                onChange={(v) => setScope(v === 'chat' ? 'chat' : 'turn')}
            />
            {isPcRefusal(error) ? <PcOnlyNotice testID="performance-pc-only" /> : error ? <Notice tone="danger" message={failureReason(error, t)} /> : null}
            {!perf && !error ? <Text tone="muted">{t('ctx.measuring')}</Text> : null}
            {perf ? <Now now={perf.now} t={t} /> : null}
            {perf && scope === 'turn' ? (
                latest ? (
                    <TurnStats turn={latest} t={t} />
                ) : perf.latest_message_id == null ? (
                    <Text tone="muted">{t('ctx.afterNext')}</Text>
                ) : (
                    <Text tone="muted">{`${t.bare('ctx.latestAnswer')} ${t('ctx.unknown')}. ${t.bare('ctx.notMeasured')}`}</Text>
                )
            ) : null}
            {perf && scope === 'chat' ? <ChatTotals totals={perf.totals} t={t} /> : null}
        </Stack>
    );
}

function Now({ now, t }: { now: ChatPerformance['now']; t: Translate }) {
    return (
        <>
            <Line label={t('env.runsOn')}>
                {now.error ? <Reveal short={placement(now, t)} full={now.error} /> : <Text>{placement(now, t)}</Text>}
            </Line>
            {now.provider === 'local' ? (
                <Line label={t('ctx.memory')} hint={t('ctx.memoryHint')}>
                    {now.weights_gb == null ? (
                        <Text tone="muted">{t('ctx.unknown')}</Text>
                    ) : (
                        <Text>
                            {isolate(
                                `${t.bare('ctx.gbModel', { gb: now.weights_gb.toFixed(1) })}${now.kv_cache_gb != null ? ` + ${t.bare('ctx.gbCache', { gb: now.kv_cache_gb.toFixed(1) })}` : ''}`,
                                'ltr',
                            )}
                        </Text>
                    )}
                    <Text tone="muted">·</Text>
                    {now.free_gb == null ? (
                        <Text tone="muted">{t('ctx.freeUnknown')}</Text>
                    ) : (
                        <Text>{isolate(t.bare('env.free', { gb: now.free_gb.toFixed(1) }), 'ltr')}</Text>
                    )}
                </Line>
            ) : null}
        </>
    );
}
