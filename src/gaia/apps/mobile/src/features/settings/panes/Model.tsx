// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { providerName, providerNameFull, providerNotice } from '@webui/utils/providerText';

import type { ProviderId, ProviderModel } from '@/api/client.types';
import {
    useActiveModel,
    useLocalModelStatus,
    useProviderModels,
    useProviders,
    useSelectModel,
    useSystemStatus,
} from '@/data';
import { useT } from '@/i18n/useT';
import { Card, Disclosure, Notice, PillTabs, Row, Spinner, Stack, Text } from '@/ui';
import { Cloud, Cpu } from '@/ui/icons';

import { isPcRefusal, PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';
import { CloudPrices } from './model/CloudPrices';
import { CloudSpending } from './model/CloudSpending';
import { GpuMemory } from './model/GpuMemory';
import { LocalDownloads } from './model/LocalDownloads';
import { LocalModels } from './model/LocalModels';
import { ModelInUse } from './model/ModelInUse';
import { ModelList } from './model/ModelList';
import { ProviderKey } from './model/ProviderKey';
import { errorText, isolate, modelLabel, sameModelId } from './model/modelText';

/** Memory for models, read from the PC only while "How this works" is open. */
function LocalMemory() {
    const t = useT();
    const status = useLocalModelStatus(false);
    const s = status.data;
    const memory = s?.memory ? isolate(`${s.memory.total_gb} GB (${s.memory.source})`) : s?.memory_error;
    if (!memory) return null;
    return <Text role="caption" tone="muted">{t('settings.model.memory', { memory })}</Text>;
}

export default function Model({ provider: requested }: { provider?: string }) {
    const t = useT();
    // The PC serves its provider list (with key details) only to itself; a paired phone isn't asked.
    const providersQuery = useProviders();
    const activeQuery = useActiveModel();
    const systemStatus = useSystemStatus();
    const select = useSelectModel();
    const [picked, setPicked] = useState<ProviderId | null>(null);
    const [howOpen, setHowOpen] = useState(false);
    const [switched, setSwitched] = useState<string | null>(null);
    const [inUseRow, setInUseRow] = useState<ProviderModel | null>(null);

    const providers = providersQuery.data?.providers;
    const active = activeQuery.data;
    const asked = providers?.find((p) => p.id === requested)?.id;
    const chosen: ProviderId = picked ?? asked ?? active?.provider ?? 'local';
    const provider = providers?.find((p) => p.id === chosen) ?? null;
    const blocked = !!provider?.blocked;
    const connected = !!provider && !blocked && provider.configured;
    const local = provider?.id === 'local';
    const serverHung = !!systemStatus.data && !systemStatus.data.lemonade_running && !!systemStatus.data.lemonade_error;

    const modelsQuery = useProviderModels(chosen, connected);
    const models = modelsQuery.data?.models;
    const listedInUse = models?.find((m) => sameModelId(active?.model, m.id));
    if (listedInUse && listedInUse !== inUseRow) setInUseRow(listedInUse);

    const ready = (models ?? []).filter((m) => m.available !== false && m.downloaded);
    const inCollapsedView = (m: ProviderModel) => m.rank !== null || sameModelId(m.id, active?.model);
    const ranked = ready.filter(inCollapsedView);
    const choices = !connected ? [] : chosen === 'fireworks' && ranked.some((m) => m.rank !== null) ? ranked : ready;

    const switchTo = (model: string) => {
        setSwitched(null);
        select.mutate(model, {
            onSuccess: () => {
                const m = models?.find((x) => x.id === model);
                setSwitched(t('settings.model.switched', { name: isolate(m ? modelLabel(m) : modelLabel({ id: model })) }));
            },
        });
    };

    const pcOnly = isPcRefusal(providersQuery.error);
    const loadError = pcOnly ? null : providersQuery.error ? errorText(providersQuery.error, t) : providersQuery.data?.lemonadeError;
    const row = listedInUse ?? (sameModelId(inUseRow?.id, active?.model) ? inUseRow : null);

    return (
        <Stack gap="lg" paddingX="lg" paddingY="md">
            <ModelInUse
                active={active}
                readFailed={!!activeQuery.error}
                row={row}
                waitingServer={serverHung}
                choices={choices}
                busy={select.isPending}
                onSwitch={switchTo}
            />
            {select.isPending ? (
                <Row gap="sm">
                    <Spinner />
                    <Text role="caption" tone="muted">{t('settings.ctx.applying')}</Text>
                </Row>
            ) : null}
            {switched ? <Notice tone="success" message={switched} /> : null}
            {select.error ? <Notice tone="danger" message={errorText(select.error, t)} /> : null}

            <Stack gap="sm">
                {pcOnly ? <PcOnlyNotice testID="model-providers-pc-only" /> : null}
                {providers ? (
                    <PillTabs
                        accessibilityLabel={t('settings.model.whereRuns')}
                        tabs={providers.map((p) => ({
                            value: p.id,
                            label: providerName(t, p),
                            accessibilityLabel: providerNameFull(t, p),
                            icon: p.remote ? Cloud : Cpu,
                            dot: p.remote && p.configured ? t('settings.key.connectedDot') : undefined,
                            disabled: !!p.blocked,
                        }))}
                        value={chosen}
                        onChange={(v) => setPicked(v as ProviderId)}
                        testID="model-providers"
                    />
                ) : null}
            </Stack>

            {!providers && !loadError && !pcOnly ? (
                <Row gap="sm">
                    <Spinner />
                    <Text role="label" tone="muted">
                        {serverHung ? t('settings.model.waitingServer') : t('settings.loading')}
                    </Text>
                </Row>
            ) : null}
            {loadError ? (
                <Notice tone="danger" message={loadError} action={{ label: t('settings.key.checkAgain'), onPress: () => void providersQuery.refetch() }} />
            ) : null}

            {provider ? (
                <Card padding="md">
                    <Stack gap="md">
                        {provider.remote ? <Notice tone="info" role="note" message={providerNotice(t, provider)} /> : null}
                        {blocked ? <Notice tone="danger" message={t('settings.privacy.localOnlyBlocked')} /> : null}
                        <Disclosure title={t('settings.model.howItWorks')} open={howOpen} onToggle={() => setHowOpen((o) => !o)}>
                            <Stack gap="sm">
                                <Text role="caption" tone="muted">{t('mobileApp.settings.model.lede')}</Text>
                                {local ? <Text role="caption" tone="muted">{providerNotice(t, provider)}</Text> : null}
                                {local ? <Text role="caption" tone="muted">{t('settings.model.devices')}</Text> : null}
                                {local ? <LocalMemory /> : null}
                            </Stack>
                        </Disclosure>
                        {provider.remote && !connected ? (
                            <Stack gap="sm">
                                <Text role="label" weight="medium" tone="secondary">{t('settings.model.prices')}</Text>
                                <CloudPrices provider={provider.id} />
                            </Stack>
                        ) : null}
                        {provider.remote && !blocked ? (
                            <SettingsAnchor name="cloud-key">
                                <ProviderKey key={provider.id} provider={provider} connected={connected} />
                            </SettingsAnchor>
                        ) : null}
                        {isPcRefusal(modelsQuery.error) ? (
                            <PcOnlyNotice testID="model-list-pc-only" />
                        ) : modelsQuery.error ? (
                            <Notice tone="danger" message={errorText(modelsQuery.error, t)} />
                        ) : null}
                        {local && connected ? <LocalDownloads /> : null}
                        {connected ? (
                            <SettingsAnchor name="models-on-pc">
                                <ModelList
                                    provider={provider.id}
                                    remote={!!provider.remote}
                                    models={models}
                                    activeModel={active?.model}
                                    switching={select.isPending ? (select.variables ?? null) : null}
                                    onUse={switchTo}
                                    onRefresh={() => void modelsQuery.refetch()}
                                />
                            </SettingsAnchor>
                        ) : null}
                        {local && connected ? <LocalModels /> : null}
                        {local && connected ? <GpuMemory /> : null}
                        {provider.remote ? <CloudSpending /> : null}
                    </Stack>
                </Card>
            ) : null}
        </Stack>
    );
}
