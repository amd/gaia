// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo } from 'react';

import { formatSize, ltrIn } from '@webui/components/setup/format';
import {
    catalogOf, chooseChat, initialSelection, recommendedSelection, shortfall, toggle,
} from '@webui/components/setup/selection';
import type { SetupModelProgress } from '@webui/types';

import { GaiaApiError } from '@/api/errors';
import { useGpuMemory, useSaveModelChoices, useSetupPlan } from '@/data';
import { describeError, useLocaleCode, useT } from '@/i18n/useT';
import { Button, Notice, Pressable, Row, Spinner, Stack, Text } from '@/ui';

import { NpuNotReady } from '../npu/NpuNotReady';
import { SetupFooter } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { useConfirmSheet } from '../sheets/ConfirmSheet';
import { serverConfirmRequest, setupConfirmFrom } from '../sheets/setupConfirm';
import { ModelChecklist, SelectionSummary } from './Checklist';
import { MarkedRow } from './ModelRows';
import { JobModels } from './JobSections';
import { backendView, chooseBackend } from './modelsView';
import { usePlanOpeners } from './planOpeners';
import { usePlanSelection } from './planSelection';

/** Where the chat model runs: the GPU, or a Ryzen AI NPU when this PC has one that is ready. */
function BackendChoice({ view, onPick, disabled }: {
    view: ReturnType<typeof backendView>;
    onPick: (which: 'gpu' | 'npu') => void;
    disabled: boolean;
}) {
    const t = useT();
    const { npuModel, npuProblems, npuUnusable, onNpu } = view;
    const choices: { id: 'gpu' | 'npu'; label: string; unavailable?: boolean }[] = [
        { id: 'gpu', label: t('setup.backend.gpu') },
        ...(npuModel ? [{ id: 'npu' as const, label: t('setup.backend.npu') }] : []),
        ...(npuProblems.length ? [{ id: 'npu' as const, label: t('setup.backend.npu'), unavailable: true }] : []),
    ];
    const selected = onNpu ? 'npu' : 'gpu';
    return (
        <Stack gap="sm">
            <Text role="ui" weight="semibold" accessibilityRole="header">{t('setup.backend.title')}</Text>
            <Row gap="sm" wrap>
                {choices.map((c) => {
                    const off = disabled || !!c.unavailable;
                    const picked = selected === c.id;
                    return (
                        <Pressable
                            key={c.id + (c.unavailable ? '-off' : '')}
                            onPress={() => onPick(c.id)}
                            disabled={off}
                            accessibilityRole="radio"
                            accessibilityLabel={c.label}
                            accessibilityState={{ checked: picked, disabled: off }}
                        >
                            <Row gap="sm">
                                <Text tone={picked ? 'accent' : 'muted'}>{picked ? '●' : '○'}</Text>
                                <Text role="ui" weight={picked ? 'semibold' : 'regular'} tone={c.unavailable ? 'muted' : 'primary'}>{c.label}</Text>
                            </Row>
                        </Pressable>
                    );
                })}
            </Row>
            {npuProblems.length > 0 ? (
                <NpuNotReady problems={npuProblems} disabled={disabled} />
            ) : (
                <Text role="caption" tone="secondary">
                    {t(npuModel ? 'setup.backend.hint' : npuUnusable ? 'setup.backend.hintNpuUnusable' : 'setup.backend.hintNoNpu')}
                </Text>
            )}
        </Stack>
    );
}

