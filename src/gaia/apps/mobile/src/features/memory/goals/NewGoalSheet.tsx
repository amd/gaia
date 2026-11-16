// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ref } from '@webui/i18n/registry';

import type { GoalPriority } from '@/api/client.types';
import { useCreateGoal } from '@/data';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Button, Notice, Row, SegmentedControl, Sheet, Stack, TextField } from '@/ui';

import { PRIORITY_CHOICES, PRIORITY_KEYS } from './goalText';

export interface NewGoalSheetProps {
    open: boolean;
    onClose: () => void;
}

/** The web's inline form as a sheet: a failed create is said on the form, and the form stays as typed. */
export function NewGoalSheet({ open, onClose }: NewGoalSheetProps) {
    const t = useT();
    const create = useCreateGoal();
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [priority, setPriority] = useState<GoalPriority>('medium');
    const canCreate = title.trim() !== '' && !create.isPending;

    const close = () => {
        create.reset();
        onClose();
    };

    const submit = () => {
        if (!canCreate) return;
        create.mutate(
            { title: title.trim(), description: description.trim(), priority },
            {
                onSuccess: () => {
                    setTitle('');
                    setDescription('');
                    setPriority('medium');
                    close();
                },
            },
        );
    };

    return (
        <Sheet
            open={open}
            onClose={close}
            title={t('mem.goals.new')}
            footer={
                <Row gap="sm" justify="end">
                    <Button label={t.bare('mem.cancel')} accessibilityLabel={t('mem.cancel')} variant="ghost" onPress={close} />
                    <Button
                        label={t.bare('mem.goals.create')}
                        accessibilityLabel={t('mem.goals.create')}
                        variant="primary"
                        busy={create.isPending}
                        disabled={!canCreate}
                        onPress={submit}
                        testID="goals-create-submit"
                    />
                </Row>
            }
        >
            <Stack gap="md">
                <TextField
                    value={title}
                    onChangeText={setTitle}
                    placeholder={t.bare('mem.goals.titlePlaceholder')}
                    accessibilityLabel={t('mem.goals.titlePlaceholder')}
                    autoFocus
                    testID="goals-create-title"
                />
                <TextField
                    value={description}
                    onChangeText={setDescription}
                    placeholder={t.bare('mem.goals.descPlaceholder')}
                    accessibilityLabel={t('mem.goals.descPlaceholder')}
                    multiline
                    maxLines={5}
                    testID="goals-create-description"
                />
                <SegmentedControl
                    value={priority}
                    onChange={(value) => setPriority(value as GoalPriority)}
                    accessibilityLabel={t(PRIORITY_CHOICES.find((choice) => choice.value === priority)!.label)}
                    options={PRIORITY_CHOICES.map((choice) => ({
                        value: choice.value,
                        label: t.bare(PRIORITY_KEYS[choice.value]),
                        accessibilityLabel: t(choice.label),
                    }))}
                    testID="goals-create-priority"
                />
                {create.isError ? (
                    <Notice tone="danger" message={t.compose((say) => say('mem.goals.failed', { what: ref('mem.goals.createFailed'), error: describeFailureWith(create.error, say) }))} />
                ) : null}
            </Stack>
        </Sheet>
    );
}
