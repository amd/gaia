// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useRef, useState } from 'react';

import { formatBytes, formatSize, ltrIn } from '@webui/components/setup/format';
import {
    catalogOf, chatCandidates, chooseChat, initialSelection, isNpu, isVision, mainRows, moreRows, pendingDownloads,
    shortfall, toggle, tooBig, type Selection,
} from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupModelProgress, SetupPlan } from '@webui/types';

import { useRemoveSetupModel, useSaveModelChoices, useSetupPlan, useSetupStatus } from '@/data';
import { describeErrorWith, useLocaleCode, useT } from '@/i18n/useT';
import { Badge, Button, Disclosure, Notice, Row, Select, SettingsGroup, Spinner, Stack, Text } from '@/ui';

import { SelectionSummary } from '../../../setup/steps/Checklist';
import { MarkedRow, Warn } from '../../../setup/steps/ModelRows';
import { fitLine, purposeChips, purposeLine, skipCaveat, statusText, tooBigText } from '../../../setup/steps/modelsView';
import { useConfirmSheet } from '../../../setup/sheets/ConfirmSheet';
import { lowDiskRequest, noFitRequest, serverConfirmRequest, setupConfirmFrom } from '../../../setup/sheets/setupConfirm';
import { useDownloadQueue } from './downloadQueue';
import { LocalError } from './LocalError';

const sameSet = (a: ReadonlySet<string>, b: ReadonlySet<string>) => a.size === b.size && [...a].every((x) => b.has(x));

function ChecklistRow({ entry: e, ticked, disabled, progress, imagesCovered, canRemove, onToggle, onRemove }: {
    entry: SetupCatalogEntry;
    ticked: boolean;
    disabled: boolean;
    progress?: SetupModelProgress;
    imagesCovered: boolean;
    canRemove: boolean;
    onToggle: (on: boolean) => void;
    onRemove: () => void;
}) {
    const t = useT();
    const locale = useLocaleCode();
    const downloading = progress?.state === 'downloading' && progress.bytes_total > 0;
    const warning = e.fits === false ? tooBigText(e, t, locale) : !ticked ? skipCaveat(e, t, imagesCovered) : null;
    const status = downloading
        ? t('setup.models.status.downloading', { done: ltrIn(locale, formatBytes(progress.bytes_done)), total: ltrIn(locale, formatBytes(progress.bytes_total)) })
        : statusText(e, t, locale);
    const note = [purposeLine(e, t), fitLine(e, t, locale), e.fits === null ? t('mobileApp.setup.models.fit.unknown') : null].filter(Boolean).join(' ');
    return (
        <Stack gap="xs">
            <MarkedRow
                kind="checkbox"
                checked={ticked}
                disabled={disabled}
                label={e.name}
                onPress={() => onToggle(!ticked)}
                name={<Text role="ui" weight="medium" dir="ltr">{e.name}</Text>}
                chips={
                    <>
                        {purposeChips(e, t).map((c) => <Badge key={c} label={c} />)}
                        {isNpu(e) ? <Badge label={t('setup.models.npu')} /> : null}
                    </>
                }
            >
                <Text role="caption" tone="secondary">{status}</Text>
                <Text role="caption" tone="muted">{note}</Text>
                {warning ? <Warn message={warning} /> : null}
            </MarkedRow>
            {canRemove ? (
                <Row>
                    <Button
                        variant="ghost"
                        size="sm"
                        label={t.bare('settings.models.remove')}
                        accessibilityLabel={t('settings.models.removeAria', { name: e.name })}
                        disabled={disabled || downloading}
                        onPress={onRemove}
                        testID={`local-remove-${e.id}`}
                    />
                </Row>
            ) : null}
        </Stack>
    );
}

