// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

import type { ParsedSchedule } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { useCreateSchedule, useGaiaClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Button, Notice, Row, Sheet, Stack, Text, TextField } from '@/ui';

import { composeDateLocale, describeSchedule, formatNextRun } from './scheduleText';

const PARSE_DEBOUNCE_MS = 400;

type Preview = { status: 'idle' } | { status: 'parsing' } | { status: 'done'; parsed: ParsedSchedule } | { status: 'failed' };

const deviceTimeZone = (): string | undefined => Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;

/** Reads the "when to run" text on the PC after the typing pauses, so the preview follows what is written. */
function usePreview(input: string): Preview {
    const client = useGaiaClient();
    const [answer, setAnswer] = useState<{ input: string; result: Exclude<Preview, { status: 'idle' | 'parsing' }> } | null>(null);
    useEffect(() => {
        if (!input.trim() || !client) return;
        const controller = new AbortController();
        const timer = setTimeout(() => {
            client
                .parseSchedule(input, deviceTimeZone(), { signal: controller.signal })
                .then((parsed) => setAnswer({ input, result: { status: 'done', parsed } }))
                .catch(() => {
                    if (!controller.signal.aborted) setAnswer({ input, result: { status: 'failed' } });
                });
        }, PARSE_DEBOUNCE_MS);
        return () => {
            clearTimeout(timer);
            controller.abort();
        };
    }, [client, input]);
    if (!input.trim() || !client) return { status: 'idle' };
    return answer?.input === input ? answer.result : { status: 'parsing' };
}

export interface NewScheduleSheetProps {
    open: boolean;
    onClose: () => void;
}

export function NewScheduleSheet({ open, onClose }: NewScheduleSheetProps) {
    const t = useT();
    const create = useCreateSchedule();
    const [when, setWhen] = useState('');
    const [name, setName] = useState('');
    const [prompt, setPrompt] = useState('');
    const preview = usePreview(when);
    const parsed = preview.status === 'done' ? preview.parsed : null;
    const rejected = parsed !== null && !parsed.valid;
    const nameTaken = create.error instanceof GaiaApiError && create.error.status === 409;
    const formError = create.isError && !nameTaken ? describeError(create.error, t) : null;
    // The backend validates on create, so only a parse that says no blocks it.
    const canCreate = when.trim() !== '' && name.trim() !== '' && prompt.trim() !== '' && !rejected && !create.isPending;

    const close = () => {
        create.reset();
        onClose();
    };

    const submit = () => {
        if (!canCreate) return;
        create.mutate(
            { name: name.trim(), interval: when.trim(), prompt: prompt.trim(), timezone: deviceTimeZone() },
            {
                onSuccess: () => {
                    setWhen('');
                    setName('');
                    setPrompt('');
                    close();
                },
            },
        );
    };

    return (
        <Sheet
            open={open}
            onClose={close}
            title={t('schedules.new')}
            footer={
                <Row gap="sm" justify="end">
                    <Button label={t('schedules.cancel')} variant="ghost" onPress={close} />
                    <Button
                        label={t(create.isPending ? 'schedules.creating' : 'schedules.create')}
                        variant="primary"
                        busy={create.isPending}
                        disabled={!canCreate}
                        onPress={submit}
                    />
                </Row>
            }
        >
            <Stack gap="md">
                <TextField
                    label={t('schedules.when')}
                    value={when}
                    onChangeText={setWhen}
                    placeholder={t.bare('schedules.whenPlaceholder')}
                    hint={t('schedules.whenHint')}
                    multiline
                    maxLines={3}
                    autoFocus
                />
                {preview.status === 'parsing' ? <Text role="caption" tone="muted">{t('schedules.parsing')}</Text> : null}
                {parsed?.valid ? (
                    <Stack gap="xs">
                        <Text role="body">{describeSchedule(parsed.description_parts, t) ?? parsed.description}</Text>
                        {parsed.next_run_at ? (
                            <Text role="caption" tone="muted">
                                {t.compose((say, code) =>
                                    say('schedules.nextRun', {
                                        when: formatNextRun(parsed.next_run_at, say, new Date(), composeDateLocale(code, t)),
                                    }),
                                )}
                            </Text>
                        ) : null}
                    </Stack>
                ) : null}
                {rejected ? (
                    <Notice
                        tone="danger"
                        title={t('schedules.notUnderstood')}
                        message={/[A-Za-z]/.test(when) ? parsed.description : undefined}
                    />
                ) : null}
                <TextField
                    label={t('schedules.name')}
                    value={name}
                    onChangeText={(text) => {
                        setName(text);
                        if (nameTaken) create.reset();
                    }}
                    placeholder={t.bare('schedules.namePlaceholder')}
                    accessibilityLabel={t('schedules.namePlaceholder')}
                    error={nameTaken ? t('schedules.nameTaken') : undefined}
                />
                <TextField
                    label={t('schedules.prompt')}
                    value={prompt}
                    onChangeText={setPrompt}
                    placeholder={t.bare('schedules.promptPlaceholder')}
                    accessibilityLabel={t('schedules.promptPlaceholder')}
                    multiline
                />
                {formError ? <Notice tone="danger" message={formError} /> : null}
            </Stack>
        </Sheet>
    );
}
