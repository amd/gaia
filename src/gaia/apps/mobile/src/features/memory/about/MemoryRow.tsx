// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ref } from '@webui/i18n/registry';
import { dueInputValue, dueLabel, relativeDay, sourceKey, type AboutYouItem } from '@webui/utils/memoryFormat.core';

import { useEditMemory } from '@/data';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Button, Divider, IconButton, Row, Stack, Text, TextField } from '@/ui';
import { Eye, Pencil, Trash2 } from '@/ui/icons';

import type { MemoryPaneProps } from '../tabs';
import { parseWhen, whenText } from './whenInput';

interface MemoryRowProps extends Pick<MemoryPaneProps, 'notify'> {
    item: AboutYouItem;
    forgetting: boolean;
    onForget: (item: AboutYouItem) => void;
}

/** One memory: its text (hidden when sensitive until tapped), what it came from and when, and Edit and Forget. */
export function MemoryRow({ item, forgetting, onForget, notify }: MemoryRowProps) {
    const t = useT();
    const edit = useEditMemory();
    const [revealed, setRevealed] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('');
    const [draftWhen, setDraftWhen] = useState('');
    const hidden = item.sensitive && !revealed;
    const reminder = item.category === 'reminder';
    const due = item.due_at ? dueLabel(item.due_at, new Date(), t) : null;
    const when = relativeDay(item.updated_at, t);
    const source = ref(sourceKey(item.source));
    const meta = when === null ? t(sourceKey(item.source)) : t('mem.about.meta', { source, when });
    const name = hidden ? t.bare('mem.about.sensitiveMemory') : item.content;

    const open = () => {
        setDraft(item.content);
        setDraftWhen(whenText(dueInputValue(item.due_at)));
        setEditing(true);
    };

    const nextDue = reminder && draftWhen.trim() ? parseWhen(draftWhen) : null;
    const dueChanged = reminder && nextDue !== null && nextDue !== dueInputValue(item.due_at);
    const dueWrong = reminder && draftWhen.trim() !== '' && nextDue === null;

    const save = () => {
        const content = draft.trim();
        if (!content || dueWrong) return;
        if (content === item.content && !dueChanged) {
            setEditing(false);
            return;
        }
        edit.mutate(
            { id: item.id, body: { ...(content !== item.content ? { content } : {}), ...(dueChanged && nextDue ? { due_at: nextDue } : {}) } },
            {
                onSuccess: () => {
                    setEditing(false);
                    notify({ tone: 'success', text: t('mem.about.updated') });
                },
                onError: (error) => notify({ tone: 'danger', text: t.compose((say) => say('mem.about.editFailed', { error: describeFailureWith(error, say) })) }),
            },
        );
    };

    return (
        <Stack testID={`memory-about-row-${item.id}`}>
            <Stack gap="sm" paddingX="lg" paddingY="md">
                {editing ? (
                    <Stack gap="md">
                        <TextField
                            value={draft}
                            onChangeText={setDraft}
                            accessibilityLabel={t('mem.about.editLabel')}
                            multiline
                            maxLines={6}
                            autoFocus
                            testID={`memory-about-edit-${item.id}`}
                        />
                        {reminder ? (
                            <TextField
                                value={draftWhen}
                                onChangeText={setDraftWhen}
                                label={t('mem.about.when')}
                                hint={t('mobileApp.mem.whenHint')}
                                error={dueWrong ? t('mobileApp.mem.whenHint') : undefined}
                            />
                        ) : null}
                        <Row gap="sm" wrap>
                            <Button label={t.bare('mem.about.save')} accessibilityLabel={t('mem.about.save')} variant="primary" size="sm" busy={edit.isPending} disabled={!draft.trim() || dueWrong} onPress={save} testID={`memory-about-save-${item.id}`} />
                            <Button label={t.bare('mem.about.cancel')} accessibilityLabel={t('mem.about.cancel')} variant="secondary" size="sm" disabled={edit.isPending} onPress={() => setEditing(false)} />
                        </Row>
                    </Stack>
                ) : (
                    <>
                        {hidden ? (
                            <Row>
                                <Button
                                    label={t.bare('mem.about.showSensitive')}
                                    accessibilityLabel={`${t('mem.about.showSensitive')}: ${t('mem.about.sensitiveMemory')}`}
                                    icon={Eye}
                                    variant="secondary"
                                    size="sm"
                                    onPress={() => setRevealed(true)}
                                    testID={`memory-about-reveal-${item.id}`}
                                />
                            </Row>
                        ) : (
                            <Text dir="auto" selectable>
                                {item.content}
                            </Text>
                        )}
                        {due ? (
                            <Text role="caption" tone={due.past ? 'danger' : 'muted'}>
                                {due.text}
                            </Text>
                        ) : null}
                        <Text role="caption" tone="muted">
                            {meta}
                        </Text>
                        <Row gap="sm" wrap>
                            <IconButton
                                icon={Pencil}
                                size="sm"
                                label={t('mem.about.editAria', { name })}
                                disabled={hidden}
                                onPress={open}
                                testID={`memory-about-edit-open-${item.id}`}
                            />
                            <Button
                                label={t.bare('mem.about.forget')}
                                accessibilityLabel={t('mem.about.forgetAria', { name })}
                                icon={Trash2}
                                variant="ghost"
                                size="sm"
                                busy={forgetting}
                                onPress={() => onForget(item)}
                                testID={`memory-about-forget-${item.id}`}
                            />
                            {hidden ? (
                                <Text role="caption" tone="muted">
                                    {t.bare('mem.about.editHidden')}
                                </Text>
                            ) : null}
                        </Row>
                    </>
                )}
            </Stack>
            <Divider />
        </Stack>
    );
}
