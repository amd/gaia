// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';

import { isDownloading, modelState } from '@webui/components/modelMeta';

import { providerName } from '@webui/utils/providerText';

import type { ProviderModel } from '@webui/types';
import { useActiveModel, useDownloadModel, useProviderModels, useProviders, useSelectModel } from '@/data';
import { openSettings } from '@/features/settings/openSettings';
import { describeError, useT } from '@/i18n/useT';
import { Button, Divider, Icon, IconButton, ListRow, Ltr, Notice, Row, Sheet, Spinner, Stack, StatusDot, Text } from '@/ui';
import { Check, Download, Info, KeyRound } from '@/ui/icons';

import { modelDetails } from './modelDetails';

/** The id without its provider prefix or catalogue path, when the PC gave no friendly name. */
export function modelLabel(m: Pick<ProviderModel, 'id' | 'display_name'>): string {
    return m.display_name || m.id.slice(m.id.lastIndexOf('/') + 1);
}

const ICON_BOX = 16;

const sameModel = (a: string | null | undefined, b: string | null | undefined) =>
    !!a && !!b && a.toLowerCase().replace(/^user\./, '') === b.toLowerCase().replace(/^user\./, '');

export interface ModelMenuProps {
    open: boolean;
    onClose: () => void;
    /** A reply is running: a model picked now is used from the next message. */
    turnRunning: boolean;
    /** The model picked while a reply ran. */
    queued: string | null;
    onQueue: (model: string | null) => void;
    /** Why the switch after a download failed; the chip shows a warning while it is set. */
    switchError: string | null;
    onSwitchError: (message: string | null) => void;
    /** Starts with this row's facts open (developer gallery). */
    initialInfoRow?: string;
    /** Shows this row as being switched to (developer gallery). */
    forceSwitching?: string;
}