/** Settings > Model > "Manage downloads and disk space": tick what stays on the PC, remove downloads, set the default chat model. */
export function LocalModels() {
    const t = useT();
    const locale = useLocaleCode();
    const planQuery = useSetupPlan();
    const plan = planQuery.data;
    const statusQuery = useSetupStatus();
    const save = useSaveModelChoices();
    const remove = useRemoveSetupModel();
    const enqueue = useDownloadQueue((s) => s.enqueue);
    const { ask, sheet } = useConfirmSheet();
    const [picked, setPicked] = useState<boolean | null>(null);
    const [moreOpen, setMoreOpen] = useState(false);
    const [selection, setSelection] = useState<Selection | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text?: string; error?: unknown } | null>(null);

    const progress = useMemo(() => {
        const out: Record<string, SetupModelProgress> = {};
        for (const m of statusQuery.data?.models ?? []) out[m.id] = m;
        return out;
    }, [statusQuery.data]);

    // The plan seeds the ticks once, and again whenever the models on the PC change.
    const onPc = (plan?.catalog ?? []).filter((e) => e.downloaded).map((e) => e.id).join(',');
    const seeded = useRef<string | null>(null);
    useEffect(() => {
        if (!plan || seeded.current === onPc) return;
        seeded.current = onPc;
        setSelection(initialSelection(plan));
    }, [plan, onPc]);

    // A model that finishes downloading is on the PC now: read the plan again.
    const readyIds = (statusQuery.data?.models ?? []).filter((m) => m.state === 'ready').map((m) => m.id).join(',');
    const firstReady = useRef(readyIds);
    useEffect(() => {
        if (readyIds === firstReady.current) return;
        firstReady.current = readyIds;
        void planQuery.refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch is stable enough; only a changed ready set matters
    }, [readyIds]);

    const busy = save.isPending || remove.isPending;
    const force = !!planQuery.error || notice?.ok === false;
    const open = force || (picked ?? false);
    const catalog = catalogOf(plan ?? null);
    const baseline = useMemo(() => (plan ? initialSelection(plan) : null), [plan]);
    const toDownload = plan && selection ? pendingDownloads(catalog, selection).filter((e) => progress[e.id]?.state !== 'downloading') : [];
    const downloadGb = toDownload.reduce((sum, e) => sum + (e.size_gb ?? 0), 0);
    const staged = !!plan && !!selection && !!baseline
        && (!sameSet(selection.ticked, baseline.ticked) || selection.defaultChat !== baseline.defaultChat || toDownload.length > 0);

    const apply = async (plan: SetupPlan, sel: Selection, ackNoFit: boolean, ackDisk: boolean): Promise<void> => {
        setNotice(null);
        if (!ackNoFit) {
            const big = tooBig(catalog, sel).filter((e) => !e.downloaded || e.id === sel.defaultChat);
            if (big.length > 0 && big.every((e) => e.need_gb != null)) {
                const request = noFitRequest(big.map((e) => ({ name: e.name, why: tooBigText(e, t, locale) })), t);
                if (await ask(request)) await apply(plan, sel, true, ackDisk);
                return;
            }
        }
        const short = shortfall(plan, sel);
        if (short && !ackDisk) {
            if (await ask(lowDiskRequest(short, t))) await apply(plan, sel, true, true);
            return;
        }
        try {
            const latest = await save.mutateAsync({ plan, selection: sel, acknowledgeNoFit: ackNoFit });
            const ids = pendingDownloads(catalogOf(latest), sel).map((e) => e.id).filter((id) => progress[id]?.state !== 'downloading');
            setSelection({ ...sel, defaultChat: latest.chat_model.id });
            if (ids.length) enqueue(ids, true);
            setNotice({ ok: true, text: t('settings.models.applied') });
        } catch (err) {
            const detail = setupConfirmFrom(err);
            if (detail && !ackNoFit) {
                if (await ask(serverConfirmRequest(detail, t))) await apply(plan, sel, true, ackDisk);
                return;
            }
            setNotice({ ok: false, error: err });
        }
    };

    const removeModel = async (e: SetupCatalogEntry): Promise<void> => {
        const size = ltrIn(locale, formatSize(e.full_size_gb ?? 0));
        const confirmed = await ask({
            title: t('settings.models.removeTitle', { name: e.name }),
            body: [t(isVision(e) ? 'settings.models.removeVision' : 'settings.models.removeBody', { size })],
            confirmLabel: t('settings.models.removeConfirm'),
            cancelLabel: t('settings.models.keep'),
            danger: true,
        });
        if (!confirmed) return;
        setNotice(null);
        try {
            const r = await remove.mutateAsync({ modelId: e.id });
            setSelection(initialSelection(r));
            setNotice({ ok: true, text: t('settings.models.removed', { name: e.name, size: ltrIn(locale, formatSize(r.freed_gb)) }) });
        } catch (err) {
            setNotice({ ok: false, error: err });
        }
    };

    const row = (e: SetupCatalogEntry) => (
        <ChecklistRow
            key={e.id}
            entry={e}
            ticked={selection!.ticked.has(e.id)}
            disabled={busy}
            progress={progress[e.id]}
            imagesCovered={catalog.some((o) => o.id !== e.id && isVision(o) && (selection!.ticked.has(o.id) || o.downloaded === true))}
            canRemove={!!e.downloaded && e.id !== selection!.defaultChat}
            onToggle={(on) => setSelection(toggle(selection!, catalog, e.id, on))}
            onRemove={() => void removeModel(e)}
        />
    );
    const more = moreRows(catalog);
    const candidates = selection ? chatCandidates(catalog, selection.ticked) : [];

    return (
        <Stack gap="xs" testID="local-models">
            <SettingsGroup title={t.bare('settings.models.manage')} hint={t('mobileApp.settings.models.manageHint')} open={open} onToggle={() => setPicked(!open)} headerTestID="local-models-toggle">
                <Stack gap="md">
                    <Text role="caption" tone="muted">{t('mobileApp.settings.models.lede')}</Text>
                    {planQuery.error ? (
                        <Notice
                            tone="danger"
                            message={t.compose((say) => say('setup.models.listFailed', { reason: describeErrorWith(planQuery.error, say) }))}
                            action={{ label: t('setup.tryAgain'), onPress: () => void planQuery.refetch() }}
                        />
                    ) : null}
                    {plan && !plan.lemonade.reachable ? <Text role="caption" tone="muted">{t('mobileApp.settings.models.unavailable')}</Text> : null}
                    {!plan && !planQuery.error ? (
                        <Row gap="sm">
                            <Spinner size="sm" />
                            <Text role="caption" tone="muted">{t('mobileApp.setup.models.loading')}</Text>
                        </Row>
                    ) : null}
                    {plan && plan.lemonade.reachable && selection ? (
                        <>
                            <Stack gap="md">{mainRows(catalog).map(row)}</Stack>
                            {more.length > 0 ? (
                                <Disclosure title={t('setup.models.more', { n: String(more.length) })} open={moreOpen} onToggle={() => setMoreOpen(!moreOpen)}>
                                    <Stack gap="md">{more.map(row)}</Stack>
                                </Disclosure>
                            ) : null}
                            <Select
                                label={t('setup.models.defaultChat')}
                                value={selection.defaultChat ?? ''}
                                disabled={busy || candidates.length === 0}
                                placeholder={t.bare('setup.models.noChat')}
                                options={candidates.map((e) => ({ value: e.id, label: `${e.name}${isNpu(e) ? ` (${t('setup.models.npu')})` : ''}` }))}
                                onChange={(id) => setSelection(chooseChat(selection, catalog, id))}
                            />
                            <SelectionSummary plan={plan} selection={selection} />
                            <Button
                                variant="primary"
                                full
                                busy={save.isPending}
                                disabled={!staged || busy}
                                label={save.isPending ? t('settings.models.applying') : toDownload.length > 0
                                    ? t('settings.models.applyDownload', { size: ltrIn(locale, formatSize(Math.round(downloadGb * 10) / 10)) })
                                    : t('settings.models.apply')}
                                onPress={() => void apply(plan, selection, false, false)}
                                testID="local-apply"
                            />
                        </>
                    ) : null}
                    {notice?.ok ? <Notice tone="success" message={notice.text} testID="local-notice-ok" /> : null}
                    {notice && !notice.ok ? <LocalError error={notice.error} /> : null}
                </Stack>
            </SettingsGroup>
            {sheet}
        </Stack>
    );
}
