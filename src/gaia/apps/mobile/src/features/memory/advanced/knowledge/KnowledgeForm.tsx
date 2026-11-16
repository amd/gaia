// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { UseMutationResult } from '@tanstack/react-query';
import { useState } from 'react';

import { dueInputValue, KNOWLEDGE_CATEGORIES, type KnowledgeEntry } from '@webui/utils/memoryFormat.core';

import type { KnowledgeCreateBody, KnowledgeSaveResult, KnowledgeUpdateBody } from '@/api/client.types';
import { useLocaleCode, useT } from '@/i18n/useT';
import { Button, Chip, Notice, Row, Stack, Switch, Text, TextField } from '@/ui';

import { parseWhen, whenText } from '../../about/whenInput';
import type { MemoryPaneProps } from '../../tabs';
import { categoryLabel, failureText, type KnowledgeFeedback } from './knowledgeUi';

interface FormValues {
    content: string;
    category: string;
    context: string;
    domain: string;
    entity: string;
    when: string;
    sensitive: boolean;
}

function startValues(item?: KnowledgeEntry): FormValues {
    return {
        content: item?.content ?? '',
        category: item?.category ?? 'fact',
        context: item?.context ?? 'global',
        domain: item?.domain ?? '',
        entity: item?.entity ?? '',
        when: whenText(dueInputValue(item?.due_at)),
        sensitive: item?.sensitive ?? false,
    };
}

interface KnowledgeFormProps extends Pick<MemoryPaneProps, 'notify'> {
    add: UseMutationResult<KnowledgeSaveResult, Error, KnowledgeCreateBody>;
    edit: UseMutationResult<KnowledgeSaveResult, Error, { id: string; body: KnowledgeUpdateBody }>;
    /** The memory to edit; absent adds a new one. */
    item?: KnowledgeEntry;
    /** Saved: the sheet closes. */
    onSaved: () => void;
    onCancel: () => void;
}

/** The add and edit form, with the web's fields: content, category, context, domain, entity, due at and sensitive. */
export function KnowledgeForm({ item, add, edit, notify, onSaved, onCancel }: KnowledgeFormProps) {
    const t = useT();
    const code = useLocaleCode();
    const [values, setValues] = useState(() => startValues(item));
    const [feedback, setFeedback] = useState<KnowledgeFeedback>(null);
    const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => setValues((prev) => ({ ...prev, [key]: value }));

    const due = values.when.trim() ? parseWhen(values.when) : null;
    const whenWrong = values.when.trim() !== '' && due === null;
    const ready = values.content.trim() !== '' && !whenWrong;
    const body: KnowledgeCreateBody | null = ready
        ? {
              content: values.content.trim(),
              category: values.category,
              context: values.context.trim() || 'global',
              ...(values.domain.trim() ? { domain: values.domain.trim() } : {}),
              ...(values.entity.trim() ? { entity: values.entity.trim() } : {}),
              sensitive: values.sensitive,
              ...(due ? { due_at: due } : {}),
          }
        : null;
    const saving = add.isPending || edit.isPending;

    const done = (result: KnowledgeSaveResult | undefined) => {
        notify(
            result?.embedded === false
                ? { tone: 'danger', text: t('mem.toast.notEmbedded') }
                : { tone: 'success', text: t(item ? 'mem.toast.updated' : 'mem.toast.created') },
        );
        onSaved();
    };

    const save = () => {
        if (!body || saving) return;
        setFeedback(null);
        const failed = (key: 'mem.toast.createFailed' | 'mem.toast.updateFailed') => (error: Error) =>
            setFeedback({ tone: 'danger', text: failureText(code, key, error) });
        if (item) edit.mutate({ id: item.id, body }, { onSuccess: done, onError: failed('mem.toast.updateFailed') });
        else add.mutate(body, { onSuccess: done, onError: failed('mem.toast.createFailed') });
    };

    return (
        <Stack gap="lg" testID="memory-knowledge-form">
            {feedback ? <Notice tone={feedback.tone} message={feedback.text} testID="memory-knowledge-form-error" /> : null}
            <TextField
                value={values.content}
                onChangeText={(text) => set('content', text)}
                label={t('mem.col.content')}
                placeholder={t.bare('mem.form.contentPlaceholder')}
                multiline
                maxLines={6}
                testID="memory-knowledge-form-content"
            />
            <Stack gap="sm">
                <Text role="label" weight="medium" tone="secondary">
                    {t('mem.col.category')}
                </Text>
                <Row gap="sm" wrap>
                    {KNOWLEDGE_CATEGORIES.map((category) => (
                        <Chip
                            key={category}
                            label={categoryLabel(category, undefined, t, true)}
                            accessibilityLabel={categoryLabel(category, undefined, t, false)}
                            selected={values.category === category}
                            onPress={() => set('category', category)}
                            testID={`memory-knowledge-form-category-${category}`}
                        />
                    ))}
                </Row>
            </Stack>
            <TextField value={values.context} onChangeText={(text) => set('context', text)} label={t('mem.col.context')} placeholder="global" />
            <TextField value={values.domain} onChangeText={(text) => set('domain', text)} label={t('mem.detail.domain')} placeholder={t.bare('mem.form.domainPlaceholder')} />
            <TextField value={values.entity} onChangeText={(text) => set('entity', text)} label={t('mem.col.entity')} placeholder={t.bare('mem.form.entityPlaceholder')} />
            <TextField
                value={values.when}
                onChangeText={(text) => set('when', text)}
                label={t('mem.form.dueAt')}
                placeholder="2026-11-20 09:00"
                hint={t('mobileApp.mem.whenHint')}
                error={whenWrong ? t('mobileApp.mem.whenHint') : undefined}
                testID="memory-knowledge-form-when"
            />
            <Switch value={values.sensitive} onValueChange={(on) => set('sensitive', on)} label={t('mem.form.sensitive')} description={t('mem.form.sensitiveHint')} testID="memory-knowledge-form-sensitive" />
            <Row gap="sm" wrap>
                <Button
                    label={t.bare(item ? 'mem.form.update' : 'mem.form.save')}
                    accessibilityLabel={t(item ? 'mem.form.update' : 'mem.form.save')}
                    variant="primary"
                    disabled={!body}
                    busy={saving}
                    onPress={save}
                    testID="memory-knowledge-form-save"
                />
                <Button label={t.bare('mem.cancel')} accessibilityLabel={t('mem.cancel')} variant="secondary" disabled={saving} onPress={onCancel} />
            </Row>
        </Stack>
    );
}
