// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';
import { formatSize } from '@webui/utils/format';

import type { MemoryStats } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Divider, Icon, Row, Stack, Text } from '@/ui';
import { Shield } from '@/ui/icons';

/** The database size and the counts under the web's tab: a muted line, entities and sensitive only when there are any. */
export function FooterStats({ stats }: { stats: MemoryStats }) {
    const t = useT();
    const { knowledge } = stats;
    return (
        <Stack gap="sm" testID="memory-footer-stats">
            <Divider />
            <Row wrap gap="md">
                <Text role="caption" tone="muted">
                    {t('mem.footer.database')}: {isolate(formatSize(stats.db_size_bytes), 'ltr')}
                </Text>
                {knowledge.avg_confidence != null ? (
                    <Text role="caption" tone="muted">
                        {t('mem.footer.avgConfidence', { n: knowledge.avg_confidence.toFixed(2) })}
                    </Text>
                ) : null}
                {knowledge.entity_count > 0 ? (
                    <Text role="caption" tone="muted">
                        {t('mem.footer.entities', { n: knowledge.entity_count })}
                    </Text>
                ) : null}
                {knowledge.sensitive_count > 0 ? (
                    <Row gap="xs">
                        <Icon icon={Shield} size="sm" tone="muted" />
                        <Text role="caption" tone="muted">
                            {t('mem.footer.sensitive', { n: knowledge.sensitive_count })}
                        </Text>
                    </Row>
                ) : null}
            </Row>
        </Stack>
    );
}
