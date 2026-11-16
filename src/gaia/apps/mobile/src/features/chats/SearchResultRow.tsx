// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { splitMatch, type MatchParts } from '@webui/utils/chatSearch';

import { useT } from '@/i18n/useT';
import { Icon, Pressable, Row, Stack, StatusDot, Text } from '@/ui';
import { EyeOff } from '@/ui/icons';

import { rowText, statusTone, type ChatRow } from './chatList';

function Marked({ parts }: { parts: MatchParts }) {
    return (
        <>
            {parts.before}
            <Text tone="accent" weight="bold">{parts.match}</Text>
            {parts.after}
        </>
    );
}

export interface SearchResultRowProps {
    row: ChatRow;
    query: string;
    /** The matched message text, when the hit was inside the chat rather than in its title. */
    snippet: MatchParts | null;
    onSelect: (sessionId: string) => void;
}

export function SearchResultRow({ row, query, snippet, onSelect }: SearchResultRowProps) {
    const t = useT();
    const text = rowText(row, t);
    const title = splitMatch(row.title, query);
    return (
        <Pressable accessibilityLabel={text.aria} accessibilityState={{ selected: row.active }} minSize={48} onPress={() => onSelect(row.id)}>
            <Stack gap="xs" paddingX="lg" paddingY="sm">
                <Row gap="xs">
                        {row.dot && text.status ? <StatusDot tone={statusTone(row.dot)} label={text.status} /> : null}
                    {row.private ? <Icon icon={EyeOff} size="sm" label={t('row.private')} /> : null}
                    <Text role="ui" weight={row.active || row.unread ? 'semibold' : 'medium'} numberOfLines={1}>
                        {title ? <Marked parts={title} /> : row.title}
                    </Text>
                </Row>
                {snippet ? (
                    <Text role="caption" tone="muted" numberOfLines={2}>
                        <Marked parts={snippet} />
                    </Text>
                ) : null}
            </Stack>
        </Pressable>
    );
}
