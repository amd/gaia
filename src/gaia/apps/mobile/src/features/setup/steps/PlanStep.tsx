// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, type ReactNode } from 'react';

import { formatFitGb, formatSize, ltrIn } from '@webui/components/setup/format';
import { initialSelection } from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupComponent, SetupPlan, SetupRunStatus } from '@webui/types';

import type { SetupRunOptions } from '@/api/client.types';

import { GaiaApiError } from '@/api/errors';
import { useApplyTestedBackend, useCloudProviders, useSetupPlan, useSetupPreflight, useStartSetup } from '@/data';
import { describeError, useLocaleCode, useT, type Translate } from '@/i18n/useT';
import { Button, Card, Disclosure, Ltr, Notice, Pressable, Row, Spinner, Stack, Text } from '@/ui';

import { SetupFooter } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { cloudIsBlocked, planView, startOptions, type PlanView } from './plan';
import { usePlanOpeners } from './planOpeners';
import { usePlanSelection } from './planSelection';

type Where = 'local' | 'cloud';
type ContentState = 'active' | 'done' | 'failed' | undefined;

/** A choice card: a radio row that names it, and what it offers below. */
function RadioCard({ where, picked, disabled, label, onPick, children, name }: {
    where: Where;
    picked: boolean;
    disabled: boolean;
    label: string;
    name: string;
    onPick: (w: Where) => void;
    children: ReactNode;
}) {
    return (
        <Card accessibilityLabel={label} testID={`plan-card-${where}`}>
            <Stack gap="sm">
                <Pressable
                    onPress={() => onPick(where)}
                    disabled={disabled}
                    accessibilityRole="radio"
                    accessibilityLabel={name}
                    accessibilityState={{ selected: picked, disabled }}
                >
                    <Row gap="sm">
                        <Text tone={picked ? 'accent' : 'muted'}>{picked ? '●' : '○'}</Text>
                        <Text role="ui" weight="semibold" tone={disabled && !picked ? 'muted' : 'primary'}>{name}</Text>
                    </Row>
                </Pressable>
                {children}
            </Stack>
        </Card>
    );
}

function Reasons({ items }: { items: string[] }) {
    return (
        <Stack gap="xs">
            {items.map((line) => (
                <Row key={line} gap="sm" align="start">
                    <Text tone="muted">•</Text>
                    <Stack flex={1}>
                        <Text role="label" tone="secondary">{line}</Text>
                    </Stack>
                </Row>
            ))}
        </Stack>
    );
}

function Fact({ label, children }: { label: string; children: string }) {
    return (
        <Row gap="sm" justify="between">
            <Text role="label" tone="secondary">{label}</Text>
            <Ltr>{children}</Ltr>
        </Row>
    );
}

function stateText(c: SetupComponent, state: ContentState, t: Translate): string {
    if (state === 'active') return t('setup.contents.working');
    if (state === 'done') return t('setup.contents.ready');
    if (state === 'failed') return t('setup.contents.failed');
    return c.installed ? t('setup.contents.alreadyHere') : t('setup.contents.waiting');
}

const sizeText = (gb: number | null, t: Translate) => (gb == null ? t('setup.contents.sizeOnDownload') : formatSize(gb));

/** Everything that comes to this PC: what, how big, and from where. */
function Contents({ plan, run, models }: { plan: SetupPlan; run: SetupRunStatus | null; models: SetupCatalogEntry[] }) {
    const t = useT();
    const locale = useLocaleCode();
    const ended = run != null && run.state !== 'running' && run.state !== 'verifying';
    return (
        <Stack gap="md">
            {(plan.components ?? []).map((c) => {
                const reported = run?.components?.[c.id] as ContentState;
                const state = reported === 'active' && ended ? undefined : reported;
                return (
                    <Stack key={c.id} gap="xs">
                        <Row gap="sm" justify="between" align="start">
                            <Stack flex={1}>
                                <Text role="ui" weight="medium">{t(`setup.component.${c.id}`)}</Text>
                                <Text role="caption" tone="secondary">{t(`setup.component.${c.id}Note`)}</Text>
                                {c.bundled ? (
                                    <Text role="caption" tone="muted">{t('setup.contents.bundled')}</Text>
                                ) : (
                                    <Text role="caption" tone="muted">{`${t('setup.contents.from')} ${ltrIn(locale, c.source ?? '')}`}</Text>
                                )}
                            </Stack>
                            <Stack align="end">
                                <Ltr>{sizeText(c.size_gb, t)}</Ltr>
                                <Text role="caption" tone="secondary">{stateText(c, state ?? (c.installed ? 'done' : undefined), t)}</Text>
                            </Stack>
                        </Row>
                    </Stack>
                );
            })}
            {models.map((e) => (
                <Row key={e.id} gap="sm" justify="between" align="start">
                    <Stack flex={1}>
                        <Text role="ui" weight="medium" dir="ltr">{e.name}</Text>
                        <Text role="caption" tone="secondary">{e.roles.map((role) => t(`setup.purpose.${role}`)).join(' · ')}</Text>
                        {e.source ? <Text role="caption" tone="muted">{`${t('setup.contents.from')} ${e.source}`}</Text> : null}
                    </Stack>
                    <Ltr>{sizeText(e.size_gb, t)}</Ltr>
                </Row>
            ))}
        </Stack>
    );
}

