// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isDownloading } from '@webui/components/modelMeta';
import { measuredText, modelFacts, RANK_NOTES } from '@webui/utils/modelList.core';

import type { ProviderModel } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Badge, Button, Disclosure, Icon, Ltr, ProgressLine, Row, Spinner, Stack, Surface, Text } from '@/ui';
import { Check } from '@/ui/icons';

import { NpuNotReady } from '../../../setup/npu/NpuNotReady';
import { isolate, priceText } from './modelText';

export interface ModelRowProps {
    model: ProviderModel;
    label: string;
    current: boolean;
    remote: boolean;
    /** What the list is doing: a model id (switching), `dl:<id>` or `rm:<id>`. */
    busy: string | null;
    onUse: () => void;
    onDownload: () => void;
    onRemove: () => void;
    /** Read the model list again, after the NPU's setup was checked. */
    onNpuRecheck: () => void;
}

/** One model: its name and chips, the long facts behind Details, and its one action. */
export function ModelRow({ model: m, label, current, remote, busy, onUse, onDownload, onRemove, onNpuRecheck }: ModelRowProps) {
    const t = useT();
    const [detailsOpen, setDetailsOpen] = useState(false);
    const progress = isDownloading(m) ? m.download_progress : null;
    const facts = modelFacts(t, m);
    const hasDetails = !!(m.display_name || facts || m.evidence || (m.custom && m.source_url));
    const anyBusy = busy !== null;
    const blocked = m.available === false;

    return (
        <Surface tone={current ? 'accent' : 'primary'} radius="md" padding="sm">
            <Stack gap="sm" testID={`model-row-${m.id}`}>
                <Row gap="sm" justify="between" align="start">
                    <Stack flex={1}>
                        <Text role="ui" weight="semibold"><Ltr>{label}</Ltr></Text>
                    </Stack>
                    {current && !blocked ? (
                        <Row gap="xs">
                            <Icon icon={Check} size="sm" tone="success" />
                            <Text role="label" tone="success" weight="semibold">{t.bare('settings.model.inUse')}</Text>
                        </Row>
                    ) : null}
                </Row>
                <Row gap="xs" wrap>
                    {m.placement?.device ? <Badge label={m.placement.device.toUpperCase()} /> : null}
                    {m.rank ? (
                        <Badge
                            label={`#${m.rank}${m.note ? ` ${RANK_NOTES[m.note] ? t.bare(RANK_NOTES[m.note]!) : m.note}` : ''}`}
                            accessibilityLabel={`#${m.rank}${m.note ? ` ${RANK_NOTES[m.note] ? t(RANK_NOTES[m.note]!) : m.note}` : ''}`}
                            tone="accent"
                        />
                    ) : null}
                    {m.state === 'loaded' ? (
                        <Badge label={t.bare('settings.model.loaded')} accessibilityLabel={t('settings.model.loaded')} tone="success" />
                    ) : null}
                    {m.fits === true && !blocked ? (
                        <Badge label={t.bare('mobileApp.settings.model.fits')} accessibilityLabel={t('mobileApp.settings.model.fits')} tone="success" />
                    ) : null}
                    {m.custom ? <Badge label={t.bare('settings.model.custom')} accessibilityLabel={t('settings.model.custom')} /> : null}
                    {m.size_gb ? <Badge label={isolate(`${m.size_gb} GB`)} /> : null}
                </Row>
                {remote ? (
                    <Text role="caption" tone="muted">
                        {priceText(m.price, t)}
                    </Text>
                ) : null}
                {hasDetails ? (
                    <Disclosure
                        title={t('settings.model.details')}
                        open={detailsOpen}
                        onToggle={() => setDetailsOpen((o) => !o)}
                        testID={`model-details-${m.id}`}
                    >
                        <Stack gap="xs">
                            {m.display_name ? <Text role="caption" tone="muted"><Ltr>{m.id}</Ltr></Text> : null}
                            {facts ? <Text role="caption" tone="muted">{facts}</Text> : null}
                            {m.evidence ? <Text role="caption" tone="muted">{measuredText(t, m.evidence)}</Text> : null}
                            {m.custom && m.source_url ? (
                                <Text role="caption" tone="muted">{t('settings.model.from', { url: isolate(m.source_url) })}</Text>
                            ) : null}
                        </Stack>
                    </Disclosure>
                ) : null}
                {m.placement?.warning ? <Text role="caption" tone="warning">{m.placement.warning}</Text> : null}
                {m.npu_problems?.length ? (
                    <NpuNotReady problems={m.npu_problems} disabled={anyBusy} onReady={onNpuRecheck} />
                ) : m.unavailable_reason ? (
                    <Text role="caption" tone="muted">{t('settings.model.cantRun', { reason: m.unavailable_reason })}</Text>
                ) : null}
                {progress ? (
                    <Stack gap="xs">
                        <ProgressLine value={progress.percent / 100} label={t('settings.model.downloadingAria', { name: label })} />
                        <Row gap="xs">
                            <Spinner size="sm" />
                            <Text role="caption" tone="secondary">
                                {progress.message ? progress.message : <Ltr>{`${Math.round(progress.percent)}%`}</Ltr>}
                            </Text>
                        </Row>
                    </Stack>
                ) : null}
                <Row gap="sm" wrap>
                    {current && blocked ? (
                        <Text role="label" tone="danger">{t('settings.model.inUseBlocked')}</Text>
                    ) : current ? null : blocked || progress ? null : !m.downloaded ? (
                        <Button
                            label={t.bare('settings.model.download')}
                            accessibilityLabel={t('settings.model.downloadAria', { name: label })}
                            onPress={onDownload}
                            busy={busy === `dl:${m.id}`}
                            disabled={anyBusy}
                            size="sm"
                            testID={`model-download-${m.id}`}
                        />
                    ) : (
                        <Button
                            label={t.bare('settings.model.use')}
                            accessibilityLabel={t('settings.model.useAria', { name: label })}
                            onPress={onUse}
                            busy={busy === m.id}
                            disabled={anyBusy}
                            size="sm"
                            testID={`model-use-${m.id}`}
                        />
                    )}
                    {m.custom && !current && !progress ? (
                        <Button
                            variant="ghost"
                            label={t.bare('settings.models.remove')}
                            accessibilityLabel={t('settings.models.removeAria', { name: label })}
                            onPress={onRemove}
                            disabled={anyBusy}
                            size="sm"
                            testID={`model-remove-${m.id}`}
                        />
                    ) : null}
                </Row>
            </Stack>
        </Surface>
    );
}