/** "Choose your models", opened from the plan's Customize: every choice lands in the plan step's selection. */
export function ModelsScreen({ ctx, close }: { ctx: SetupStepContext; close: () => void }) {
    const t = useT();
    const locale = useLocaleCode();
    const planQuery = useSetupPlan();
    const plan = ctx.plan ?? planQuery.data;
    const { selection, setSelection, keepTrying, setKeepTrying } = usePlanSelection();
    const gpu = useGpuMemory().data;
    const { openGpu } = usePlanOpeners();
    const save = useSaveModelChoices();
    const { ask, sheet } = useConfirmSheet();
    const progress = useMemo(() => {
        const rows: Record<string, SetupModelProgress> = {};
        for (const m of ctx.run?.models ?? []) rows[m.id] = m;
        return rows;
    }, [ctx.run]);

    if (!plan?.catalog || !selection) {
        return planQuery.error ? (
            <Stack gap="md">
                <Notice tone="danger" message={t('setup.models.listFailed', { reason: describeError(planQuery.error, t) })} />
                <Row gap="sm">
                    <Button variant="primary" label={t('setup.tryAgain')} onPress={() => void planQuery.refetch()} />
                    <Button variant="secondary" label={t('setup.back')} onPress={close} />
                </Row>
            </Stack>
        ) : (
            <Row gap="sm">
                <Spinner size="sm" />
                <Text tone="secondary">{t('setup.models.loading')}</Text>
            </Row>
        );
    }

    const catalog = catalogOf(plan);
    const busy = save.isPending;
    const locked = ctx.locked || busy;
    const view = backendView(plan, selection);
    const noChat = !selection.defaultChat;
    const short = shortfall(plan, selection);
    const showGpu = gpu?.applies && gpu.total_gb != null && gpu.gpu_gb != null;

    const commit = async (acknowledgeNoFit: boolean): Promise<void> => {
        try {
            await save.mutateAsync({ plan, selection, acknowledgeNoFit });
            close();
        } catch (err) {
            const detail = setupConfirmFrom(err);
            if (detail?.code === 'no_fit' && !acknowledgeNoFit && (await ask(serverConfirmRequest(detail, t)))) await commit(true);
        }
    };
    // What the PC holds is the plan: backing out drops this visit's changes.
    const back = () => {
        setSelection(initialSelection(plan));
        close();
    };
    const saveError = save.error && !setupConfirmFrom(save.error) ? save.error : null;

    return (
        <Stack gap="lg">
            <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.models.title')}</Text>
            <Text tone="secondary">{t('setup.job.lede')}</Text>

            <BackendChoice view={view} disabled={locked} onPick={(which) => setSelection(chooseBackend(plan, selection, which))} />
            {showGpu ? (
                <Stack gap="xs">
                    <Text role="caption" tone="secondary">
                        {t('setup.models.gpu', { gpu: ltrIn(locale, formatSize(gpu.gpu_gb!)), total: ltrIn(locale, formatSize(gpu.total_gb!)) })}
                    </Text>
                    {openGpu ? <Button variant="ghost" size="sm" label={t('setup.found.changeMemory')} disabled={locked} onPress={openGpu} /> : null}
                </Stack>
            ) : null}

            <JobModels plan={plan} selection={selection} onSelection={setSelection} disabled={locked} />

            <Stack gap="md">
                <Text role="ui" weight="semibold" accessibilityRole="header">{t('setup.models.change')}</Text>
                <ModelChecklist
                    plan={plan}
                    selection={selection}
                    disabled={locked}
                    progress={progress}
                    onToggle={(id, on) => setSelection(toggle(selection, catalog, id, on))}
                    onDefaultChat={(id) => setSelection(chooseChat(selection, catalog, id))}
                />
                <Button variant="ghost" label={t('setup.models.resetRecommended')} disabled={locked} onPress={() => setSelection(recommendedSelection(plan))} />
            </Stack>

            <SelectionSummary plan={plan} selection={selection} />
            <MarkedRow
                kind="checkbox"
                checked={keepTrying}
                disabled={locked}
                label={t('setup.keepTrying')}
                onPress={() => setKeepTrying(!keepTrying)}
                name={<Text role="ui">{t('setup.keepTrying')}</Text>}
            />
            {noChat ? <Notice tone="danger" message={t('setup.models.noChatReason')} /> : null}
            {saveError ? <SaveError error={saveError} /> : null}

            <SetupFooter>
                <Button variant="secondary" label={t('setup.back')} disabled={busy} onPress={back} />
                <Button
                    variant="primary"
                    label={short ? t('setup.continueAnyway') : t('setup.continue')}
                    busy={busy}
                    disabled={locked || noChat}
                    onPress={() => void commit(false)}
                />
            </SetupFooter>
            {sheet}
        </Stack>
    );
}

/** A refused save: the PC's own 403 says the change is made there, anything else in the shared words. */
function SaveError({ error }: { error: Error }) {
    const t = useT();
    if (error instanceof GaiaApiError && error.status === 403) return <Notice tone="info" message={t('setup.plan.pcOnly')} />;
    return <Notice tone="danger" message={describeError(error, t)} />;
}
