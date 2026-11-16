// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo } from 'react';

import { isolate } from '@webui/i18n/registry';
import { PREVIEW_ID, PREVIEW_TEXT, VOICE_GROUPS, speedChoices } from '@webui/utils/readAloudSettings.core';

import { useSpeechSettings, useUpdateSpeechSettings } from '@/data';
import { ReadAloudErrorNotice, useReadAloud } from '@/features/audio/ReadAloud';
import { kindOf, useReadAloudStore } from '@/features/audio/readAloudStore';
import type { SpeechSettingsUpdate } from '@/api/client.types';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Button, Card, Notice, Row, Select, Spinner, Stack, Switch, Text } from '@/ui';
import { Square, Volume2 } from '@/ui/icons';

import { SettingsAnchor } from '../targets';

/** Settings › Read aloud: the voice and speed replies are read in, and whether every reply is read. */
export default function ReadAloud() {
    const t = useT();
    const settings = useSpeechSettings();
    const update = useUpdateSpeechSettings();
    const preview = useReadAloud(PREVIEW_ID, PREVIEW_TEXT);
    const saving = update.isPending;
    const data = settings.data;

    const voices = useMemo(
        () =>
            VOICE_GROUPS.flatMap((g) =>
                (data?.voices ?? [])
                    .filter((v) => v.accent === g.accent && v.gender === g.gender)
                    .map((v) => ({ value: v.id, label: v.name, description: t(`readAloud.group.${g.accent}.${g.gender}`) })),
            ),
        [data?.voices, t],
    );
    const speeds = useMemo(
        () =>
            data
                ? speedChoices(data.speed).map((s) => ({
                      value: String(s),
                      label: s === 100 ? t.bare('readAloud.settings.normalSpeed') : isolate(`${(s / 100).toLocaleString(t.locale)}×`, 'ltr'),
                  }))
                : [],
        [data, t],
    );

    const save = (change: SpeechSettingsUpdate) => {
        // A preview in the old voice is stale; a reply playing in a chat is not.
        if (useReadAloudStore.getState().messageId === PREVIEW_ID) useReadAloudStore.getState().stop();
        update.mutate(change);
    };

    if (settings.error) {
        const unsupported = kindOf(settings.error) === 'unsupported';
        return (
            <Stack gap="lg" padding="lg">
                <Notice
                    tone="danger"
                    message={
                        unsupported
                            ? t('mobileApp.readAloud.unsupported')
                            : t.compose((say) => say('readAloud.settings.loadFailed', { reason: describeFailureWith(settings.error, say) }))
                    }
                    testID="read-aloud-load-error"
                />
            </Stack>
        );
    }
    if (!data) {
        return (
            <Stack padding="lg">
                <Row gap="sm">
                    <Spinner size="sm" />
                    <Text role="body" tone="secondary">
                        {t('settings.loading')}
                    </Text>
                </Row>
            </Stack>
        );
    }

    return (
        <Stack gap="lg" padding="lg">
            <Text role="body" tone="secondary">
                {t('readAloud.settings.lede')}
            </Text>
            <SettingsAnchor name="voice">
                <Card>
                    <Stack gap="md">
                        <Select
                            label={t('readAloud.settings.voice')}
                            value={data.voice}
                            options={voices}
                            disabled={saving}
                            onChange={(voice) => save({ voice })}
                            testID="read-aloud-voice"
                        />
                        <Row gap="sm">
                            <Button
                                variant="secondary"
                                size="sm"
                                icon={preview.active ? Square : Volume2}
                                label={preview.active ? t('readAloud.stop') : t('readAloud.settings.preview')}
                                disabled={saving}
                                onPress={preview.toggle}
                                testID="read-aloud-preview"
                            />
                            {preview.loading ? <Spinner size="sm" /> : null}
                        </Row>
                        <Text role="label" tone="muted">
                            {t('readAloud.settings.englishOnly')}
                        </Text>
                        <ReadAloudErrorNotice messageId={PREVIEW_ID} />
                    </Stack>
                </Card>
            </SettingsAnchor>
            <SettingsAnchor name="speed">
                <Card>
                    <Select
                        label={t('readAloud.settings.speed')}
                        value={String(data.speed)}
                        options={speeds}
                        disabled={saving}
                        onChange={(speed) => save({ speed: Number(speed) })}
                        testID="read-aloud-speed"
                    />
                </Card>
            </SettingsAnchor>
            <SettingsAnchor name="auto">
                <Card>
                    <Switch
                        label={t('readAloud.settings.auto')}
                        value={data.auto}
                        disabled={saving}
                        onValueChange={(auto) => save({ auto })}
                        testID="read-aloud-auto"
                    />
                </Card>
            </SettingsAnchor>
            <Text role="label" tone="muted">
                {t('readAloud.settings.shared')}
            </Text>
            {update.error ? (
                <Notice tone="danger" message={t.compose((say) => say('readAloud.settings.saveFailed', { reason: describeFailureWith(update.error, say) }))} testID="read-aloud-save-error" />
            ) : null}
        </Stack>
    );
}
