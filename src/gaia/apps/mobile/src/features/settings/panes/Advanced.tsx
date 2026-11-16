// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';

import { isolate } from '@webui/i18n/registry';
import type { SystemStatus } from '@webui/types';

import { useModelActions, useQueryFailure, useSystemStatus } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Button, Card, Disclosure, Divider, Ltr, Notice, ProgressLine, Row, Spinner, Stack, Surface, Text } from '@/ui';

import { LogsCard } from '@/features/logs';

import { isPcRefusal, PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';
import { ActionError, LoadAnywaySheet, RawDetails } from './advanced/errors';
import { ServerControlsSlot } from './advanced/ServerControls';
import { capabilitiesText, needsLocalSetup, needText, statusFlags } from './advanced/statusText';

/** Settings > Advanced: the PC's status and model actions, then the Logs card; the server controls go in the slot. */
export default function Advanced() {
    return (
        <SettingsAnchor name="pane">
            <Stack gap="lg" paddingX="lg" paddingY="md">
                <StatusCard />
                <SettingsAnchor name="logs">
                    <LogsCard />
                </SettingsAnchor>
                <ServerControlsSlot />
            </Stack>
        </SettingsAnchor>
    );
}

function StatusRow({ label, value, ok, hint, oneLine }: { label: string; value: ReactNode; ok: boolean; hint?: string; oneLine?: boolean }) {
    return (
        <Surface tone="secondary" border radius="md" padding="md">
            <Row gap="md" align={hint ? 'start' : 'center'}>
                <Text role="label">{label}</Text>
                <Stack flex={1} align="end" gap="xs">
                    <Text role="label" weight="semibold" mono tone={ok ? 'success' : 'warning'} align="end" numberOfLines={oneLine ? 1 : undefined}>{value}</Text>
                    {hint ? <Text role="caption" tone="muted" align="end">{hint}</Text> : null}
                </Stack>
            </Row>
        </Surface>
    );
}

function StatusCard() {
    const t = useT();
    const query = useSystemStatus();
    const failure = useQueryFailure(query);
    const status = query.data;
    const modelName = status ? status.default_model_name : null;
    const actions = useModelActions(modelName, status);
    const [detailsOpen, setDetailsOpen] = useState(false);

    return (
        <Card padding="lg" testID="advanced-status" accessibilityLabel={t('settings.advanced.status')}>
            <Stack gap="md">
                {query.isPending && !failure.failed ? (
                    <Row gap="sm">
                        <Spinner />
                        <Text role="label" tone="muted">{t('settings.advanced.checking')}</Text>
                    </Row>
                ) : !status && isPcRefusal(query.error) ? (
                    <PcOnlyNotice testID="advanced-status-pc-only" />
                ) : !status ? (
                    <Stack gap="sm">
                        <Notice
                            tone="warning"
                            message={t('settings.advanced.unreachable')}
                            action={{ label: t('settings.key.checkAgain'), onPress: () => void query.refetch() }}
                            testID="advanced-unreachable"
                        />
                        <RawDetails text={describeError(failure.error, t)} />
                    </Stack>
                ) : (
                    <StatusBody
                        status={status}
                        modelName={modelName}
                        actions={actions}
                        detailsOpen={detailsOpen}
                        onToggleDetails={() => setDetailsOpen((v) => !v)}
                    />
                )}
            </Stack>
        </Card>
    );
}

function StatusBody({ status, modelName, actions, detailsOpen, onToggleDetails }: {
    status: SystemStatus;
    modelName: string | null;
    actions: ReturnType<typeof useModelActions>;
    detailsOpen: boolean;
    onToggleDetails: () => void;
}) {
    const t = useT();
    const { cloud, wrongModel, smallContext, notDownloaded } = statusFlags(status);
    const needsLoad = (wrongModel || smallContext) && modelName !== null;
    const setupNeeded = needsLocalSetup(status);
    const downloading = actions.pending === 'download';
    const progress = status.download_progress;
    const downloadActive = progress?.state === 'starting' || progress?.state === 'downloading';
    const name = modelName ?? '';

    const serverHint = status.lemonade_running
        ? undefined
        : setupNeeded
          ? t('mobileApp.settings.advanced.notInstalledHint')
          : status.start_instruction && !/gaia init/i.test(status.start_instruction)
            ? status.start_instruction
            : t('mobileApp.settings.advanced.notRunningHint');
    const serverValue = status.lemonade_running
        ? status.lemonade_version
            ? t.bare('settings.advanced.runningAt', { version: isolate(status.lemonade_version, 'ltr') })
            : t.bare('settings.advanced.running')
        : setupNeeded
          ? t.bare('settings.advanced.notInstalled')
          : status.lemonade_error
            ? t.bare('settings.advanced.unavailable')
            : t.bare('settings.advanced.notRunning');

    const disk = status.disk_space_gb;
    const memory = status.memory_available_gb;

    return (
        <Stack gap="md">
            <Stack gap="sm">
                <StatusRow label={t('settings.advanced.modelServer')} value={serverValue} ok={status.lemonade_running} hint={serverHint} />
                {!status.lemonade_running && status.lemonade_error ? (
                    <RawDetails text={status.lemonade_error} />
                ) : null}
                {setupNeeded ? <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="advanced-setup-pc-only" /> : null}
                <StatusRow
                    label={t('settings.section.model')}
                    oneLine
                    value={status.model_loaded ? <Ltr>{status.model_loaded}</Ltr> : t.bare('settings.advanced.noneLoaded')}
                    ok={!!status.model_loaded && (cloud || status.expected_model_loaded !== false)}
                    hint={
                        !status.model_loaded
                            ? t('settings.advanced.loadsFirst')
                            : !cloud && status.expected_model_loaded === false
                              ? t('settings.advanced.expected', { name: isolate(name, 'ltr') })
                              : undefined
                    }
                />
                <StatusRow
                    label={t('settings.advanced.disk')}
                    value={<Ltr>{`${disk} GB`}</Ltr>}
                    ok={disk > 5}
                    hint={
                        !status.model_loaded && disk < 30
                            ? t('settings.advanced.diskLow', { need: needText(status), size: isolate(`${disk} GB`, 'ltr') })
                            : undefined
                    }
                />
            </Stack>

            {notDownloaded ? (
                <Stack gap="sm">
                    <Divider />
                    <Text role="label" weight="semibold" accessibilityRole="header">{t('settings.advanced.notDownloaded')}</Text>
                    <Text role="caption" tone="muted">{t('settings.advanced.notDownloadedHint', { name: isolate(name, 'ltr'), need: needText(status) })}</Text>
                    <Button
                        label={downloading ? t('settings.advanced.downloading') : t('settings.model.download')}
                        variant="primary"
                        full
                        busy={downloading}
                        disabled={downloading}
                        onPress={() => void actions.download(false)}
                        testID="advanced-download"
                    />
                </Stack>
            ) : null}
            {downloading && downloadActive && progress ? (
                <ProgressLine value={progress.percent / 100} label={`${Math.round(progress.percent)}%`} />
            ) : null}

            {needsLoad ? (
                <Stack gap="sm">
                    <Divider />
                    <Text role="label" weight="semibold" accessibilityRole="header">{wrongModel ? t('settings.advanced.wrongModel') : t('settings.advanced.smallContext')}</Text>
                    <Text role="caption" tone="muted">{t('settings.advanced.loadHint', { name: isolate(name, 'ltr') })}</Text>
                    <Button
                        label={actions.pending === 'load' ? t('settings.advanced.loading') : t('settings.advanced.load')}
                        variant="primary"
                        full
                        busy={actions.pending === 'load'}
                        disabled={actions.pending === 'load'}
                        onPress={() => void actions.load()}
                        testID="advanced-load"
                    />
                </Stack>
            ) : null}

            {actions.error ? <ActionError error={actions.error} /> : null}

            <Disclosure title={t('settings.advanced.details')} open={detailsOpen} onToggle={onToggleDetails}>
                <Stack gap="sm">
                    {status.model_size_gb != null ? (
                        <StatusRow label={t('settings.advanced.modelSize')} value={<Ltr>{`${status.model_size_gb} GB`}</Ltr>} ok />
                    ) : null}
                    {status.model_device && !cloud ? (
                        <StatusRow label={t('settings.advanced.device')} value={<Ltr>{status.model_device.toUpperCase()}</Ltr>} ok={status.model_device !== 'cpu'} />
                    ) : null}
                    {status.model_context_size != null ? (
                        <StatusRow
                            label={t('settings.advanced.context')}
                            value={t.bare('settings.ctx.tokens', { size: isolate(`${(status.model_context_size / 1024).toFixed(0)}K`, 'ltr') })}
                            ok={status.context_size_sufficient}
                        />
                    ) : null}
                    {status.model_labels && status.model_labels.length > 0 ? (
                        <StatusRow label={t('settings.advanced.capabilities')} value={capabilitiesText(t, status.model_labels)} ok />
                    ) : null}
                    <StatusRow
                        label={t('settings.advanced.embedding')}
                        value={status.embedding_model_loaded ? t.bare('settings.advanced.available') : t.bare('settings.advanced.notLoaded')}
                        ok={status.embedding_model_loaded}
                    />
                    {status.gpu_name ? (
                        <StatusRow
                            label={t('resources.gpu')}
                            value={<Ltr>{`${status.gpu_name}${status.gpu_vram_gb ? ` (${status.gpu_vram_gb.toFixed(1)} GB)` : ''}`}</Ltr>}
                            ok
                        />
                    ) : null}
                    <StatusRow
                        label={t('settings.advanced.memory')}
                        value={memory != null ? <Ltr>{`${memory} GB`}</Ltr> : t.bare('settings.advanced.unknown')}
                        ok={memory != null && memory > 2}
                    />
                    {status.processor_name ? <StatusRow label={t('settings.advanced.processor')} value={<Ltr>{status.processor_name}</Ltr>} ok /> : null}
                    {status.lemonade_running && modelName !== null && !cloud ? (
                        <Row gap="md">
                            <Stack flex={1}>
                                <Text role="caption" tone="muted">{t('settings.advanced.corrupted')}</Text>
                            </Stack>
                            <Button
                                label={downloading ? t.bare('settings.advanced.downloading') : t.bare('settings.advanced.redownload')}
                                accessibilityLabel={downloading ? t('settings.advanced.downloading') : t('settings.advanced.redownload')}
                                variant="secondary"
                                size="sm"
                                busy={downloading}
                                disabled={downloading}
                                onPress={() => void actions.download(true)}
                                testID="advanced-redownload"
                            />
                        </Row>
                    ) : null}
                </Stack>
            </Disclosure>

            <LoadAnywaySheet
                open={actions.loadBlocked !== null}
                name={name}
                onClose={actions.dismissLoadBlocked}
                onConfirm={() => {
                    actions.dismissLoadBlocked();
                    void actions.load(true);
                }}
            />
        </Stack>
    );
}

