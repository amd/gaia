// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Card, Icon, Row, Stack, Text } from '@/ui';
import { ArrowRight } from '@/ui/icons';

interface DocumentBriefCardProps {
    // How many indexed documents the card attaches.
    count: number;
    onPress: () => void;
}

// The server's "Ask about your documents" start card: a tap attaches the whole library and asks.
export function DocumentBriefCard({ count, onPress }: DocumentBriefCardProps) {
    const t = useT();
    return (
        <Card padding="md" onPress={onPress} accessibilityLabel={t('card.document-brief.title')} testID="document-brief-card">
            <Stack gap="xs">
                <Row gap="xs">
                    <Text role="label">{t('card.document-brief.title')}</Text>
                    <Icon icon={ArrowRight} size="sm" tone="secondary" />
                </Row>
                <Text role="caption" tone="secondary">
                    {t.plural('card.document-brief.note', count)}
                </Text>
            </Stack>
        </Card>
    );
}
