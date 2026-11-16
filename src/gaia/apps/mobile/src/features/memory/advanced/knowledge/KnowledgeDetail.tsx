// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { UseMutationResult } from '@tanstack/react-query';
import * as Clipboard from 'expo-clipboard';
import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';
import { confidenceLevel, formatDateFull, isOverdue, type KnowledgeEntry } from '@webui/utils/memoryFormat.core';

import type { KnowledgeSaveResult, KnowledgeUpdateBody } from '@/api/client.types';
import { dateLocale, useLocaleCode, useT } from '@/i18n/useT';
import { Badge, Button, Divider, Ltr, Notice, Row, Stack, Text } from '@/ui';
import { Copy, Pencil, Shield, ShieldOff, Trash2 } from '@/ui/icons';

import { CATEGORY_TONE, categoryLabel, failureText, type KnowledgeFeedback } from './knowledgeUi';

/** The browser owns the mutations: a sheet draws outside the screen's providers in the dev gallery, so its content can't reach the client itself. */
interface KnowledgeDetailProps {
    item: KnowledgeEntry;
    edit: UseMutationResult<KnowledgeSaveResult, Error, { id: string; body: KnowledgeUpdateBody }>;
    forget: UseMutationResult<unknown, Error, string>;
    onEdit: (item: KnowledgeEntry) => void;
    /** The sensitive flag changed; the sheet shows the new row. */
    onChanged: (item: KnowledgeEntry) => void;
    onFilterEntity: (entity: string) => void;
    /** The memory was forgotten, or the person closed the sheet. */
    onClose: () => void;
}

function Field({ label, children }: { label: string; children: string }) {
    return (
        <Stack gap="xs">
            <Text role="caption" tone="muted">
                {label}
            </Text>
            <Text dir="auto" selectable>
                {children}
            </Text>
        </Stack>
    );
}