/** Switch the model, download one that isn't on the PC, or see what only the PC can do. */
export function ModelMenu({ open, onClose, turnRunning, queued, onQueue, switchError, onSwitchError, initialInfoRow, forceSwitching }: ModelMenuProps) {
    const t = useT();
    const active = useActiveModel().data;
    const local = useProviderModels('local', open);
    const cloud = useProviderModels(active?.remote ? active.provider : undefined, open);
    const providers = useProviders().data?.providers;
    const select = useSelectModel();
    const download = useDownloadModel();
    const [error, setError] = useState<string | null>(null);
    const [askDownload, setAskDownload] = useState<ProviderModel | null>(null);
    // The row whose facts are open; a phone has no hover, so each row has an info button.
    const [infoRow, setInfoRow] = useState<string | null>(initialInfoRow ?? null);
    const pickAfterDownload = useRef<string | null>(null);
    const [wasOpen, setWasOpen] = useState(open);
    const current = queued ?? active?.model;
    const switchingTo = select.isPending ? select.variables : forceSwitching;

    // A fresh open starts clean; reset while rendering, not in an effect.
    if (open !== wasOpen) {
        setWasOpen(open);
        if (open) {
            setError(null);
            setAskDownload(null);
            setInfoRow(initialInfoRow ?? null);
        }
    }

    const choose = (model: string, afterDownload?: string) => {
        setError(null);
        if (turnRunning) {
            onQueue(sameModel(model, active?.model) ? null : model);
            onClose();
            return;
        }
        select.mutate(model, {
            onSuccess: onClose,
            onError: (err) => {
                if (afterDownload) onSwitchError(t('modelMenu.switchAfterFailed', { model: afterDownload, reason: describeError(err, t) }));
                else setError(describeError(err, t));
            },
        });
    };

    // A model picked before it was on the PC is switched to once its download ends.
    const models = local.data?.models;
    useEffect(() => {
        if (!pickAfterDownload.current || !models) return;
        const picked = pickAfterDownload.current;
        const m = models.find((x) => x.id === picked);
        if (!m) {
            pickAfterDownload.current = null;
            onSwitchError(t('modelMenu.modelGone', { model: picked }));
            return;
        }
        if (isDownloading(m)) return;
        if (m.download_progress?.state === 'error') {
            pickAfterDownload.current = null;
            onSwitchError(t('modelMenu.downloadFailed', { model: modelLabel(m), message: m.download_progress.message ?? '' }));
        } else if (m.download_progress?.state === 'complete' || modelState(m) !== 'not_downloaded') {
            pickAfterDownload.current = null;
            onSwitchError(null);
            choose(m.id, modelLabel(m));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [models]);

    const startDownload = (m: ProviderModel, pick: boolean) => {
        setAskDownload(null);
        setError(null);
        onSwitchError(null);
        if (pick) pickAfterDownload.current = m.id;
        download.mutate(m.id, {
            onError: (err) => setError(describeError(err, t)),
        });
    };

    const row = (m: ProviderModel, remote: boolean) => {
        const state = modelState(m);
        const downloading = isDownloading(m);
        const needsDownload = !remote && state === 'not_downloaded' && !sameModel(current, m.id);
        const name = modelLabel(m);
        const switching = sameModel(switchingTo, m.id);
        let trailing;
        if (downloading) {
            trailing = (
                <Row gap="xs">
                    <Spinner size="sm" />
                    <Text role="caption" tone="secondary">{`${Math.round(m.download_progress?.percent ?? 0)}%`}</Text>
                </Row>
            );
        } else if (!remote && needsDownload) {
            trailing = <Icon icon={Download} size="sm" tone="secondary" label={t('modelMenu.downloadModel', { name })} />;
        } else if (!remote && state === 'loaded') {
            trailing = <StatusDot tone="success" solid label={t('modelStatus.state.loaded')} />;
        } else if (!remote) {
            trailing = (
                <Text role="caption" tone="secondary">
                    {t('modelMenu.ready')}
                </Text>
            );
        }
        return (
            <Stack key={`${remote ? 'cloud' : 'local'}:${m.id}`} gap="xs">
                <Row gap="xs" align="center">
                    <View style={{ flex: 1 }}>
                <ListRow
                    title={name}
                    subtitle={!remote && m.size_gb ? `${m.size_gb} GB` : undefined}
                    leading={
                        <View style={{ width: ICON_BOX, height: ICON_BOX, alignItems: 'center', justifyContent: 'center' }}>
                            {switching ? <Spinner size="sm" /> : sameModel(current, m.id) ? <Icon icon={Check} size="sm" tone="accent" /> : null}
                        </View>
                    }
                    trailing={trailing}
                    selected={sameModel(current, m.id)}
                    onPress={
                        downloading || select.isPending
                            ? undefined
                            : () => {
                                  pickAfterDownload.current = null;
                                  if (needsDownload) setAskDownload(m);
                                  else choose(m.id);
                              }
                    }
                    testID={`model-row-${m.id}`}
                />
                    </View>
                    <IconButton
                        icon={Info}
                        size="sm"
                        label={t('modelMenu.about', { name })}
                        onPress={() => setInfoRow(infoRow === m.id ? null : m.id)}
                        testID={`model-info-${m.id}`}
                    />
                </Row>
                {infoRow === m.id ? (
                    <Stack gap="xs" paddingX="md" testID={`model-facts-${m.id}`}>
                        {modelDetails(m, remote, t).map((d) => (
                            <Text key={d} role="caption" tone="secondary">
                                {d}
                            </Text>
                        ))}
                        {m.placement?.warning ? (
                            <Text role="caption" tone="warning">
                                {m.placement.warning}
                            </Text>
                        ) : null}
                        <Text role="caption" tone="muted">
                            <Ltr>{m.id}</Ltr>
                        </Text>
                    </Stack>
                ) : null}
                {askDownload?.id === m.id ? (
                    <Notice
                        tone="info"
                        message={`${
                            m.size_gb
                                ? t('modelMenu.downloadConfirm', {
                                      name,
                                      size: `${m.size_gb} GB`,
                                  })
                                : t('modelMenu.downloadConfirmNoSize', { name })
                        } ${t('modelMenu.switchWhenDone')}`}
                        action={{
                            label: t('modelMenu.download'),
                            onPress: () => startDownload(m, true),
                        }}
                        onDismiss={() => setAskDownload(null)}
                    />
                ) : null}
            </Stack>
        );
    };

    const localModels = (local.data?.models ?? []).filter((m) => m.available !== false);
    const ordered = [
        ...localModels.filter((m) => sameModel(current, m.id) || modelState(m) !== 'not_downloaded' || isDownloading(m)),
        ...localModels.filter((m) => !sameModel(current, m.id) && modelState(m) === 'not_downloaded' && !isDownloading(m)),
    ];
    const firstToDownload = ordered.find((m) => !sameModel(current, m.id) && modelState(m) === 'not_downloaded' && !isDownloading(m));
    const unavailable = (local.data?.models ?? []).filter((m) => m.available === false).length;
    const needsKey = providers?.filter((p) => p.remote && !p.configured) ?? [];
    const toSettings = (target?: string) => {
        onClose();
        openSettings('model', target);
    };
    const cloudModels = (cloud.data?.models ?? []).filter((m) => m.available !== false);

    return (
        <Sheet open={open} onClose={onClose} title={t('modelMenu.label')}>
            <Stack gap="sm">
                {turnRunning ? (
                    <Text role="caption" tone="secondary">
                        {active?.model
                            ? t('modelMenu.keepsModel', {
                                  model: active.display_name || active.model,
                              })
                            : t('modelMenu.keepsItsModel')}
                    </Text>
                ) : null}
                {switchError ? <Notice tone="danger" message={switchError} onDismiss={() => onSwitchError(null)} /> : null}
                {error ? <Notice tone="danger" message={error} /> : null}
                {local.isLoading ? (
                    <Row gap="sm">
                        <Spinner size="sm" />
                        <Text role="caption" tone="secondary">
                            {t('modelMenu.loading')}
                        </Text>
                    </Row>
                ) : null}
                {local.isError ? <Notice tone="danger" message={describeError(local.error, t)} /> : null}
                <View accessibilityRole="list" accessibilityLabel={t('modelMenu.models')}>
                <Stack gap="sm">
                {active?.remote && cloud.isSuccess && cloudModels.length === 0 ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary">
                            {active.provider_name || active.provider}
                        </Text>
                        <Text role="caption" tone="secondary">
                            {t('modelMenu.noModels')}
                        </Text>
                    </Stack>
                ) : null}
                {cloudModels.length > 0 && active ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary">
                            {active.provider_name || active.provider}
                        </Text>
                        {cloudModels.map((m) => row(m, true))}
                    </Stack>
                ) : null}
                {localModels.length > 0 ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary">
                            {t.bare('providers.local.name')}
                        </Text>
                        {ordered.map((m) => (
                            <Stack key={m.id} gap="xs">
                                {m === firstToDownload ? (
                                    <Text role="caption" tone="muted">
                                        {t('modelMenu.availableToDownload')}
                                    </Text>
                                ) : null}
                                {row(m, false)}
                            </Stack>
                        ))}
                    </Stack>
                ) : null}
                {local.isSuccess && localModels.length === 0 ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary">
                            {t.bare('providers.local.name')}
                        </Text>
                        <Text role="caption" tone="secondary">
                            {t('modelMenu.noModels')}
                        </Text>
                    </Stack>
                ) : null}
                </Stack>
                </View>
                <Divider />
                {needsKey.map((p) => (
                    <ListRow
                        key={p.id}
                        title={t('modelMenu.addKey')}
                        subtitle={t('modelMenu.connectInSettings', { provider: providerName(t, p) })}
                        leading={<Icon icon={KeyRound} size="sm" tone="secondary" />}
                        onPress={() => toSettings(p.id)}
                        testID={`model-menu-key-${p.id}`}
                    />
                ))}
                {unavailable > 0 ? (
                    <ListRow title={t.plural('modelMenu.unavailable', unavailable)} onPress={() => toSettings()} testID="model-menu-unavailable" />
                ) : null}
                <ListRow title={t('modelMenu.addFromLink')} subtitle={t('modelMenu.addFromLinkNote')} onPress={() => toSettings()} testID="model-menu-link" />
                <ListRow title={t('modelMenu.providers')} onPress={() => toSettings()} testID="model-menu-providers" />
                <Button variant="secondary" label={t('modelMenu.cancel')} onPress={onClose} />
            </Stack>
        </Sheet>
    );
}
