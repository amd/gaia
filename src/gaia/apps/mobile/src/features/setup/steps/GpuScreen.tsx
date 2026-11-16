// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Clipboard from 'expo-clipboard';
import { useState } from 'react';

import { formatSize, ltrIn } from '@webui/components/setup/format';
import { copyableCommand, gpuGbAtSetting, osName, splitFigures } from '@webui/components/setup/gpuMemory';
import type { GpuMemoryStatus } from '@webui/types';

import { useActiveModel, useGpuMemory, useGpuMemoryPending } from '@/data';
import { describeError, useLocaleCode, useT } from '@/i18n/useT';
import { Button, ChoiceList, Ltr, Notice, Row, Spinner, Stack, Surface, Text } from '@/ui';
import { Check, Copy } from '@/ui/icons';

import { SetupFooter } from '../SetupFooter';
import type { PlanScreen } from './planScreens';
import { defaultTarget, maxSetting, modelNames, unlockNames } from './gpu';

const KEEP = 'keep';

/** CPU, the GPU's share now, and what the picked size adds. Read only. */
function SplitBar({ status, targetGpuGb }: { status: GpuMemoryStatus; targetGpuGb: number | null }) {
    const t = useT();
    const locale = useLocaleCode();
    const { total, gpu, extra, cpu, target } = splitFigures(status, targetGpuGb);
    const gb = (n: number) => ltrIn(locale, formatSize(n / 10));
    const label = target != null
        ? t('gpu.split.ariaTarget', { cpu: gb(cpu), gpu: gb(target), now: gb(gpu) })
        : t('gpu.split.aria', { cpu: gb(cpu), gpu: gb(gpu) });
    const segments = [
        { key: 'gpu', n: gpu, tone: 'card' as const, accent: true, legend: t('gpu.split.gpu', { gb: gb(gpu) }) },
        { key: 'added', n: extra, tone: 'secondary' as const, accent: true, legend: t('gpu.split.added', { gb: gb(extra) }) },
        { key: 'cpu', n: cpu, tone: 'recessed' as const, accent: false, legend: t('gpu.split.cpu', { gb: gb(cpu) }) },
    ];
    return (
        <Stack gap="xs">
            <Surface accessibilityLabel={label} testID="gpu-split">
                <Row>
                    {total > 0 ? segments.filter((s) => s.n > 0).map((s) => (
                        <Stack key={s.key} flex={s.n}>
                            <Surface tone={s.tone} border={s.accent ? 'accent' : true} padding="sm" />
                        </Stack>
                    )) : null}
                </Row>
            </Surface>
            <Row gap="md" wrap>
                {segments.filter((s) => s.n > 0).map((s) => (
                    <Text key={s.key} role="caption" tone="secondary">{s.legend}</Text>
                ))}
            </Row>
        </Stack>
    );
}

