// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { formatBytes, formatSize, ltrIn } from '@webui/components/setup/format';
import {
    catalogOf, chatCandidates, installedOnlyRows, isNpu, isVision, mainRows, moreRows, shortfall, totals, type Selection,
} from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupModelProgress, SetupPlan } from '@webui/types';

import { useLocaleCode, useT } from '@/i18n/useT';
import { Badge, Card, Disclosure, Notice, ProgressLine, Row, SearchField, Select, Stack, Text } from '@/ui';

import { MarkedRow, Warn } from './ModelRows';
import { fitLine, purposeChips, purposeLine, skipCaveat, statusText, tooBigText } from './modelsView';

const SEARCH_ABOVE = 6;

/** "C:" from "C:\Users\me\.cache\huggingface\hub"; the folder itself elsewhere. */
function driveName(path: string | null): string | null {
    if (!path) return null;
    const win = /^([A-Za-z]:)[\\/]/.exec(path);
    return win ? win[1]! : path;
}

function Row1({ entry: e, ticked, onToggle, disabled, progress, imagesCovered }: {
    entry: SetupCatalogEntry;
    ticked: boolean;
    onToggle: (on: boolean) => void;
    disabled?: boolean;
    progress?: SetupModelProgress;
    imagesCovered: boolean;
}) {
    const t = useT();
    const locale = useLocaleCode();
    const big = e.fits === false;
    const downloading = progress?.state === 'downloading' && progress.bytes_total > 0;
    const warning = big ? tooBigText(e, t, locale) : !ticked ? skipCaveat(e, t, imagesCovered) : null;
    const status = downloading
        ? t('setup.models.status.downloading', { done: ltrIn(locale, formatBytes(progress.bytes_done)), total: ltrIn(locale, formatBytes(progress.bytes_total)) })
        : statusText(e, t, locale);
    const note = [purposeLine(e, t), fitLine(e, t, locale), e.fits === null ? t('setup.models.fit.unknown') : null].filter(Boolean).join(' ');
    return (
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
            <Row gap="sm" wrap>
                <Text role="caption" tone="secondary">{status}</Text>
                {e.recommended ? <Badge label={t('setup.models.recommended')} tone="accent" /> : null}
                {e.fits === null ? <Badge label={t('setup.models.fitUnknown')} tone="warning" /> : null}
            </Row>
            <Text role="caption" tone="muted">{note}</Text>
            {downloading ? (
                <ProgressLine value={progress.bytes_done / progress.bytes_total} label={t('setup.progress.rowLabel', { name: e.name, state: t('setup.state.downloading') })} />
            ) : null}
            {warning ? <Warn message={warning} /> : null}
        </MarkedRow>
    );
}

/** Download total against the free space on the model drive, live as the user ticks. */
export function SelectionSummary({ plan, selection }: { plan: SetupPlan; selection: Selection }) {
    const t = useT();
    const locale = useLocaleCode();
    const { downloadGb, freeGb, afterGb } = totals(plan, selection);
    const short = shortfall(plan, selection);
    const drive = driveName(plan.disk?.path ?? null);
    const size = (gb: number) => ltrIn(locale, formatSize(gb));
    const spoken = downloadGb == null
        ? t('setup.summary.spokenUnknown')
        : afterGb != null
            ? t('setup.summary.spoken', { download: size(downloadGb), after: size(Math.max(0, afterGb)) })
            : t('setup.summary.spokenNoDisk', { download: size(downloadGb) });
    const shortArgs = short && {
        need: size(short.needGb), free: size(short.freeGb), drive: drive ? ltrIn(locale, drive) : t('setup.summary.theDrive'), freeUp: size(short.freeUpGb),
    };
    return (
        <Card accessibilityLabel={t('setup.summary.label')}>
            <Stack gap="sm">
                <Row gap="sm" justify="between">
                    <Text role="label" tone="secondary">{t('setup.summary.download')}</Text>
                    <Text role="label" dir="ltr">{downloadGb == null ? t('setup.summary.sizeUnknown') : formatSize(downloadGb)}</Text>
                </Row>
                <Row gap="sm" justify="between">
                    <Text role="label" tone="secondary">{drive ? `${t('setup.summary.freeOn')} ${ltrIn(locale, drive)}` : t('setup.summary.free')}</Text>
                    <Text role="label" dir="ltr">{freeGb == null ? t('setup.summary.freeUnknown') : formatSize(freeGb)}</Text>
                </Row>
                {afterGb != null && !short ? (
                    <Row gap="sm" justify="between">
                        <Text role="label" tone="secondary">{t('setup.summary.after')}</Text>
                        <Text role="label" dir="ltr">{formatSize(Math.max(0, afterGb))}</Text>
                    </Row>
                ) : null}
                {short && shortArgs ? (
                    <Notice
                        tone="danger"
                        message={short.drop
                            ? t('setup.summary.shortDrop', { ...shortArgs, name: short.drop.name, saves: size(short.drop.size_gb ?? 0) })
                            : t('setup.summary.short', shortArgs)}
                    />
                ) : (
                    <Text role="caption" tone="muted">{spoken}</Text>
                )}
            </Stack>
        </Card>
    );
}

