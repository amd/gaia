// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { PC_ONLY_REFUSAL } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { useLocalModelStatus, useQueryFailure, useRestartLocalModel } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ChipButton } from '@/features/schedules/ChipButton';
import { Button, Notice, Row, Sheet, Spinner, Stack, StatusDot, Text, type StatusTone } from '@/ui';
import { RotateCw, ScrollText, Settings } from '@/ui/icons';

import { memoryText, restartControl, STATE_TEXT, type ModelDot } from './modelStatusText';

const DOT_TONE: Record<ModelDot, StatusTone> = {
    loaded: 'success',
    warming: 'running',
    idle: 'idle',
    stopped: 'error',
};

export interface ModelStatusSheetProps {
    open: boolean;
    onClose: () => void;
    onOpenSettings?: () => void;
}

function Fact({ label, value }: { label: string; value: string }) {
    return (
        <Row gap="md" align="start" justify="between">
            <Text role="label" tone="secondary">
                {label}
            </Text>
            <Stack flex={1}>
                <Text role="label" align="end">
                    {value}
                </Text>
            </Stack>
        </Row>
    );
}

/** The model's state and facts, with Model settings, the server log (PC only), and Restart. */
export function ModelStatusSheet({ open, onClose, onOpenSettings }: ModelStatusSheetProps) {
    const t = useT();
    const query = useLocalModelStatus(open);
    const failure = useQueryFailure(query);
    const restart = useRestartLocalModel();
    const [confirming, setConfirming] = useState(false);
    const [logRefused, setLogRefused] = useState(false);
    const s = query.data;
    const control = s ? restartControl(s, restart.isPending) : null;
    const busy = restart.isPending || !!s?.restarting;
    const restartError = restart.error
        ? restart.error instanceof GaiaApiError
            ? restart.error.detail
            : describeError(restart.error, t)
        : null;

    const close = () => {
        setConfirming(false);
        setLogRefused(false);
        onClose();
    };
    const reload = (confirm: boolean) => {
        setConfirming(false);
        restart.mutate({ confirm });
    };

    return (
        <Sheet open={open} onClose={close} title={t('modelStatus.label')}>
            <Stack gap="md">
                {!s && !failure.failed ? (
                    <Row gap="sm">
                        <Spinner />
                        <Text role="label">{t('modelStatus.checking')}</Text>
                    </Row>
                ) : null}
                {failure.failed && !s ? <Notice tone="danger" message={describeError(failure.error, t)} /> : null}
                {s ? (
                    <>
                        <Row gap="sm">
                            <StatusDot tone={DOT_TONE[s.state]} label={t(STATE_TEXT[s.state])} />
                            <Text role="ui" weight="semibold">
                                {s.state === 'loaded' && s.busy_reason === 'request'
                                    ? t('modelStatus.state.loadedAnswering')
                                    : t(STATE_TEXT[s.state])}
                            </Text>
                        </Row>
                        {s.active_remote ? (
                            <Text role="label" tone="secondary">
                                {t('modelStatus.cloudLine', {
                                    name: s.active_display_name ?? s.active_model ?? '—',
                                })}
                            </Text>
                        ) : null}
                        <Stack gap="sm">
                            <Fact
                                label={t('modelStatus.model')}
                                value={s.display_name ?? (s.active_remote ? '—' : s.active_display_name) ?? '—'}
                            />
                            {s.placement?.label ? (
                                <Fact
                                    label={t('modelStatus.runsOn')}
                                    value={
                                        s.placement.source === 'expected'
                                            ? t('modelStatus.expected', { label: s.placement.label })
                                            : s.placement.label
                                    }
                                />
                            ) : null}
                            {s.state !== 'stopped' ? (
                                <Fact
                                    label={t('modelStatus.memory')}
                                    value={memoryText(s, t) ?? t('modelStatus.unknown')}
                                />
                            ) : null}
                            {s.ctx_size != null ? (
                                <Fact
                                    label={t('modelStatus.context')}
                                    value={t('settings.ctx.tokens', {
                                        size: `${Math.round(s.ctx_size / 1024)}K`,
                                    })}
                                />
                            ) : null}
                        </Stack>
                        {s.placement?.warning ? <Notice tone="warning" message={s.placement.warning} /> : null}
                        {s.error ? <Notice tone="danger" message={s.error} /> : null}
                        {s.restart_error ? <Notice tone="danger" message={s.restart_error} /> : null}
                    </>
                ) : null}
                {restartError ? <Notice tone="danger" message={restartError} /> : null}
                {confirming && s ? (
                    <Stack gap="sm">
                        <Notice
                            tone="warning"
                            message={t('modelStatus.confirm', {
                                name: s.display_name ?? t('modelStatus.theModel'),
                                url: s.base_url ?? '—',
                            })}
                        />
                        <Row gap="sm">
                            <Button label={t('modelStatus.cancel')} onPress={() => setConfirming(false)} />
                            <Button label={t('modelStatus.reloadIt')} variant="primary" onPress={() => reload(true)} />
                        </Row>
                    </Stack>
                ) : null}
                {logRefused ? <Notice tone="info" message={PC_ONLY_REFUSAL} /> : null}
                <Row gap="xs" wrap justify="end">
                    {onOpenSettings ? (
                        <ChipButton
                            variant="plain"
                            label={t('modelStatus.settings')}
                            icon={Settings}
                            onPress={() => {
                                close();
                                onOpenSettings();
                            }}
                        />
                    ) : null}
                    {!s?.active_remote ? (
                        <ChipButton variant="plain" label={t('logs.serverLog')} icon={ScrollText} onPress={() => setLogRefused(true)} />
                    ) : null}
                    {control?.shown ? (
                        <ChipButton
                            variant="plain"
                            label={busy ? t('modelStatus.restarting') : t('modelStatus.restart')}
                            icon={RotateCw}
                            disabled={busy || control.disabled}
                            onPress={() => (control.confirms ? setConfirming(true) : reload(false))}
                        />
                    ) : null}
                </Row>
                {control?.disabled && !busy ? (
                    <Text role="label" tone="secondary">
                        {t(control.hint)}
                    </Text>
                ) : null}
            </Stack>
        </Sheet>
    );
}