/** The GPU memory screen, opened from the plan step: the split, the sizes and what they unlock, and the manual steps. Changing it is done on the PC. */
export const GpuScreen: PlanScreen = ({ ctx, close }) => {
    const t = useT();
    const locale = useLocaleCode();
    const read = useGpuMemory();
    const status = read.data;
    const [chosen, setChosen] = useState<number | null | undefined>(undefined);
    const [guided, setGuided] = useState(false);
    const [copied, setCopied] = useState<string | null>(null);
    const [copyError, setCopyError] = useState<string | null>(null);

    const chatInCloud = !!useActiveModel().data?.remote;
    const target = chosen !== undefined ? chosen : status ? defaultTarget(status, chatInCloud) : null;
    const steps = useGpuMemory(target, guided && target != null);
    const pendingChange = useGpuMemoryPending();

    const goBack = () => (guided ? setGuided(false) : close());
    const doneByHand = () => {
        if (target != null) pendingChange.mutate(target, { onSuccess: () => setGuided(false) });
    };
    const back = <Button variant="secondary" label={t('setup.back')} onPress={goBack} />;

    if (!status) {
        return (
            <Stack gap="md">
                <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.gpu.title')}</Text>
                {read.error ? (
                    <Notice tone="danger" message={describeError(read.error, t)} action={{ label: t('setup.tryAgain'), onPress: () => void read.refetch() }} />
                ) : (
                    <Row gap="sm">
                        <Spinner size="sm" />
                        <Text tone="secondary">{t('gpu.guided.loading')}</Text>
                    </Row>
                )}
                <SetupFooter>{back}</SetupFooter>
            </Stack>
        );
    }

    const gb = (n: number) => ltrIn(locale, formatSize(n));
    const names = modelNames(ctx.plan?.catalog);
    const presets = status.presets ?? [];
    const preset = presets.find((p) => p.setting_gb === target) ?? null;
    const max = maxSetting(status);
    const recommended = chatInCloud ? null : status.recommended_setting_gb ?? null;
    const cpuLeft = preset && status.total_gb != null ? Math.max(0, status.total_gb - preset.gpu_gb) : null;
    const fitsNow = recommended == null && !chatInCloud ? (status.models ?? []).find((m) => m.default && m.fits_now) : undefined;
    const unlocks = preset ? unlockNames(preset, names) : [];
    const pending = status.pending ?? null;
    const restartPending = pending?.state === 'restart_required';
    const canGuide = status.guided_value !== null;
    const os = osName(status.os);
    const nowGb = gb(status.gpu_gb ?? 0);
    const restartGb = pending ? gpuGbAtSetting(status, pending.setting_gb) : null;
    const keepAction = { label: t('gpu.keep', { gb: nowGb }), onPress: () => pendingChange.mutate(null) };

    const copy = (text: string) => {
        setCopyError(null);
        Clipboard.setStringAsync(text).then(
            () => setCopied(text),
            (err: unknown) => setCopyError(t('gpu.copyFailed', { reason: err instanceof Error ? err.message : String(err) })),
        );
    };

    const options = [
        ...(status.gpu_gb != null ? [{ value: KEEP, label: t('gpu.presets.keep', { gb: gb(status.gpu_gb) }) }] : []),
        ...presets.map((p) => ({
            value: String(p.setting_gb),
            label: t('gpu.presets.gpu', { gb: gb(p.gpu_gb) }),
            description: [
                p.setting_gb === recommended ? t('gpu.presets.recommended') : null,
                p.setting_gb === max ? t('gpu.presets.max') : null,
            ].filter(Boolean).join(' · ') || undefined,
        })),
    ];

    const stepList = steps.data?.guided_steps ?? [];
    const body = guided ? (
        <Stack gap="md">
            {preset ? <Text tone="secondary">{t('gpu.guided.lede', { gb: gb(preset.gpu_gb) })}</Text> : null}
            {steps.error ? <Notice tone="danger" message={describeError(steps.error, t)} /> : null}
            {steps.isPending ? (
                <Row gap="sm">
                    <Spinner size="sm" />
                    <Text tone="secondary">{t('gpu.guided.loading')}</Text>
                </Row>
            ) : steps.data?.guided_value === null ? (
                <Text tone="secondary">{t('gpu.noPresets')}</Text>
            ) : (
                <Stack gap="md">
                    {stepList.map((step, i) => {
                        const cmd = copyableCommand(step);
                        return (
                            <Stack key={step} gap="xs">
                                <Row gap="sm" align="start">
                                    <Text tone="muted">{`${i + 1}.`}</Text>
                                    <Stack flex={1}>
                                        <Text dir="auto" selectable>{step}</Text>
                                    </Stack>
                                </Row>
                                {cmd ? (
                                    <Row justify="end">
                                        <Button
                                            variant="secondary"
                                            size="sm"
                                            icon={copied === cmd ? Check : Copy}
                                            label={copied === cmd ? t('gpu.copied') : t('gpu.copy')}
                                            accessibilityLabel={t('gpu.copyAria', { what: cmd })}
                                            onPress={() => copy(cmd)}
                                        />
                                    </Row>
                                ) : null}
                            </Stack>
                        );
                    })}
                </Stack>
            )}
            {copyError ? <Notice tone="danger" message={copyError} /> : null}
            {pendingChange.error ? <Notice tone="danger" message={describeError(pendingChange.error, t)} /> : null}
            <Notice tone="info" message={t('setup.plan.pcOnly')} />
        </Stack>
    ) : (
        <Stack gap="md">
            <Text tone="secondary">{t('gpu.body', { total: gb(status.total_gb ?? 0), gpu: nowGb })}</Text>
            <SplitBar status={status} targetGpuGb={!restartPending ? preset?.gpu_gb ?? null : null} />

            {pending?.state === 'took_effect' ? <Notice tone="success" message={t('gpu.pending.tookEffect', { gb: nowGb })} /> : null}
            {restartPending ? (
                <Notice
                    tone="info"
                    message={[
                        restartGb == null ? t('gpu.pending.restartAny') : t('gpu.pending.restart', { gb: ltrIn(locale, formatSize(restartGb)) }),
                        t('gpu.pending.restartHow'),
                        t('gpu.pending.downloadsFirst'),
                    ].join(' ')}
                    action={pending.source === 'guided' ? keepAction : undefined}
                />
            ) : null}
            {pending?.state === 'not_applied' ? <Notice tone="warning" message={t('gpu.pending.notApplied', { gb: nowGb })} action={keepAction} /> : null}
            {pendingChange.error && !guided ? <Notice tone="danger" message={describeError(pendingChange.error, t)} /> : null}

            {!restartPending && presets.length > 0 ? (
                <Stack gap="md">
                    {fitsNow ? <Text tone="success">{t('gpu.keepRecommended', { model: names[fitsNow.id] ?? fitsNow.id })}</Text> : null}
                    <ChoiceList
                        accessibilityLabel={t('gpu.presets.label')}
                        options={options}
                        value={target == null ? KEEP : String(target)}
                        onChange={(v) => {
                            setCopied(null);
                            setChosen(v === KEEP ? null : Number(v));
                        }}
                    />
                    {preset ? (
                        <Text role="label" tone="secondary">
                            {unlocks.length > 0
                                ? t(unlocks.length > 1 ? 'gpu.unlock.two' : 'gpu.unlock.one', { gb: gb(preset.gpu_gb), a: unlocks[0] ?? '', b: unlocks[1] ?? '' })
                                : t('gpu.unlock.none', { gb: gb(preset.gpu_gb) })}
                        </Text>
                    ) : null}
                    {preset && preset.setting_gb === max && cpuLeft != null ? (
                        <Notice tone="warning" role="note" message={t('gpu.maxWarn', { cpu: gb(cpuLeft), os })} />
                    ) : null}
                    <Text role="caption" tone="muted">{`${t('gpu.reserve', { gb: gb(status.reserve_gb ?? 16), os })} ${t('gpu.needs')}`}</Text>
                    {status.method !== 'apply' && status.method_reason ? <Ltr>{status.method_reason}</Ltr> : null}
                </Stack>
            ) : null}
            {!restartPending && presets.length === 0 ? <Text tone="secondary">{t('gpu.noPresets')}</Text> : null}

            {!restartPending && presets.length > 0 && canGuide ? (
                <Button variant="secondary" label={t('gpu.showMe')} disabled={target == null} onPress={() => setGuided(true)} />
            ) : null}
            {status.method === 'apply' || status.revert?.available || pending ? <Notice tone="info" message={t('setup.plan.pcOnly')} /> : null}
        </Stack>
    );

    return (
        <Stack gap="md">
            <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.gpu.title')}</Text>
            <Text tone="secondary">{t('setup.gpu.lede')}</Text>
            {body}
            <SetupFooter>
                {back}
                {guided ? (
                    <Button
                        variant="primary"
                        label={t('gpu.guided.done')}
                        busy={pendingChange.isPending}
                        disabled={target == null || steps.data?.guided_value == null}
                        onPress={doneByHand}
                    />
                ) : restartPending ? (
                    <Button variant="primary" label={t('setup.continue')} onPress={close} />
                ) : target == null && presets.length > 0 ? (
                    <Button variant="primary" label={t('gpu.keepAsIs')} onPress={close} />
                ) : (
                    <Button variant="primary" label={t('gpu.skip')} onPress={close} />
                )}
            </SetupFooter>
        </Stack>
    );
};