/** The model checklist: GAIA's models, the chat models already on the PC, and the default chat model. */
export function ModelChecklist({ plan, selection, onToggle, onDefaultChat, disabled, progress }: {
    plan: SetupPlan;
    selection: Selection;
    onToggle: (id: string, on: boolean) => void;
    onDefaultChat: (id: string) => void;
    disabled?: boolean;
    progress?: Record<string, SetupModelProgress>;
}) {
    const t = useT();
    const catalog = catalogOf(plan);
    const [moreOpen, setMoreOpen] = useState(false);
    const [installedOpen, setInstalledOpen] = useState(false);
    const [query, setQuery] = useState('');
    const main = mainRows(catalog);
    const more = moreRows(catalog);
    const installed = installedOnlyRows(catalog);
    const q = query.trim().toLowerCase();
    const shownInstalled = q ? installed.filter((e) => e.id.toLowerCase().includes(q) || e.name.toLowerCase().includes(q)) : installed;
    const candidates = chatCandidates(catalog, selection.ticked);

    const row = (e: SetupCatalogEntry) => (
        <Row1
            key={e.id}
            entry={e}
            ticked={selection.ticked.has(e.id)}
            onToggle={(on) => onToggle(e.id, on)}
            disabled={disabled}
            progress={progress?.[e.id]}
            imagesCovered={catalog.some((o) => o.id !== e.id && isVision(o) && (selection.ticked.has(o.id) || o.downloaded === true))}
        />
    );

    return (
        <Stack gap="md">
            <Stack gap="md">{main.map(row)}</Stack>
            <Text role="caption" tone="muted">{t('setup.models.neverDeletes')}</Text>

            {more.length > 0 ? (
                <Disclosure title={t('setup.models.more', { n: String(more.length) })} open={moreOpen} onToggle={() => setMoreOpen(!moreOpen)}>
                    <Stack gap="md">{more.map(row)}</Stack>
                </Disclosure>
            ) : null}

            {installed.length > 0 ? (
                <Disclosure title={t('setup.models.installed', { n: String(installed.length) })} open={installedOpen} onToggle={() => setInstalledOpen(!installedOpen)}>
                    <Stack gap="md">
                        {installed.length > SEARCH_ABOVE ? <SearchField value={query} onChangeText={setQuery} placeholder={t.bare('setup.models.search')} accessibilityLabel={t('setup.models.search')} /> : null}
                        {shownInstalled.map((e) => (
                            <MarkedRow
                                key={e.id}
                                kind="radio"
                                checked={selection.defaultChat === e.id}
                                disabled={disabled}
                                label={`${e.name}. ${t('setup.models.useAsDefault')}`}
                                onPress={() => onDefaultChat(e.id)}
                                name={<Text role="ui" weight="medium" dir="ltr">{e.name}</Text>}
                                chips={<><Badge label={t('setup.models.purpose.chat')} />{isNpu(e) ? <Badge label={t('setup.models.npu')} /> : null}</>}
                            >
                                <Text role="caption" tone="secondary">{`${t('setup.models.status.onPc')} · ${t('setup.models.useAsDefault')}`}</Text>
                            </MarkedRow>
                        ))}
                    </Stack>
                </Disclosure>
            ) : null}

            <Select
                label={t('setup.models.defaultChat')}
                value={selection.defaultChat ?? ''}
                disabled={disabled || candidates.length === 0}
                placeholder={t.bare('setup.models.noChat')}
                options={candidates.map((e) => ({ value: e.id, label: `${e.name}${isNpu(e) ? ` (${t('setup.models.npu')})` : ''}` }))}
                onChange={onDefaultChat}
            />
        </Stack>
    );
}