function LocalCard({ view, chatChosen, picked, disabled, onPick }: {
    view: PlanView;
    chatChosen: boolean;
    picked: boolean;
    disabled: boolean;
    onPick: (w: Where) => void;
}) {
    const t = useT();
    const locale = useLocaleCode();
    const { chat, bigger } = view;
    const reasons: string[] = [];
    if (!chatChosen) reasons.push(t('setup.plan.why.evaluated'));
    if (chat?.need_gb != null && chat.capacity_gb != null && chat.fits) {
        reasons.push(t('setup.plan.why.fits', { need: ltrIn(locale, formatFitGb(chat.need_gb)), have: ltrIn(locale, formatFitGb(chat.capacity_gb)) }));
    }
    reasons.push(t('setup.plan.why.private'));
    if (view.onNpu) reasons.push(t('setup.plan.why.noImagesNpu'));
    if (bigger?.need_gb != null && bigger.capacity_gb != null) {
        reasons.push(t('setup.plan.why.bigger', {
            name: bigger.name, need: ltrIn(locale, formatFitGb(bigger.need_gb)), have: ltrIn(locale, formatFitGb(bigger.capacity_gb)),
        }));
    }
    const after = view.totals.afterGb;
    return (
        <RadioCard
            where="local"
            picked={picked}
            disabled={disabled}
            label={t('setup.plan.label')}
            name={t('setup.plan.run', { name: chat?.name ?? '' })}
            onPick={onPick}
        >
            <Text role="caption" tone="accent">{chatChosen ? t('setup.plan.yours') : t('setup.plan.recommended')}</Text>
            <Stack gap="xs">
                <Fact label={t('setup.summary.download')}>{formatSize(view.totals.downloadGb ?? 0)}</Fact>
                <Row gap="sm" justify="between">
                    <Text role="label" tone="secondary">{t('setup.plan.runsOn')}</Text>
                    <Text role="label" dir="ltr">{view.onNpu ? t('setup.checks.npu') : view.gpuName ?? t('setup.plan.cpu')}</Text>
                </Row>
                <Fact label={t('setup.plan.diskAfter')}>{after != null ? formatSize(Math.max(0, after)) : '\u2014'}</Fact>
            </Stack>
            <Reasons items={reasons} />
            {view.pending.length > 0 ? <Text role="caption" tone="muted">{t('setup.plan.cancelAnytime')}</Text> : null}
        </RadioCard>
    );
}

function CloudCard({ blocked, picked, disabled, onPick }: { blocked: boolean; picked: boolean; disabled: boolean; onPick: (w: Where) => void }) {
    const t = useT();
    return (
        <RadioCard where="cloud" picked={picked} disabled={disabled || blocked} label={t('setup.plan.cloudLabel')} name={t('setup.plan.cloud')} onPick={onPick}>
            {blocked ? (
                <Text role="caption" tone="muted">{t('settings.privacy.localOnlyBlocked')}</Text>
            ) : (
                <Reasons items={[t('setup.plan.cloudWhy.stillLocal'), t('setup.plan.cloudWhy.key'), t('setup.plan.cloudWhy.sent')]} />
            )}
        </RadioCard>
    );
}

