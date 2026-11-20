// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ref, type MessageKey } from '@webui/i18n/registry';
import { compactNarration, type ActivityRow, type PlanItemStatus } from '@webui/utils/agentActivity.core';

import { useT } from '@/i18n/useT';
import { Icon, Row, Spinner, Stack, Text } from '@/ui';
import { Check, CircleAlert, Square } from '@/ui/icons';

const STATUS_LABEL: Record<PlanItemStatus, MessageKey> = {
    done: 'activity.planStatus.done',
    running: 'activity.planStatus.running',
    failed: 'activity.planStatus.failed',
    stopped: 'activity.planStatus.stopped',
    pending: 'activity.planStatus.pending',
};

function StatusMark({ status }: { status?: PlanItemStatus }) {
    if (status === 'done') return <Icon icon={Check} size="sm" tone="success" />;
    if (status === 'running') return <Spinner size="sm" />;
    if (status === 'failed' || status === 'stopped') return <Icon icon={CircleAlert} size="sm" tone="danger" />;
    return <Icon icon={Square} size="sm" tone="muted" />;
}

/** The plan as a checklist: each step done, running, failed or not started. */
export function PlanChecklist({ step }: { step: ActivityRow }) {
    const t = useT();
    const items = step.planSteps ?? [];
    if (items.length === 0) return null;
    const statuses = step.planStatuses;
    const done = statuses?.filter((s) => s === 'done').length ?? 0;
    return (
        <Stack gap="xs" testID="plan-checklist">
            <Row gap="sm">
                <Text role="label" weight="medium" tone="secondary">
                    {t('activity.plan')}
                </Text>
                {statuses ? (
                    <Text role="caption" tone="muted">
                        {t('activity.planDone', { done, total: items.length })}
                    </Text>
                ) : null}
            </Row>
            {items.map((raw, i) => {
                const status = statuses?.[i];
                const text = compactNarration(step.planNarrations?.[i] || raw);
                return (
                    <Row
                        key={i}
                        gap="sm"
                        align="start"
                        accessibilityRole="text"
                    >
                        <StatusMark status={status} />
                        <Stack flex={1}>
                            <Text
                                role="label"
                                dir="auto"
                                tone="secondary"
                                accessibilityLabel={status ? t('activity.planItem', { status: ref(STATUS_LABEL[status]), text }) : undefined}
                            >
                                {text}
                            </Text>
                        </Stack>
                    </Row>
                );
            })}
        </Stack>
    );
}
