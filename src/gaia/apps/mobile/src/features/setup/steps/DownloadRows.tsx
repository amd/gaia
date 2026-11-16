// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { failureText, logAddsDetail, retryingSeconds, rowDetail, stalledSeconds, STATE_KEY } from '@webui/components/setup/downloadModel';
import { formatSize, STALL_STUCK_S, type Estimate } from '@webui/components/setup/format';
import type { MessageKey } from '@webui/i18n/registry';
import type { SetupComponent, SetupModelProgress, SetupPlan, SetupRunStatus } from '@webui/types';
import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Card, Disclosure, Icon, IconButton, Ltr, ProgressLine, Row, Spinner, Stack, Text } from '@/ui';
import { Check, ChevronDown, ChevronUp, CircleAlert, Circle, Pause, RotateCw, X } from '@/ui/icons';

import { collapseProgress } from '../sheets/logLines';

type RunState = 'active' | 'done' | 'failed' | undefined;

function Details({ text }: { text: string }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <Disclosure title={t('setup.progress.details')} open={open} onToggle={() => setOpen((o) => !o)}>
            <Text mono role="caption" dir="ltr" selectable>{text}</Text>
        </Disclosure>
    );
}

export interface ModelRowProps {
    model: SetupModelProgress;
    name: string;
    estimate: Estimate | undefined;
    now: number;
    freeGb: number | null;
    checking: boolean;
    checkingFor: number;
    /** The rest of the run is still pulling models: Retry waits for it. */
    retryLater: boolean;
    /** Nothing is moving, so a cancelled row offers Resume. */
    idle: boolean;
    busy: boolean;
    onRetry(m: SetupModelProgress): void;
    onResume(m: SetupModelProgress): void;
    onSkip(m: SetupModelProgress): void;
    onRestart(): void;
}

export function ModelRow({ model: m, name, estimate, now, freeGb, checking, checkingFor, retryLater, idle, busy, onRetry, onResume, onSkip, onRestart }: ModelRowProps) {
    const t = useT();
    const [showLog, setShowLog] = useState(false);
    const lines = m.log ?? [];
    const pct = m.bytes_total > 0 ? Math.min(100, (m.bytes_done / m.bytes_total) * 100) : m.state === 'ready' ? 100 : 0;
    const retryingFor = retryingSeconds(m, now);
    const stalled = stalledSeconds(m, estimate, retryingFor);
    const detail = rowDetail({ model: m, estimate, retryingFor, stalled, checking, checkingFor, t });
    const logs = m.state !== 'failed' && logAddsDetail(lines);
    const mark =
        m.state === 'ready' || m.state === 'unchecked' ? <Icon icon={Check} size="sm" tone="success" />
        : m.state === 'failed' ? <Icon icon={CircleAlert} size="sm" tone="danger" />
        : m.state === 'cancelled' ? <Icon icon={Pause} size="sm" tone="muted" />
        : m.state === 'downloading' || m.state === 'verifying' ? <Spinner />
        : <Icon icon={Circle} size="sm" tone="muted" />;
    return (
        <Stack gap="xs" testID={`download-row-${m.id}`}>
            <Row gap="sm" align="center">
                {mark}
                <Stack flex={1}>
                    <Text weight="medium" dir="auto">{name}</Text>
                </Stack>
                <Text role="caption" tone={m.state === 'failed' ? 'danger' : 'secondary'}>{t(STATE_KEY[m.state])}</Text>
                {m.state === 'downloading' ? <Ltr>{`${Math.floor(pct)}%`}</Ltr> : null}
                {logs ? (
                    <IconButton
                        icon={showLog ? ChevronUp : ChevronDown}
                        size="sm"
                        label={t('setup.progress.logFor', { name })}
                        onPress={() => setShowLog((v) => !v)}
                    />
                ) : null}
            </Row>
            <ProgressLine value={m.state === 'verifying' ? undefined : pct / 100} />
            {detail ? <Text role="caption" tone="secondary">{detail}</Text> : null}
            {showLog && logs ? <Text mono role="caption" dir="ltr" selectable>{collapseProgress(lines).slice(-12).join('\n')}</Text> : null}
            {m.state === 'failed' ? (
                <Stack gap="xs">
                    <Text tone="danger" accessibilityRole="alert">{failureText(m, name, freeGb, t)}</Text>
                    {lines.length > 0 || m.error ? <Details text={lines.length > 0 ? lines.join('\n') : m.error ?? ''} /> : null}
                    {retryLater ? (
                        <Text role="caption" tone="secondary">{t('setup.progress.retryLater')}</Text>
                    ) : (
                        <Row gap="sm" wrap>
                            <Button label={t('setup.progress.retry')} icon={RotateCw} variant="primary" size="sm" disabled={busy} onPress={() => onRetry(m)} />
                            <Button label={t('setup.progress.skipModel')} variant="secondary" size="sm" disabled={busy} onPress={() => onSkip(m)} />
                        </Row>
                    )}
                </Stack>
            ) : null}
            {m.state === 'cancelled' && idle ? (
                <Row gap="sm">
                    <Button label={t('setup.progress.resume')} variant="secondary" size="sm" disabled={busy} onPress={() => onResume(m)} />
                </Row>
            ) : null}
            {stalled >= STALL_STUCK_S ? (
                <Row gap="sm">
                    <Button label={t('setup.progress.retry')} icon={RotateCw} variant="secondary" size="sm" disabled={busy} onPress={onRestart} />
                </Row>
            ) : null}
        </Stack>
    );
}