/** The "Your setup" step: this PC or the cloud, what downloads, and the way on to the run. */
export function PlanStep({ ctx }: { ctx: SetupStepContext }) {
    const t = useT();
    const locale = useLocaleCode();
    const openers = usePlanOpeners();
    const planQuery = useSetupPlan();
    const plan = ctx.plan ?? planQuery.data;
    const preflight = useSetupPreflight().data;
    const cloudBlocked = cloudIsBlocked(useCloudProviders().data?.providers);
    const start = useStartSetup();
    const backend = useApplyTestedBackend();
    const { selection, setSelection, keepTrying } = usePlanSelection();
    const [where, setWhere] = useState<Where>('local');
    const [listOpen, setListOpen] = useState(false);
    const [detailsOpen, setDetailsOpen] = useState(false);

    // The plan seeds the selection once, from what is planned and on disk.
    useEffect(() => {
        if (plan?.catalog && plan.lemonade.reachable && !selection) setSelection(initialSelection(plan));
    }, [plan, selection, setSelection]);

    const goStart = (options?: SetupRunOptions) => start.mutate({ options }, { onSuccess: () => ctx.startedRun() });
    // A local setup puts llama.cpp on the tested backend first; a refusal there stops the run.
    const goLocal = (options: SetupRunOptions) => backend.mutate(undefined, { onSuccess: () => goStart(options) });
    const startError = backend.error ?? start.error;

    if (!plan && planQuery.error) {
        return (
            <Stack gap="md">
                <Notice tone="danger" message={describeError(planQuery.error, t)} />
                <SetupFooter>
                    <Button variant="secondary" label={t('setup.models.useRecommended')} disabled={start.isPending} onPress={() => goStart({ keepTrying })} />
                    <Button variant="primary" label={t('setup.tryAgain')} onPress={() => void planQuery.refetch()} />
                </SetupFooter>
                <Text role="caption" tone="secondary">{t('setup.models.useRecommendedNote')}</Text>
                {startError ? <StartError error={startError} /> : null}
            </Stack>
        );
    }
    if (!plan || !selection) {
        return (
            <Row gap="sm">
                <Spinner size="sm" />
                <Text tone="secondary">{t('setup.check.lede')}</Text>
            </Row>
        );
    }

    const view = planView(plan, selection, preflight);
    const { short } = view;
    const picked: Where = cloudBlocked ? 'local' : where;
    const cloud = picked === 'cloud';
    const busy = start.isPending || backend.isPending;
    const proceed = (acknowledgeLowDisk: boolean) => goLocal(startOptions(plan, selection, acknowledgeLowDisk, keepTrying));
    const onContinue = () => {
        if (cloud) openers.openKey?.();
        else if (short && openers.confirmLowDisk) openers.confirmLowDisk(short, proceed);
        else proceed(false);
    };

    return (
        <Stack gap="md">
            <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.plan.title')}</Text>
            <Text tone="secondary">{t('setup.plan.lede')}</Text>

            <LocalCard view={view} chatChosen={selection.chatChosen} picked={!cloud} disabled={ctx.locked} onPick={setWhere} />
            <CloudCard blocked={cloudBlocked} picked={cloud} disabled={ctx.locked} onPick={setWhere} />

            {view.blocked ? (
                <Stack gap="sm">
                    <Notice tone="danger" message={t('setup.blocked')} />
                    <Disclosure title={t('setup.progress.details')} open={detailsOpen} onToggle={() => setDetailsOpen(!detailsOpen)}>
                        <Text role="caption" mono dir="ltr" selectable>{view.blockers.join('\n')}</Text>
                    </Disclosure>
                </Stack>
            ) : null}
            {short && !cloud ? (
                <Notice
                    tone="danger"
                    message={t('setup.summary.short', {
                        need: ltrIn(locale, formatSize(short.needGb)),
                        free: ltrIn(locale, formatSize(short.freeGb)),
                        drive: t('setup.summary.theDrive'),
                        freeUp: ltrIn(locale, formatSize(short.freeUpGb)),
                    })}
                />
            ) : null}
            {startError ? <StartError error={startError} /> : null}

            {cloud ? null : (
                <Disclosure title={t('setup.plan.whatDownloads')} open={listOpen} onToggle={() => setListOpen(!listOpen)}>
                    <Contents plan={plan} run={ctx.run} models={view.pending} />
                </Disclosure>
            )}

            <SetupFooter>
                {cloud || !openers.openModels ? <Row /> : <Button variant="secondary" label={t('setup.plan.customize')} disabled={busy || ctx.locked} onPress={openers.openModels} />}
                <Button
                    variant="primary"
                    label={cloud ? t('setup.continue') : view.pending.length ? t('setup.plan.start') : t('setup.plan.startNoDownload')}
                    busy={busy}
                    disabled={ctx.locked || busy || (!cloud && view.blocked)}
                    onPress={onContinue}
                />
            </SetupFooter>
        </Stack>
    );
}

/** A refused start: the PC's own 403 says the change is made there, anything else in the shared words. */
function StartError({ error }: { error: Error }) {
    const t = useT();
    if (error instanceof GaiaApiError && error.status === 403) return <Notice tone="info" message={t('setup.plan.pcOnly')} />;
    return <Notice tone="danger" message={describeError(error, t)} />;
}
