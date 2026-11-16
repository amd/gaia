// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StepStatusFilter } from '@webui/utils/agentActivity.core';

import { useT } from '@/i18n/useT';
import { SearchField, SegmentedControl, Stack } from '@/ui';

export interface StepFilterProps {
    name: string;
    status: StepStatusFilter;
    onName: (name: string) => void;
    onStatus: (status: StepStatusFilter) => void;
}

/** Sifts a finished turn's tool calls by name and by outcome. */
export function StepFilter({ name, status, onName, onStatus }: StepFilterProps) {
    const t = useT();
    return (
        <Stack gap="sm" testID="step-filter">
            <SearchField value={name} onChangeText={onName} placeholder={t.bare('activity.filterPlaceholder')} accessibilityLabel={t('activity.filterByTool')} />
            <SegmentedControl
                accessibilityLabel={t('activity.filterByStatus')}
                value={status}
                onChange={(v) => onStatus(v as StepStatusFilter)}
                options={[
                    { value: 'all', label: t.bare('activity.filter.all') },
                    { value: 'success', label: t.bare('activity.filter.success') },
                    { value: 'error', label: t.bare('activity.filter.error') },
                ]}
            />
        </Stack>
    );
}
