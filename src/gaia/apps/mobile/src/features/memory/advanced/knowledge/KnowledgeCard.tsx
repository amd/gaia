// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatRelativeDate, isOverdue, type KnowledgeEntry } from '@webui/utils/memoryFormat.core';

import { useT } from '@/i18n/useT';
import { Badge, Card, Icon, Row, Spacer, StatusDot, Stack, Text } from '@/ui';
import { Shield } from '@/ui/icons';

import { CATEGORY_TONE, categoryLabel } from './knowledgeUi';

/** One memory as a card: what kind, the whole text (wrapped, never cut), and where and when. Tapping opens its detail. */
export function KnowledgeCard({ item, onOpen }: { item: KnowledgeEntry; onOpen: (item: KnowledgeEntry) => void }) {
    const t = useT();
    const label = categoryLabel(item.category, item.source, t, true);
    const full = categoryLabel(item.category, item.source, t, false);
    const text = item.sensitive ? t('mem.about.sensitiveMemory') : item.content;
    const superseded = !!item.superseded_by;

    return (
        <Card padding="md" onPress={() => onOpen(item)} accessibilityLabel={`${full}: ${text}`} testID={`memory-knowledge-card-${item.id}`}>
            <Stack gap="sm">
                <Row gap="sm" wrap>
                    <Badge label={label} tone={CATEGORY_TONE[item.category] ?? 'neutral'} accessibilityLabel={full} />
                    {item.sensitive ? <Icon icon={Shield} size="sm" tone="warning" label={t('mem.form.sensitive')} /> : null}
                    {superseded ? <Badge label={t.bare('mem.knowledge.superseded')} accessibilityLabel={t('mem.knowledge.superseded')} /> : null}
                    {item.due_at ? (
                        <Badge label={formatRelativeDate(item.due_at, t)} tone={isOverdue(item.due_at) ? 'danger' : 'warning'} />
                    ) : null}
                    <Spacer />
                    {item.has_embedding !== undefined ? (
                        <StatusDot tone={item.has_embedding ? 'success' : 'attention'} label={t(item.has_embedding ? 'mem.knowledge.embedded' : 'mem.knowledge.embedMissing')} />
                    ) : null}
                </Row>
                <Text dir="auto" tone={superseded || item.sensitive ? 'muted' : 'primary'}>
                    {text}
                </Text>
                <Row gap="xs" wrap>
                    <Text role="caption" tone="muted" dir="auto">
                        {item.context}
                    </Text>
                    {item.entity ? (
                        <Text role="caption" tone="muted" dir="auto">
                            {`· ${item.entity}`}
                        </Text>
                    ) : null}
                    <Text role="caption" tone="muted">
                        {`· ${formatRelativeDate(item.updated_at, t)}`}
                    </Text>
                </Row>
            </Stack>
        </Card>
    );
}