/** A memory in full, with Edit, the sensitive flag, Copy ID and Delete; delete asks once more, as the web does. */
export function KnowledgeDetail({ item, edit, forget, onEdit, onChanged, onFilterEntity, onClose }: KnowledgeDetailProps) {
    const t = useT();
    const code = useLocaleCode();
    const [confirming, setConfirming] = useState(false);
    const [feedback, setFeedback] = useState<KnowledgeFeedback>(null);
    const locale = dateLocale(t);
    const when = (iso: string | null | undefined) => formatDateFull(iso ?? null, locale) || '—';
    const level = confidenceLevel(item.confidence ?? 0);

    const toggleSensitive = () => {
        setFeedback(null);
        edit.mutate(
            { id: item.id, body: { sensitive: !item.sensitive } },
            {
                onSuccess: () => {
                    onChanged({ ...item, sensitive: !item.sensitive });
                    setFeedback({ tone: 'info', text: t(item.sensitive ? 'mem.toast.unmarkedSensitive' : 'mem.toast.markedSensitive') });
                },
                onError: (error) => setFeedback({ tone: 'danger', text: failureText(code, 'mem.toast.sensitiveFailed', error) }),
            },
        );
    };

    const copyId = () => {
        setFeedback(null);
        Clipboard.setStringAsync(item.id).then(
            () => setFeedback({ tone: 'info', text: t('mem.toast.idCopied') }),
            (error: unknown) => setFeedback({ tone: 'danger', text: failureText(code, 'mem.toast.copyFailed', error) }),
        );
    };

    const remove = () => {
        setFeedback(null);
        forget.mutate(item.id, {
            onSuccess: onClose,
            onError: (error) => {
                setConfirming(false);
                setFeedback({ tone: 'danger', text: failureText(code, 'mem.toast.deleteFailed', error) });
            },
        });
    };

    return (
        <Stack gap="lg" testID="memory-knowledge-detail">
            <Row gap="sm" wrap>
                <Badge label={categoryLabel(item.category, item.source, t, true)} tone={CATEGORY_TONE[item.category] ?? 'neutral'} accessibilityLabel={categoryLabel(item.category, item.source, t, false)} />
                {item.sensitive ? <Badge label={t.bare('mem.form.sensitive')} tone="warning" accessibilityLabel={t('mem.form.sensitive')} /> : null}
                {item.due_at ? <Badge label={when(item.due_at)} tone={isOverdue(item.due_at) ? 'danger' : 'warning'} /> : null}
            </Row>
            <Stack gap="xs">
                <Text role="caption" tone="muted">
                    {t('mem.detail.fullContent')}
                </Text>
                <Text dir="auto" selectable testID="memory-knowledge-detail-content">
                    {item.content}
                </Text>
            </Stack>

            {feedback ? <Notice tone={feedback.tone} message={feedback.text} testID="memory-knowledge-feedback" /> : null}

            {confirming ? (
                <Stack gap="md" testID="memory-knowledge-confirm">
                    <Text weight="semibold" tone="danger" accessibilityRole="header">
                        {t('mem.row.confirmDeleteAria')}
                    </Text>
                    <Row gap="sm" wrap>
                        <Button
                            label={t.bare('mem.row.delete')}
                            accessibilityLabel={t('mem.row.deleteAria')}
                            icon={Trash2}
                            variant="danger"
                            busy={forget.isPending}
                            onPress={remove}
                            testID="memory-knowledge-confirm-delete"
                        />
                        <Button label={t.bare('mem.cancel')} accessibilityLabel={t('mem.cancel')} variant="secondary" disabled={forget.isPending} onPress={() => setConfirming(false)} />
                    </Row>
                </Stack>
            ) : (
                <Row gap="sm" wrap>
                    <Button label={t.bare('mem.row.edit')} accessibilityLabel={t('mem.row.edit')} icon={Pencil} variant="secondary" onPress={() => onEdit(item)} testID="memory-knowledge-edit" />
                    <Button
                        label={t.bare(item.sensitive ? 'mem.row.unmarkSensitive' : 'mem.row.markSensitive')}
                        accessibilityLabel={t(item.sensitive ? 'mem.row.unmarkSensitive' : 'mem.row.markSensitive')}
                        icon={item.sensitive ? ShieldOff : Shield}
                        variant="secondary"
                        busy={edit.isPending}
                        onPress={toggleSensitive}
                        testID="memory-knowledge-sensitive"
                    />
                    <Button label={t.bare('mem.row.copyId')} accessibilityLabel={t('mem.row.copyId')} icon={Copy} variant="secondary" onPress={copyId} />
                    <Button
                        label={t.bare('mem.row.delete')}
                        accessibilityLabel={t('mem.row.deleteAria')}
                        icon={Trash2}
                        variant="ghost"
                        onPress={() => setConfirming(true)}
                        testID="memory-knowledge-delete"
                    />
                </Row>
            )}

            <Divider />
            <Stack gap="md">
                <Field label={t('mem.col.context')}>{item.context}</Field>
                {item.entity ? (
                    <Stack gap="xs" align="start">
                        <Field label={t('mem.col.entity')}>{item.entity}</Field>
                        <Button
                            label={t.bare('mem.knowledge.filterBy', { entity: isolate(item.entity, 'ltr') })}
                            accessibilityLabel={t('mem.knowledge.filterBy', { entity: isolate(item.entity, 'ltr') })}
                            variant="ghost"
                            size="sm"
                            onPress={() => onFilterEntity(item.entity!)}
                            testID="memory-knowledge-filter-entity"
                        />
                    </Stack>
                ) : null}
                <Field label={t('mem.detail.domain')}>{item.domain || '—'}</Field>
                <Field label={t('mem.col.source')}>{item.source || '—'}</Field>
                <Stack gap="xs">
                    <Text role="caption" tone="muted">
                        {t('mem.col.confidence')}
                    </Text>
                    <Row gap="sm">
                        <Text tone={level === 'low' ? 'danger' : level === 'medium' ? 'warning' : 'success'}>{(item.confidence ?? 0).toFixed(2)}</Text>
                    </Row>
                </Stack>
                <Field label={t('mem.detail.useCount')}>{String(item.use_count ?? 0)}</Field>
                <Field label={t('mem.col.created')}>{when(item.created_at)}</Field>
                <Field label={t('mem.col.updated')}>{when(item.updated_at)}</Field>
                <Field label={t('mem.detail.lastUsed')}>{when(item.last_used)}</Field>
                <Field label={t('mem.detail.remindedAt')}>{when(item.reminded_at)}</Field>
                {item.superseded_by ? <Field label={t('mem.detail.supersededBy')}>{item.superseded_by}</Field> : null}
                {item.metadata ? (
                    <Stack gap="xs">
                        <Text role="caption" tone="muted">
                            {t('mem.detail.metadata')}
                        </Text>
                        <Ltr>{typeof item.metadata === 'object' ? JSON.stringify(item.metadata, null, 2) : item.metadata}</Ltr>
                    </Stack>
                ) : null}
                <Stack gap="xs">
                    <Text role="caption" tone="muted">
                        {t('mem.detail.id')}
                    </Text>
                    <Ltr>{item.id}</Ltr>
                </Stack>
            </Stack>
        </Stack>
    );
}
