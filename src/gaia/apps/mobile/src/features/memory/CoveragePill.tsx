// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEmbeddingCoverage } from '@/data';
import { useT } from '@/i18n/useT';
import { ProgressLine, Stack, Text } from '@/ui';

/** How many memories are embedded; hidden until the PC reports at least one entry. The web shows the counts as a tooltip, which a phone has no hover for. */
export function CoveragePill() {
    const t = useT();
    const { data } = useEmbeddingCoverage();
    if (!data || data.total_items <= 0) return null;
    const counts = t('mem.coverage.title', { n: data.with_embedding, total: data.total_items });
    return (
        <Stack gap="xs" paddingX="lg" paddingY="sm" testID="memory-coverage">
            <ProgressLine value={data.coverage_pct / 100} label={t('mem.coverage.label', { pct: `${Math.round(data.coverage_pct)}%` })} />
            <Text role="caption" tone="muted">
                {counts}
            </Text>
        </Stack>
    );
}
