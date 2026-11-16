// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isServerDown } from '@webui/utils/serverDown';

import { GaiaApiError } from '@/api/errors';
import { describeError, useT } from '@/i18n/useT';
import { Button, Disclosure, Ltr, Notice, Sheet, Stack, Text } from '@/ui';
import { isolate } from '@webui/i18n/registry';
import { tEn } from '@webui/i18n/translator';

/** A raw error or log line, one tap away and kept left to right. */
export function RawDetails({ text }: { text: string }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <Disclosure title={t('settings.errors.details')} open={open} onToggle={() => setOpen((v) => !v)}>
            <Text role="caption" tone="muted" selectable><Ltr>{text}</Ltr></Text>
        </Disclosure>
    );
}

/** An action's failure in plain words; a refusal from the PC says to do it there, and the raw text stays behind Details. */
export function ActionError({ error, title }: { error: unknown; title?: string }) {
    const t = useT();
    if (error instanceof GaiaApiError && error.status === 403) {
        return <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="advanced-pc-only" />;
    }
    const raw = describeError(error, tEn);
    return (
        <Stack gap="sm">
            <Notice tone="danger" title={title} message={isServerDown(raw) ? t('mobileApp.settings.errors.serverDown') : t('mobileApp.settings.modelActionFailed')} />
            <RawDetails text={raw} />
        </Stack>
    );
}

/** Asks before a load that competes with chats that are answering; "Load anyway" sends it forced. */
export function LoadAnywaySheet({ open, name, onClose, onConfirm }: { open: boolean; name: string; onClose: () => void; onConfirm: () => void }) {
    const t = useT();
    return (
        <Sheet
            open={open}
            onClose={onClose}
            title={t('settings.advanced.load')}
            footer={
                <Stack gap="sm">
                    <Button label={t('mobileApp.settings.loadAnywayButton')} variant="danger" full onPress={onConfirm} />
                    <Button label={t('settings.key.cancel')} variant="secondary" full onPress={onClose} />
                </Stack>
            }
        >
            <Text role="body">{t('mobileApp.settings.loadAnyway', { name: isolate(name, 'ltr') })}</Text>
        </Sheet>
    );
}
