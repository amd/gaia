// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { isolate } from '@webui/i18n/registry';
import { backendLabel, loadedModelDetail, modelKindLabel, OWNER_LABELS, runsOn } from '@webui/utils/modelServer.core';

import { modelServerKeys, useModelActions, useModelServer, useSetModelServerBackend } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Button, Ltr, Notice, Row, SegmentedControl, Spinner, Stack, Text } from '@/ui';

import { modelLabel } from '@/features/composer/ModelMenu';
import { openSettings } from '../../openSettings';
import { isPcRefusal, PcOnlyNotice } from '../../PcOnlyNotice';
import { ActionError, LoadAnywaySheet, RawDetails } from './errors';

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
    return (
        <Row gap="md" align="start">
            <Stack flex={1}>
                <Text role="label" tone="secondary">{label}</Text>
            </Stack>
            <Stack flex={1} align="end">
                <Text role="label" weight="medium" align="end">{children}</Text>
            </Stack>
        </Row>
    );
}

/** Which model server GAIA uses, where it is, what each loaded model runs on, and the llama.cpp backend. */
export function ModelServerPanel() {
    const t = useT();
    const queryClient = useQueryClient();
    const query = useModelServer();
    const setBackend = useSetModelServerBackend();
    const info = query.data;
    const chat = info?.chat_model ?? null;
    const actions = useModelActions(chat?.name ?? null, undefined);
    const [reloaded, setReloaded] = useState(false);
    const wasLoading = useRef(false);

    useEffect(() => {
        if (actions.pending === 'load') wasLoading.current = true;
        else if (wasLoading.current) {
            wasLoading.current = false;
            void queryClient.invalidateQueries({ queryKey: modelServerKeys.info });
        }
    }, [actions.pending, queryClient]);

    const changeModel = (
        <Button label={t('settings.server.changeModel')} variant="secondary" full onPress={() => openSettings('model', undefined, 'advanced')} testID="advanced-change-model" />
    );

    if (query.isPending) {
        return (
            <Row gap="sm">
                <Spinner />
                <Text role="label" tone="muted">{t('settings.server.checkingServer')}</Text>
            </Row>
        );
    }
    if (!info && isPcRefusal(query.error)) return <PcOnlyNotice testID="advanced-server-pc-only" />;
    if (!info) {
        return (
            <Stack gap="sm">
                <Notice tone="warning" message={t('settings.errors.serverDownTitle')} testID="advanced-server-unreachable" />
                <RawDetails text={describeError(query.error, t)} />
            </Stack>
        );
    }
    if (!info.reachable) {
        return (
            <Stack gap="sm">
                <Notice tone="warning" message={t('settings.errors.serverDownTitle')} testID="advanced-server-unreachable" />
                {info.error ? <RawDetails text={info.error} /> : null}
                {changeModel}
            </Stack>
        );
    }

    // Only backends GAIA has tested, which the server lists; the server decides, the phone adds none.
    const available = info.available_backends ?? [];
    const preferred = info.preferred_backend ?? null;
    const choices = available.map((b) => b.name);
    if (preferred && !choices.includes(preferred)) choices.push(preferred);
    const selected = preferred ?? info.default_backend ?? null;
    const installable = available.filter((b) => b.state === 'installable').map((b) => backendLabel(b.name));
    const chatIsLocalLlamacpp = !!chat?.name && chat.recipe === 'llamacpp';
    const owner = OWNER_LABELS[info.owner] ? t.bare(OWNER_LABELS[info.owner]!) : info.owner;
    const reloading = actions.pending === 'load';
    const extra = (info.loaded_models ?? []).filter((m) => !(m.name && m.name === chat?.name) && !(m.name && m.name === info.embedding_model?.name));

    return (
        <Stack gap="md">
            <Stack gap="md">
                <InfoRow label={t('settings.server.server')}>
                    {owner}
                    {info.version ? <> <Ltr>{`v${info.version}`}</Ltr></> : null}
                </InfoRow>
                <InfoRow label={t('settings.server.addressShort')}><Ltr>{info.base_url}</Ltr></InfoRow>
                <InfoRow label={t('settings.server.chatModel')}>
                    {chat?.name ? <Ltr>{modelLabel({ id: chat.name })}</Ltr> : t.bare('settings.advanced.noneLoaded')}
                </InfoRow>
                {chat ? <InfoRow label={t('settings.server.runsOn')}><Ltr>{runsOn(chat)}</Ltr></InfoRow> : null}
                {chat && chat.backend !== 'remote' ? (
                    <InfoRow label={t('settings.advanced.context')}>
                        {chat.ctx_size
                            ? t.bare('settings.ctx.tokens', { size: isolate(`${Math.round(chat.ctx_size / 1024)}K`, 'ltr') })
                            : t.bare('settings.advanced.unknown')}
                    </InfoRow>
                ) : null}
                <InfoRow label={t('settings.advanced.embedding')}>
                    {info.embedding_model?.name ? <Ltr>{`${modelLabel({ id: info.embedding_model.name })} · ${runsOn(info.embedding_model)}`}</Ltr> : t.bare('settings.advanced.noneLoaded')}
                </InfoRow>
                {extra.map((model, index) => (
                    <InfoRow key={`${model.name ?? 'unknown'}-${index}`} label={modelKindLabel(model, t)}>
                        <Ltr>{loadedModelDetail(model, t, (id) => modelLabel({ id }))}</Ltr>
                    </InfoRow>
                ))}
            </Stack>
            {info.embedding_note ? <RawDetails text={info.embedding_note} /> : null}
            {info.system_info_error ? <RawDetails text={info.system_info_error} /> : null}

            <Stack gap="sm">
                <Text role="label" weight="semibold" accessibilityRole="header">{t('settings.server.backend')}</Text>
                {available.length === 0 && !preferred ? (
                    <Text role="caption" tone="muted">{t('mobileApp.settings.serverNoBackends')}</Text>
                ) : (
                    <>
                        <SegmentedControl
                            options={choices.map((name) => ({ value: name, label: backendLabel(name) }))}
                            value={selected ?? ''}
                            accessibilityLabel={t('settings.server.backendShort')}
                            onChange={(name) => {
                                if (name !== selected && !setBackend.isPending) setBackend.mutate(name);
                            }}
                            testID="advanced-backend"
                        />
                        <Text role="caption" tone="muted">
                            {t('mobileApp.settings.serverBackendHint')}
                            {installable.length > 0 ? ` ${t('settings.server.installable', { names: installable.join(', ') })}` : ''}
                        </Text>
                    </>
                )}
                {setBackend.isSuccess ? <Notice tone="success" message={t('settings.server.savedBackend')} /> : null}
                {setBackend.isError ? <ActionError error={setBackend.error} /> : null}
            </Stack>

            <Stack gap="sm">
                {changeModel}
                {chatIsLocalLlamacpp ? (
                    <Button
                        label={t('settings.server.reload')}
                        variant="secondary"
                        full
                        busy={reloading}
                        disabled={reloading}
                        onPress={() => {
                            setReloaded(true);
                            void actions.load();
                        }}
                        testID="advanced-reload"
                    />
                ) : null}
                {reloaded && chat?.name && !actions.error && actions.loadBlocked === null ? (
                    <Notice tone="success" message={t('settings.server.reloading', { name: isolate(chat.name, 'ltr') })} />
                ) : null}
                {actions.error ? <ActionError error={actions.error} /> : null}
            </Stack>

            <LoadAnywaySheet
                open={actions.loadBlocked !== null}
                name={chat?.name ?? ''}
                onClose={actions.dismissLoadBlocked}
                onConfirm={() => {
                    actions.dismissLoadBlocked();
                    void actions.load(true);
                }}
            />
        </Stack>
    );
}
