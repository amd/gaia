// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import type { Goal } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Badge, Button, Divider, Icon, Pressable, Row, StatusDot, Stack, Surface, Text } from '@/ui';
import { ChevronDown, ChevronRight, ListChecks, Trash2 } from '@/ui/icons';

import { PRIORITY_KEYS, PRIORITY_TONES, SOURCE_KEYS, STATUS_KEYS, statusTone } from './goalText';

interface GoalCardProps {
    goal: Goal;
    busy: boolean;
    onCancel: (goal: Goal) => void;
    onDelete: (goal: Goal) => void;
}

/** One goal: status, priority, source and task count in the header; its notes, tasks, Cancel and Delete open under it. */
export function GoalCard({ goal, busy, onCancel, onDelete }: GoalCardProps) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const done = goal.tasks.filter((task) => task.status === 'completed').length;

    return (
        <Surface tone="card" radius="lg" border testID={`goal-${goal.id}`}>
            <Pressable
                onPress={() => setOpen((was) => !was)}
                accessibilityLabel={`${goal.title}. ${t(STATUS_KEYS[goal.status] ?? STATUS_KEYS.queued!)}, ${t(PRIORITY_KEYS[goal.priority])}`}
                accessibilityState={{ expanded: open }}
                testID={`goal-toggle-${goal.id}`}
            >
                <Stack gap="sm" padding="md">
                    <Row gap="sm">
                        <Stack flex={1}>
                            <Text weight="semibold" dir="auto">
                                {goal.title}
                            </Text>
                        </Stack>
                        <Icon icon={open ? ChevronDown : ChevronRight} size="sm" tone="muted" />
                    </Row>
                    <Row gap="sm" wrap>
                        <StatusDot tone={statusTone(goal.status)} label={t(STATUS_KEYS[goal.status] ?? STATUS_KEYS.queued!)} showLabel />
                        <Badge label={t.bare(PRIORITY_KEYS[goal.priority])} accessibilityLabel={t(PRIORITY_KEYS[goal.priority])} tone={PRIORITY_TONES[goal.priority]} />
                        {goal.source !== 'user' && SOURCE_KEYS[goal.source] ? (
                            <Badge label={t.bare(SOURCE_KEYS[goal.source]!)} accessibilityLabel={t(SOURCE_KEYS[goal.source]!)} tone="accent" />
                        ) : null}
                        <Row gap="xs">
                            <Icon icon={ListChecks} size="sm" tone="muted" />
                            <Text role="caption" tone="muted" accessibilityLabel={t('mobileApp.mem.goalTasksDone', { done, total: goal.tasks.length })}>
                                {`⁦${done}/${goal.tasks.length}⁩`}
                            </Text>
                        </Row>
                    </Row>
                </Stack>
            </Pressable>
            {open ? (
                <>
                    <Divider />
                    <Stack gap="md" padding="md" testID={`goal-body-${goal.id}`}>
                        {goal.description ? (
                            <Text tone="secondary" dir="auto">
                                {goal.description}
                            </Text>
                        ) : null}
                        {goal.progress_notes ? (
                            <Text role="caption" tone="muted" dir="auto">
                                {goal.progress_notes}
                            </Text>
                        ) : null}
                        {goal.tasks.length > 0 ? (
                            <Stack gap="sm">
                                {goal.tasks.map((task) => (
                                    <Stack key={task.id} gap="xs">
                                        <Row gap="sm" align="start">
                                            <StatusDot tone={statusTone(task.status)} label={t(STATUS_KEYS[task.status]!)} />
                                            <Stack flex={1}>
                                                <Text dir="auto">{task.description}</Text>
                                            </Stack>
                                        </Row>
                                        <Text role="caption" tone="muted">
                                            {t.bare(STATUS_KEYS[task.status]!)}
                                        </Text>
                                        {task.result ? (
                                            <Text role="caption" tone="secondary" dir="auto">
                                                {task.result}
                                            </Text>
                                        ) : null}
                                    </Stack>
                                ))}
                            </Stack>
                        ) : goal.status === 'queued' || goal.status === 'in_progress' ? (
                            <Text role="caption" tone="muted">
                                {t('mem.goals.noTasks')}
                            </Text>
                        ) : null}
                        <Row gap="sm" wrap>
                            {goal.status === 'queued' ? (
                                <Button
                                    label={t.bare('mem.cancel')}
                                    accessibilityLabel={`${t('mem.cancel')}: ${goal.title}`}
                                    variant="secondary"
                                    disabled={busy}
                                    onPress={() => onCancel(goal)}
                                    testID={`goal-cancel-${goal.id}`}
                                />
                            ) : null}
                            {confirmDelete ? (
                                <Button
                                    label={t.bare('mem.row.deleteConfirm')}
                                    accessibilityLabel={`${t('mem.row.deleteConfirm')} ${goal.title}`}
                                    icon={Trash2}
                                    variant="danger"
                                    busy={busy}
                                    onPress={() => {
                                        setConfirmDelete(false);
                                        onDelete(goal);
                                    }}
                                    testID={`goal-delete-confirm-${goal.id}`}
                                />
                            ) : (
                                <Button
                                    label={t.bare('mem.row.delete')}
                                    accessibilityLabel={t('mem.goals.deleteAria', { title: goal.title })}
                                    icon={Trash2}
                                    variant="ghost"
                                    disabled={busy}
                                    onPress={() => setConfirmDelete(true)}
                                    testID={`goal-delete-${goal.id}`}
                                />
                            )}
                        </Row>
                    </Stack>
                </>
            ) : null}
        </Surface>
    );
}
