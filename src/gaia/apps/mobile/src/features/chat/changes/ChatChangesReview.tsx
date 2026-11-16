// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SessionChanges } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Sheet, Stack, Text } from '@/ui';

import { DiffCard } from '../cards/DiffCard';
import { noteOf } from './ChangesReview';
import { totalText } from './changesText';

/** Every file the chat changed, from before its first turn to after its last, built from the loaded totals. */
export function ChatChangesReview({ changes, onClose }: { changes: SessionChanges; onClose: () => void }) {
    const t = useT();
    const label = { edited: t('chg.edited'), created: t('chg.created'), deleted: t('chg.deleted') };
    return (
        <Sheet open onClose={onClose} title={t('mobileApp.changes.chat.reviewTitle')}>
            <Stack gap="md" paddingX="lg" paddingY="md">
                <Text role="caption" tone="secondary" dir="auto">
                    {t('mobileApp.changes.chat.reviewHeading', { total: totalText(changes, t) })}
                </Text>
                {changes.files.map((file) => (
                    <DiffCard
                        key={file.path}
                        data={{
                            path: file.path,
                            badge: label[file.change],
                            note: noteOf(file, t),
                            unified: file.binary || file.too_large ? '' : file.unified,
                        }}
                    />
                ))}
            </Stack>
        </Sheet>
    );
}
