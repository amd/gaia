// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import type { OutputItem, OutputKind } from '@/api/client.types';
import { useSessionOutputs } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { openPcFile } from '@/features/files/openPcFile';
import { dateLocale, describeFailureWith, useT } from '@/i18n/useT';
import { Button, Icon, Notice, Pressable, Row, Stack, Text } from '@/ui';
import { Brain, CalendarClock, CornerDownRight, FileOutput, FileText, Image, Library } from '@/ui/icons';
import { isolate, ref, type MessageKey } from '@webui/i18n/registry';
import type { LucideIcon } from 'lucide-react-native';

import { jumpToMessage } from '../messages/jumpToMessage';
import { PanelSection } from './PanelSection';
import { useSessionPanelStore } from './panelStore';
import { whenOf } from './outputTime';
import type { SectionProps } from './sections';

/** Rows shown before "Show more". */
export const OUTPUTS_SHOWN = 5;

const KIND: Record<OutputKind, { label: MessageKey; icon: LucideIcon }> = {
    file: { label: 'out.kind.file', icon: FileText },
    export: { label: 'out.kind.export', icon: FileOutput },
    image: { label: 'out.kind.image', icon: Image },
    document: { label: 'out.kind.document', icon: Library },
    memory: { label: 'out.kind.memory', icon: Brain },
    schedule: { label: 'out.kind.schedule', icon: CalendarClock },
};

const CHANGE_LABEL = { created: 'out.change.created', edited: 'out.change.edited', deleted: 'out.change.deleted' } as const;

// Only files can be read from the phone.
const PREVIEWABLE: ReadonlySet<OutputKind> = new Set(['file', 'export', 'image']);

export function OutputsSection({ sessionId, live, close }: SectionProps) {
    const t = useT();
    const open = useSessionPanelStore((s) => s.sections.outputs);
    const query = useSessionOutputs(sessionId, { enabled: open, live });
    return (
        <PanelSection id="outputs" title={t('out.title')}>
            <OutputList sessionId={sessionId} items={query.data?.items} error={query.error} close={close} />
        </PanelSection>
    );
}

function OutputList({ sessionId, items, error, close }: { sessionId: string; items: OutputItem[] | undefined; error: unknown; close?: () => void }) {
    const t = useT();
    const [all, setAll] = useState(false);
    const [now] = useState(Date.now);
    const [notLoaded, setNotLoaded] = useState(false);
    if (isPcRefusal(error)) return <PcOnlyNotice testID="outputs-pc-only" />;
    if (error) return <Notice tone="danger" message={t.compose((say) => say('out.listError', { error: describeFailureWith(error, say) }))} />;
    if (!items) return <Text tone="muted">{t('sp.checking')}</Text>;
    if (items.length === 0) return <Text tone="secondary">{t('out.empty')}</Text>;
    const shown = all ? items : items.slice(0, OUTPUTS_SHOWN);
    const goToTurn = (messageId: number) => {
        // The panel covers the transcript; close it first so the jump lands in view.
        const found = jumpToMessage(messageId);
        if (found) close?.();
        setNotLoaded(!found);
    };
    return (
        <Stack gap="md">
            <Stack gap="md" accessibilityRole="list" accessibilityLabel={t('out.listLabel')}>
                {shown.map((item) => (
                    <OutputRow key={item.id} item={item} sessionId={sessionId} now={now} onGoToTurn={goToTurn} />
                ))}
            </Stack>
            {items.length > OUTPUTS_SHOWN ? (
                <Button
                    variant="ghost"
                    size="sm"
                    label={all ? t('out.fewer') : t('out.more', { n: items.length - OUTPUTS_SHOWN })}
                    onPress={() => setAll((v) => !v)}
                />
            ) : null}
            {notLoaded ? <Notice tone="warning" message={t('out.notLoaded')} onDismiss={() => setNotLoaded(false)} /> : null}
        </Stack>
    );
}

function OutputRow({ item, sessionId, now, onGoToTurn }: { item: OutputItem; sessionId: string; now: number; onGoToTurn: (messageId: number) => void }) {
    const t = useT();
    const { label, icon } = KIND[item.kind];
    const state: MessageKey | null = item.state === 'undone' ? 'out.undone' : item.state === 'gone' ? 'sp.gone' : null;
    const changed = item.change && item.kind !== 'document' ? CHANGE_LABEL[item.change] : undefined;
    const when = whenOf(item.created_at, now, dateLocale(t));
    // One line per language, bracketed once: kind, time, state.
    const meta = t.compose((say) =>
        [
            changed ? say('out.kindChanged', { kind: ref(label), change: ref(changed) }) : say(label),
            when == null ? '' : typeof when === 'string' ? when : say(when.key, when.params),
            state ? say(state) : '',
        ]
            .filter(Boolean)
            .join(' · '),
    );
    const canPreview = !!item.path && item.state === 'present' && PREVIEWABLE.has(item.kind);
    return (
        <Row gap="sm" align="start">
            <Icon icon={icon} size="sm" tone="muted" />
            <Stack flex={1} gap="xs">
                <Text tone={item.state === 'present' ? 'primary' : 'muted'} numberOfLines={2}>
                    {isolate(item.name, 'ltr')}
                </Text>
                <Text role="caption" tone="muted">
                    {meta}
                </Text>
                <Row gap="lg" wrap>
                    {item.message_id != null ? (
                        <Pressable accessibilityLabel={t('out.goToAria', { name: item.name })} onPress={() => onGoToTurn(item.message_id as number)}>
                            <Row gap="xs">
                                <Icon icon={CornerDownRight} size="sm" tone="accent" />
                                <Text role="caption" tone="accent" weight="medium">
                                    {t('sp.goToTurn')}
                                </Text>
                            </Row>
                        </Pressable>
                    ) : null}
                    {canPreview ? (
                        <Pressable
                            accessibilityLabel={`${t('mobileApp.rail.preview')}: ${item.name}`}
                            onPress={() => openPcFile(item.path as string, sessionId)}
                        >
                            <Text role="caption" tone="accent" weight="medium">
                                {t('mobileApp.rail.preview')}
                            </Text>
                        </Pressable>
                    ) : null}
                </Row>
            </Stack>
        </Row>
    );
}
