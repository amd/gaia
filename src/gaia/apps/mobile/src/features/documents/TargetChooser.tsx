// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { SegmentedControl, Stack, Text } from '@/ui';

export type LibraryTarget = 'chat' | 'next';

interface TargetChooserProps {
    // The open chat's title; null when no chat is open.
    chatTitle: string | null;
    target: LibraryTarget;
    onChange: (target: LibraryTarget) => void;
}

// Where documents added here go: the open chat, or the next new chat.
export function TargetChooser({ chatTitle, target, onChange }: TargetChooserProps) {
    const t = useT();
    return (
        <Stack gap="xs">
            {chatTitle === null ? (
                <Text tone="secondary">{t('docs.nextChatOnly')}</Text>
            ) : (
                <>
                    <Text role="label" tone="secondary">
                        {t('docs.addTo')}
                    </Text>
                    <SegmentedControl
                        accessibilityLabel={t('docs.addTo')}
                        value={target}
                        onChange={(v) => onChange(v as LibraryTarget)}
                        options={[
                            { value: 'chat', label: t.bare('docs.thisChat', { chat: `“${chatTitle}”` }) },
                            { value: 'next', label: t.bare('docs.nextChat') },
                        ]}
                    />
                </>
            )}
            <Text role="caption" tone="muted">
                {t('docs.hint')}
            </Text>
        </Stack>
    );
}
