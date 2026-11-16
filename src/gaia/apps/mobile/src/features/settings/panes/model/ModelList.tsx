// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Children, useMemo, useState, type ReactNode } from 'react';

import { isDownloading } from '@webui/components/modelMeta';
import { distinctLabels, groupModels } from '@webui/utils/modelList.core';

import type { ProviderModel } from '@/api/client.types';
import { useRemoveCustomModel, useStartModelDownload } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Divider, Notice, Row, SearchField, SettingsGroup, Sheet, Spinner, Stack, Text } from '@/ui';

import { AddCustomModel } from './AddCustomModel';
import { ModelRow } from './ModelRow';
import { errorText, isolate, modelLabel, refusedOnPhone, sameModelId } from './modelText';

const SEARCHABLE_FROM = 6;

export interface ModelListProps {
    provider: string;
    remote: boolean;
    /** Undefined until the PC has answered. */
    models: ProviderModel[] | undefined;
    activeModel: string | null | undefined;
    /** The model a Use is switching to, while it does. */
    switching: string | null;
    onUse: (model: string) => void;
    /** Read the list again. */
    onRefresh: () => void;
}

/** One group of rows behind a Disclosure; `force` holds it open while a search or a download needs it seen. */
function Group({ id, title, count, defaultOpen, force, children }: {
    id: string;
    title: string;
    count: number;
    defaultOpen: boolean;
    force: boolean;
    children: ReactNode;
}) {
    const [picked, setPicked] = useState<boolean | null>(null);
    const open = force || (picked ?? defaultOpen);
    return (
        <Stack testID={`model-group-${id}`}>
            <SettingsGroup title={title} count={count} open={open} onToggle={() => setPicked(!open)}>
                <Stack gap="xs">
                    {Children.toArray(children).flatMap((c, i) => (i === 0 ? [c] : [<Divider key={`d${i}`} />, c]))}
                </Stack>
            </SettingsGroup>
        </Stack>
    );
}

