// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';

import { formatSize, ltrIn } from '@webui/components/setup/format';
import {
    canCreateImages, catalogOf, isEmbedding, isNpu, isOptional, isVision, pickChat, pickVision, resolveVision, toggle,
    VISION_NONE, type Selection,
} from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupPlan } from '@webui/types';

import { useLocaleCode, useT } from '@/i18n/useT';
import { Badge, Card, Row, Stack, Text } from '@/ui';

import { MarkedRow, ModelRadio, SupportBadge, Warn } from './ModelRows';

function Job({ title, children }: { title: string; children: ReactNode }) {
    return (
        <Stack gap="sm">
            <Text role="ui" weight="semibold" accessibilityRole="header">{title}</Text>
            {children}
        </Stack>
    );
}

function Hint({ children }: { children: string }) {
    return <Text role="caption" tone="secondary">{children}</Text>;
}

/** A job's checkbox: a model or an extra, with its size and note. */
function CheckJob({ label, note, size, checked, disabled, warning, chips, onChange }: {
    label: string;
    note: string;
    size: string;
    checked: boolean;
    disabled?: boolean;
    warning?: string | null;
    chips?: ReactNode;
    onChange: (on: boolean) => void;
}) {
    return (
        <MarkedRow
            kind="checkbox"
            checked={checked}
            disabled={disabled}
            label={label}
            onPress={() => onChange(!checked)}
            name={<Text role="ui" weight="medium" dir="ltr">{label}</Text>}
            chips={chips}
        >
            <Text role="caption" tone="secondary" dir="ltr">{size}</Text>
            <Text role="caption" tone="secondary">{note}</Text>
            {warning ? <Warn message={warning} /> : null}
        </MarkedRow>
    );
}

/** Models by the job they do; picking the chat model updates the jobs that depend on it. */
export function JobModels({ plan, selection, onSelection, disabled }: {
    plan: SetupPlan;
    selection: Selection;
    onSelection: (selection: Selection) => void;
    disabled?: boolean;
}) {
    const t = useT();
    const locale = useLocaleCode();
    const catalog = catalogOf(plan);
    const [showVision, setShowVision] = useState(false);
    const chat = catalog.find((e) => e.id === selection.defaultChat);
    const onNpu = !!chat && isNpu(chat);
    const chatRows = catalog.filter((e) => e.chat_choice && !isEmbedding(e) && (e.gaia_model || e.downloaded) && isNpu(e) === onNpu);
    const verified = chatRows.filter((e) => e.support === 'verified');
    const untested = chatRows.filter((e) => e.support !== 'verified');
    const visionPick = resolveVision(catalog, selection);
    const builtIn = !!chat?.reads_images;
    const visionRows = catalog.filter((e) => isVision(e) && e.id !== chat?.id);
    const embedder = catalog.find(isEmbedding);
    const extra = (role: string) => catalog.find((e) => isOptional(e) && e.roles.includes(role as never));
    const sizeOf = (e: SetupCatalogEntry) =>
        e.downloaded ? t('setup.models.status.onPc')
            : e.size_gb != null ? ltrIn(locale, formatSize(e.size_gb))
                : t('setup.contents.sizeOnDownload');
    const set = (id: string, on: boolean) => onSelection(toggle(selection, catalog, id, on));
    const chooseChat = (id: string) => onSelection(pickChat(selection, catalog, id));
    const chooseVision = (id: string | null) => onSelection(pickVision(selection, catalog, id));
    const speech = extra('speech');
    const voice = extra('voice');
    const images = extra('image_creation');
    const imagesAllowed = canCreateImages(chat);
    const name = chat?.name ?? '';

    return (
        <Stack gap="lg">
            <Job title={t('setup.job.chat')}>
                <Hint>{t('setup.job.verifiedNote')}</Hint>
                {verified.map((e) => (
                    <ModelRadio key={e.id} entry={e} checked={e.id === selection.defaultChat} recommended={e.id === plan.recommended_model} disabled={disabled} onPick={() => chooseChat(e.id)} />
                ))}
                {untested.length > 0 ? (
                    <>
                        <Hint>{t('setup.job.untestedNote')}</Hint>
                        {untested.map((e) => (
                            <ModelRadio key={e.id} entry={e} checked={e.id === selection.defaultChat} recommended={false} disabled={disabled} onPick={() => chooseChat(e.id)} />
                        ))}
                    </>
                ) : null}
                {chat?.support === 'untested' ? <Warn message={t('setup.job.untestedPicked')} /> : null}
            </Job>

            <Job title={t('setup.job.vision')}>
                {onNpu ? (
                    <Hint>{t('setup.job.visionOffNpu', { name })}</Hint>
                ) : builtIn ? (
                    <Card>
                        <Stack gap="xs">
                            <Text dir="ltr">{t('setup.job.visionBuiltIn', { name })}</Text>
                            <Text role="caption" tone="success">{t('setup.job.nothingMore')}</Text>
                            <Text role="label" tone="accent" onPress={() => setShowVision((v) => !v)} accessibilityRole="button">
                                {showVision ? t('setup.job.hideVision') : t('setup.job.otherVision')}
                            </Text>
                        </Stack>
                    </Card>
                ) : (
                    <Hint>{t('setup.job.visionNeeded', { name })}</Hint>
                )}
                {!onNpu && (!builtIn || showVision) ? (
                    <Stack gap="sm">
                        {visionRows.map((e) => (
                            <ModelRadio key={e.id} entry={e} checked={visionPick === e.id} recommended={!builtIn && e.gaia_model && e.support === 'verified'} disabled={disabled} onPick={() => chooseVision(e.id)} />
                        ))}
                        <MarkedRow
                            kind="radio"
                            checked={builtIn ? visionPick === chat?.id : visionPick === null}
                            disabled={disabled}
                            label={builtIn ? t('setup.job.useChatForVision') : t('setup.job.skipVision')}
                            onPress={() => chooseVision(builtIn ? null : VISION_NONE)}
                            name={<Text role="ui" weight="medium">{builtIn ? t('setup.job.useChatForVision') : t('setup.job.skipVision')}</Text>}
                        >
                            {visionPick === null && !builtIn ? <Warn message={t('setup.job.skipVisionNote')} /> : null}
                        </MarkedRow>
                    </Stack>
                ) : null}
            </Job>

            {embedder ? (
                <Job title={t('setup.job.documents')}>
                    <CheckJob
                        label={embedder.name}
                        note={t('setup.models.line.documents')}
                        size={sizeOf(embedder)}
                        checked={selection.ticked.has(embedder.id)}
                        disabled={disabled}
                        warning={selection.ticked.has(embedder.id) ? null : t('setup.job.documentsOff')}
                        chips={<><Badge label={t('setup.models.recommended')} tone="accent" /><SupportBadge entry={embedder} /></>}
                        onChange={(on) => set(embedder.id, on)}
                    />
                </Job>
            ) : null}

            <Job title={t('setup.job.extras')}>
                <Hint>{t('setup.job.extrasNote')}</Hint>
                {speech ? <CheckJob label={t('setup.job.speech')} note={t('setup.job.speechNote')} size={sizeOf(speech)} checked={selection.ticked.has(speech.id)} disabled={disabled} onChange={(on) => set(speech.id, on)} /> : null}
                {voice ? <CheckJob label={t('setup.job.voice')} note={t('setup.job.voiceNote')} size={sizeOf(voice)} checked={selection.ticked.has(voice.id)} disabled={disabled} onChange={(on) => set(voice.id, on)} /> : null}
                {images ? (
                    <CheckJob
                        label={t('setup.job.images')}
                        note={t('setup.job.imagesNote')}
                        size={sizeOf(images)}
                        checked={selection.ticked.has(images.id)}
                        disabled={disabled || !imagesAllowed}
                        warning={imagesAllowed ? null : t('setup.job.imagesBlocked', { name })}
                        onChange={(on) => set(images.id, on)}
                    />
                ) : null}
            </Job>

            <WhatYouGet plan={plan} selection={selection} />
        </Stack>
    );
}

