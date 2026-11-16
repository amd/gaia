// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { useDeleteAllChats, useSessions } from '@/data';
import { describeError, describeFailureWith, useT } from '@/i18n/useT';
import { Button, Card, Notice, Sheet, Stack, Text } from '@/ui';


/** Deletes every chat after a confirmation sheet; one failing chat doesn't stop the rest. */
export function DeleteChats() {
    const t = useT();
    const sessions = useSessions().data?.sessions ?? [];
    const deleteAll = useDeleteAllChats();
    // The count is read when the sheet opens, so the buttons say what the person agreed to.
    const [asking, setAsking] = useState<number | null>(null);
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

    const confirm = async () => {
        setAsking(null);
        setResult(null);
        try {
            const { failed } = await deleteAll.mutateAsync(sessions.map((s) => ({ id: s.id, title: s.title })));
            setResult(
                failed.length
                    ? { ok: false, text: t.compose((say) => say('settings.privacy.deleteFailed', { n: failed.length, reason: failed.map((f) => `${f.title}: ${describeFailureWith(f.error, say)}`).join('; ') })) }
                    : { ok: true, text: t('settings.privacy.deleted') },
            );
        } catch (error) {
            setResult({ ok: false, text: describeError(error, t) });
        }
    };

    return (
        <Card>
            <Stack gap="md">
                <Text role="label" tone="muted">
                    {sessions.length === 0 ? t('settings.privacy.nothingToDelete') : t('settings.privacy.deleteWarning')}
                </Text>
                <Button
                    variant="danger"
                    full
                    label={t('settings.privacy.delete')}
                    busy={deleteAll.isPending}
                    disabled={sessions.length === 0}
                    onPress={() => setAsking(sessions.length)}
                    testID="privacy-delete-all"
                />
                {result ? <Notice tone={result.ok ? 'success' : 'danger'} message={result.text} testID="privacy-delete-result" /> : null}
            </Stack>
            <Sheet
                open={asking !== null}
                onClose={() => setAsking(null)}
                title={t.plural('mobileApp.settings.privacy.deleteAsk', asking ?? 0)}
                footer={
                    <Stack gap="sm">
                        <Button variant="danger" full label={t.plural('mobileApp.settings.privacy.deleteYes', asking ?? 0)} onPress={() => void confirm()} testID="privacy-delete-confirm" />
                        <Button variant="secondary" full label={t('setup.cancel')} onPress={() => setAsking(null)} />
                    </Stack>
                }
            >
                <Stack gap="sm">
                    <Text role="body">{t('settings.privacy.deleteWarning')}</Text>
                    <Text role="body" weight="medium">
                        {t('mobileApp.settings.privacy.deleteFinal')}
                    </Text>
                </Stack>
            </Sheet>
        </Card>
    );
}