function stateText(c: SetupComponent, state: RunState, t: ReturnType<typeof useT>): string {
    if (state === 'active') return t('setup.contents.working');
    if (state === 'done') return t('setup.contents.ready');
    if (state === 'failed') return t('setup.contents.failed');
    return c.installed ? t('setup.contents.alreadyHere') : t('setup.contents.waiting');
}

/** Everything that comes to this PC besides the models: what, how big, from where, and where it is. */
export function DownloadContents({ plan, run, onRetry, busy }: { plan: SetupPlan; run: SetupRunStatus; onRetry(): void; busy: boolean }) {
    const t = useT();
    const ended = run.state !== 'running' && run.state !== 'verifying';
    return (
        <Card accessibilityLabel={t('setup.contents.title')} testID="download-contents">
            <Stack gap="md">
                {(plan.components ?? []).map((c) => {
                    const reported = run.components?.[c.id] as RunState;
                    // A run that ended mid-step left that step unfinished, not still working.
                    const state = reported === 'active' && ended ? undefined : reported;
                    const name = t(`setup.component.${c.id}` as MessageKey);
                    const mark =
                        state === 'done' || (state == null && c.installed) ? <Icon icon={Check} size="sm" tone="success" />
                        : state === 'active' ? <Spinner />
                        : state === 'failed' ? <Icon icon={X} size="sm" tone="danger" />
                        : <Icon icon={Circle} size="sm" tone="muted" />;
                    return (
                        <Stack key={c.id} gap="xs">
                            <Row gap="sm" align="start">
                                {mark}
                                <Stack flex={1} gap="xs">
                                    <Text weight="medium">{name}</Text>
                                    <Text role="caption" tone="secondary">{t(`setup.component.${c.id}Note` as MessageKey)}</Text>
                                    {c.bundled ? (
                                        <Text role="caption" tone="muted">{t('setup.contents.bundled')}</Text>
                                    ) : (
                                        <Row gap="xs" wrap>
                                            <Text role="caption" tone="muted">{t('setup.contents.from')}</Text>
                                            <Ltr>{c.source}</Ltr>
                                        </Row>
                                    )}
                                </Stack>
                                <Stack align="end">
                                    <Ltr>{c.size_gb == null ? t('setup.contents.sizeOnDownload') : formatSize(c.size_gb)}</Ltr>
                                    <Text role="caption" tone="secondary">{stateText(c, state, t)}</Text>
                                </Stack>
                            </Row>
                            {state === 'failed' && ended && !run.problem ? (
                                <Stack gap="xs">
                                    <Text tone="danger" accessibilityRole="alert">
                                        {t(run.component_kinds?.[c.id] === 'transient' ? 'setup.contents.failedNetwork' : 'setup.contents.failedOther', { name })}
                                    </Text>
                                    {run.error ? <Details text={run.error} /> : null}
                                    <Row gap="sm">
                                        <Button
                                            label={t('setup.progress.retry')}
                                            accessibilityLabel={t('setup.contents.retryNamed', { name })}
                                            variant="primary"
                                            size="sm"
                                            disabled={busy}
                                            onPress={onRetry}
                                        />
                                    </Row>
                                </Stack>
                            ) : null}
                        </Stack>
                    );
                })}
            </Stack>
        </Card>
    );
}

export { Details };