/** Settings > Model's list: what is on the PC, what can be downloaded, and what can't run here. */
export function ModelList({ provider, remote, models, activeModel, switching, onUse, onRefresh }: ModelListProps) {
    const t = useT();
    const download = useStartModelDownload();
    const remove = useRemoveCustomModel();
    const [query, setQuery] = useState('');
    const [showAll, setShowAll] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const [pcOnly, setPcOnly] = useState(false);
    const [justAdded, setJustAdded] = useState(false);
    const [adding, setAdding] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState<ProviderModel | null>(null);

    const groups = useMemo(
        () => groupModels(models, { query, provider, showAll, activeModel }),
        [models, query, provider, showAll, activeModel],
    );
    const labels = useMemo(() => distinctLabels(models ?? [], (id) => modelLabel({ id })), [models]);
    const searching = !!query.trim();
    const local = !remote;
    const anyDownloading = groups.toDownload.some(isDownloading);
    const effectiveBusy = switching ?? busy;

    const fail = (err: unknown) => {
        if (refusedOnPhone(err)) setPcOnly(true);
        else setNotice({ ok: false, text: errorText(err, t) });
    };

    const startDownload = async (m: ProviderModel) => {
        setBusy(`dl:${m.id}`);
        setNotice(null);
        setPcOnly(false);
        setJustAdded(false);
        try {
            await download.mutateAsync(m.id);
        } catch (err) {
            fail(err);
        } finally {
            setBusy(null);
        }
    };

    const removeModel = async (m: ProviderModel) => {
        setConfirmRemove(null);
        setBusy(`rm:${m.id}`);
        setNotice(null);
        setPcOnly(false);
        try {
            const r = await remove.mutateAsync(m.id);
            const key = r.cleared.includes('last_model') ? 'settings.model.removedLast'
                : r.cleared.includes('default_model') ? 'settings.model.removedDefault' : 'settings.model.removed';
            setNotice({ ok: true, text: t(key, { name: r.display_name }) });
        } catch (err) {
            fail(err);
        } finally {
            setBusy(null);
        }
    };

    const renderRow = (m: ProviderModel) => (
        <ModelRow
            key={m.id}
            model={m}
            label={labels[m.id] ?? modelLabel(m)}
            current={sameModelId(activeModel, m.id)}
            remote={remote}
            busy={effectiveBusy}
            onUse={() => onUse(m.id)}
            onDownload={() => void startDownload(m)}
            onRemove={() => setConfirmRemove(m)}
            onNpuRecheck={onRefresh}
        />
    );

    return (
        <Stack gap="md" testID="model-list">
            <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">{t('settings.model.models')}</Text>
            {(models?.length ?? 0) > SEARCHABLE_FROM ? (
                <SearchField
                    value={query}
                    onChangeText={setQuery}
                    placeholder={t.bare('settings.model.search')}
                    accessibilityLabel={t('settings.model.search')}
                    testID="model-search"
                />
            ) : null}
            {pcOnly ? <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="model-pc-only" /> : null}
            {notice?.ok ? <Notice tone="success" message={notice.text} /> : null}
            {notice && !notice.ok ? <Notice tone="danger" message={notice.text} /> : null}
            {!models ? (
                <Row gap="sm">
                    <Spinner />
                    <Text role="label" tone="muted">{t('settings.loading')}</Text>
                </Row>
            ) : null}
            {models && groups.filtered.length === 0 ? (
                <Text role="label" tone="muted">{searching || remote ? t('settings.model.noMatch') : t('settings.model.noModels')}</Text>
            ) : null}
            {local ? (
                <>
                    {groups.onThisPc.length > 0 ? (
                        <Group id="downloaded" title={t('settings.model.groupDownloaded')} count={groups.onThisPc.length} defaultOpen force={searching}>
                            {groups.onThisPc.map(renderRow)}
                        </Group>
                    ) : null}
                    {groups.toDownload.length > 0 ? (
                        <Group
                            id="available"
                            title={t('settings.model.groupAvailable')}
                            count={groups.toDownload.length}
                            defaultOpen={groups.onThisPc.length === 0}
                            force={searching || justAdded || anyDownloading}
                        >
                            {groups.toDownload.map(renderRow)}
                        </Group>
                    ) : null}
                </>
            ) : (
                <Stack gap="sm">{groups.visibleRunnable.map(renderRow)}</Stack>
            )}
            {groups.collapsed && groups.hiddenCount > 0 ? (
                <Button label={t('settings.model.showAll')} onPress={() => setShowAll(true)} />
            ) : null}
            {provider === 'fireworks' && showAll && !searching ? (
                <Button label={t('settings.model.showRecommended')} onPress={() => setShowAll(false)} />
            ) : null}
            {groups.unavailable.length > 0 ? (
                <Group id="unavailable" title={t('mobileApp.settings.model.groupUnavailable')} count={groups.unavailable.length} defaultOpen={false} force={searching}>
                    {groups.unavailable.map(renderRow)}
                </Group>
            ) : null}
            {local ? (
                <>
                    <AddCustomModel
                        open={adding}
                        onToggle={() => setAdding((o) => !o)}
                        onClose={() => setAdding(false)}
                        onAdded={(name) => {
                            setNotice({ ok: true, text: t('settings.model.added', { name: isolate(name) }) });
                            setJustAdded(true);
                            onRefresh();
                        }}
                    />
                </>
            ) : null}
            <Sheet
                open={!!confirmRemove}
                onClose={() => setConfirmRemove(null)}
                title={confirmRemove ? t('settings.models.removeTitle', { name: isolate(labels[confirmRemove.id] ?? modelLabel(confirmRemove)) }) : undefined}
            >
                <Stack gap="md" padding="lg">
                    {confirmRemove?.downloaded ? (
                        <Text role="body">{t('mobileApp.settings.model.removeDownloadBody')}</Text>
                    ) : null}
                    <Button
                        variant="danger"
                        full
                        wrap
                        label={confirmRemove?.downloaded ? t('settings.model.removeDownload') : t('settings.models.remove')}
                        onPress={() => confirmRemove && void removeModel(confirmRemove)}
                        testID="model-remove-confirm"
                    />
                    <Button full label={t('settings.models.keep')} onPress={() => setConfirmRemove(null)} />
                </Stack>
            </Sheet>
        </Stack>
    );
}