/** The result of every choice above, in plain words. */
export function WhatYouGet({ plan, selection }: { plan: SetupPlan; selection: Selection }) {
    const t = useT();
    const catalog = catalogOf(plan);
    const name = (id: string | null | undefined) => catalog.find((e) => e.id === id)?.name ?? id ?? t('setup.get.off');
    const ticked = (role: string) => catalog.find((e) => isOptional(e) && e.roles.includes(role as never) && selection.ticked.has(e.id));
    const embedder = catalog.find((e) => isEmbedding(e) && selection.ticked.has(e.id));
    const chat = catalog.find((e) => e.id === selection.defaultChat);
    const vision = resolveVision(catalog, selection);
    const voice = [ticked('speech') && t('setup.job.speech'), ticked('voice') && t('setup.job.voice')].filter(Boolean).join(', ');
    const rows: [string, string, boolean][] = [
        [t('setup.job.chat'), name(selection.defaultChat), true],
        [t('setup.job.vision'), chat && isNpu(chat) ? t('setup.get.offNpu') : vision ? name(vision) : t('setup.get.off'), true],
        [t('setup.job.documents'), embedder ? embedder.name : t('setup.get.off'), true],
        [t('setup.get.voice'), voice || t('setup.get.off'), false],
        [t('setup.job.images'), !canCreateImages(chat) ? t('setup.get.notAvailable') : ticked('image_creation') ? t('setup.get.on') : t('setup.get.off'), false],
    ];
    return (
        <Card accessibilityLabel={t('setup.get.title')}>
            <Stack gap="sm">
                <Text role="ui" weight="semibold" accessibilityRole="header">{t('setup.get.title')}</Text>
                {rows.map(([label, value, model]) => (
                    <Row key={label} gap="sm" justify="between" align="start">
                        <Text role="label" tone="secondary">{label}</Text>
                        <Stack flex={1} align="end">
                            <Text role="label" dir={model ? 'ltr' : undefined}>{value}</Text>
                        </Stack>
                    </Row>
                ))}
            </Stack>
        </Card>
    );
}
