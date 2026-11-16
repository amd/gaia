// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ActiveModel, ProviderModel } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Badge, Ltr, Notice, Row, Select, Spinner, Stack, Surface, Text } from '@/ui';

import { SettingsAnchor } from '../../targets';
import { ContextSize } from './ContextSize';
import { ctxLabel, isolate, modelLabel, sameModelId } from './modelText';

export interface ModelInUseProps {
    active: ActiveModel | undefined;
    /** Why the PC couldn't be asked, once it has failed. */
    readFailed: boolean;
    /** The in-use model's row, once a list held it; carries its device and size. */
    row: ProviderModel | null;
    waitingServer: boolean;
    /** Models the Switch model select offers: ready to use, on the provider being viewed. */
    choices: ProviderModel[];
    busy: boolean;
    onSwitch: (model: string) => void;
}

/** The answer to "what am I using?": the model, what it runs on, and its context size. */
export function ModelInUse({ active, readFailed, row, waitingServer, choices, busy, onSwitch }: ModelInUseProps) {
    const t = useT();
    const model = active?.model ?? null;
    const remote = !!active?.remote;
    const offered = choices.some((m) => sameModelId(model, m.id));
    const downloaded = active?.downloaded ?? row?.downloaded;
    const stateKey = remote ? null
        : row?.state === 'loaded' ? 'settings.model.loaded'
        : downloaded === true ? 'settings.model.downloaded'
        : downloaded === false ? 'settings.model.notDownloaded'
        : null;
    const device = row?.placement?.device;
    const labels = choices.map((m) => modelLabel(m));
    const options = choices.map((m, i) => ({
        value: m.id,
        label: labels[i]!,
        description: labels.filter((l) => l === labels[i]).length > 1 ? m.id : undefined,
    }));
    const current = choices.find((m) => sameModelId(model, m.id))?.id;

    return (
        <SettingsAnchor name="model-in-use">
            <Surface tone="card" radius="lg" padding="lg" border="accent">
                <Stack gap="md">
                    <Text role="caption" tone="muted" weight="semibold" accessibilityRole="header">
                        {t('settings.model.inUseTitle')}
                    </Text>
                    {model ? (
                        <Text role="title" weight="semibold">
                            <Ltr>{row?.display_name || active?.display_name || modelLabel({ id: model })}</Ltr>
                        </Text>
                    ) : active || readFailed ? (
                        <Text role="label" tone="muted">{t('settings.model.noneInUse')}</Text>
                    ) : (
                        <Row gap="sm">
                            <Spinner />
                            <Text role="label" tone="muted">
                                {waitingServer ? t('settings.model.waitingServer') : t('settings.loading')}
                            </Text>
                        </Row>
                    )}
                    {active?.config_error ? <Notice tone="danger" message={active.config_error} /> : null}
                    {model && !remote && row?.available === false ? (
                        <Notice tone="danger" message={t('mobileApp.settings.model.inUseCantRun')} />
                    ) : null}
                    {model ? (
                        <Row gap="sm" wrap>
                            {remote ? <Badge label={t.bare('settings.model.cloud')} accessibilityLabel={t('settings.model.cloud')} /> : null}
                            {remote && active?.provider_name ? <Badge label={active.provider_name} /> : null}
                            {device ? <Badge label={device.toUpperCase()} /> : null}
                            {stateKey ? <Badge label={t.bare(stateKey)} accessibilityLabel={t(stateKey)} tone={row?.state === 'loaded' ? 'success' : 'neutral'} /> : null}
                            {row?.fits === true ? <Badge label={t.bare('mobileApp.settings.model.fits')} accessibilityLabel={t('mobileApp.settings.model.fits')} tone="success" /> : null}
                            {row?.size_gb ? <Badge label={isolate(`${row.size_gb} GB`)} /> : null}
                            {remote && row?.context_length ? (
                                <Badge
                                    label={t.bare('settings.model.contextChip', { size: isolate(ctxLabel(row.context_length)) })}
                                    accessibilityLabel={t('settings.model.contextChip', { size: isolate(ctxLabel(row.context_length)) })}
                                />
                            ) : null}
                        </Row>
                    ) : null}
                    {choices.length > 1 || (choices.length === 1 && !offered) ? (
                        <Select
                            label={t('settings.model.switch')}
                            value={current}
                            options={options}
                            placeholder={t.bare('settings.model.switchPlaceholder')}
                            onChange={onSwitch}
                            disabled={busy}
                            testID="model-switch"
                        />
                    ) : null}
                    {model && !remote && active?.downloaded !== false ? <ContextSize model={model} /> : null}
                </Stack>
            </Surface>
        </SettingsAnchor>
    );
}
