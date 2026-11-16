// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';
import { customModelNote } from '@webui/utils/modelServer.core';

import type { Settings } from '@/api/client.types';
import { useUpdateSettings } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Notice, Stack, Text, TextField } from '@/ui';

import { ActionError } from './errors';

/** The saved ID is read once from the PC; the field edits a copy until Save. */
export function CustomModel({ settings }: { settings: Settings }) {
    const t = useT();
    const save = useUpdateSettings();
    const saved = settings.custom_model ?? '';
    const [value, setValue] = useState(saved);
    const dirty = value.trim() !== saved.trim();
    const note = customModelNote(saved, settings.model_status);
    const justSaved = save.isSuccess && !dirty;

    return (
        <Stack gap="sm">
            <Text role="caption" tone="muted">{t('settings.advanced.customDesc')}</Text>
            <TextField
                value={value}
                onChangeText={(text) => {
                    setValue(text);
                    save.reset();
                }}
                placeholder={t.bare('settings.advanced.customPlaceholder')}
                accessibilityLabel={t('settings.advanced.customAria')}
                disabled={save.isPending}
                returnKeyType="done"
                onSubmitEditing={() => {
                    if (dirty && !save.isPending) save.mutate({ custom_model: value.trim() });
                }}
                testID="advanced-custom-model"
            />
            <Button
                label={save.isPending ? t('settings.models.applying') : justSaved ? t('settings.advanced.saved') : t('settings.advanced.save')}
                accessibilityLabel={t('settings.advanced.customSave')}
                variant="primary"
                full
                busy={save.isPending}
                disabled={save.isPending || (!dirty && !justSaved)}
                onPress={() => save.mutate({ custom_model: value.trim() })}
                testID="advanced-custom-save"
            />
            <Text role="caption" tone="muted">{t('settings.advanced.customIds')}</Text>
            {save.isError ? <ActionError error={save.error} title={t('settings.advanced.saveFailed')} /> : null}
            {note ? (
                <Notice
                    tone="warning"
                    message={note.kind === 'unlisted' ? t('settings.advanced.customNotListed', { id: isolate(note.id, 'ltr') }) : t('settings.advanced.customNotChecked')}
                />
            ) : null}
        </Stack>
    );
}
