// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { overflowCount } from '@webui/utils/renderCards.core';

import { useT } from '@/i18n/useT';
import { Text } from '@/ui';

/** The "+N more (truncated)" line under a card whose payload went past the cap. */
export function TruncationNote({ total }: { total: number }) {
    const t = useT();
    return (
        <Text role="caption" tone="secondary" italic>
            {t('render.moreTruncated', { n: overflowCount(total) })}
        </Text>
    );
}
