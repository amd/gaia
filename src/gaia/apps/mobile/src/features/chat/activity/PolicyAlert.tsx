// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ActivityRow } from '@webui/utils/agentActivity.core';

import { useT } from '@/i18n/useT';
import { Icon, Row, Stack, Surface, Text } from '@/ui';
import { ShieldAlert } from '@/ui/icons';

function Line({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <Row gap="sm" wrap>
            <Text role="caption" tone="muted">
                {label}
            </Text>
            {children}
        </Row>
    );
}

/** A tool call the governance policy blocked: what, why, which rules and the receipt. */
export function PolicyAlert({ step }: { step: ActivityRow }) {
    const t = useT();
    return (
        <Surface tone="recessed" radius="md" border>
            <Stack gap="xs" paddingX="md" paddingY="sm" testID="policy-alert">
                <Row gap="sm" justify="between">
                    <Row gap="xs">
                        <Icon icon={ShieldAlert} size="sm" tone="warning" />
                        <Text role="label" weight="semibold">
                            {t('activity.policy.title')}
                        </Text>
                    </Row>
                    <Text role="caption" tone="danger" dir="ltr">
                        {step.decision || 'BLOCK'}
                    </Text>
                </Row>
                <Line label={t('activity.policy.tool')}>
                    <Text role="label" mono code dir="ltr" selectable>
                        {step.tool || t('activity.policy.unknownTool')}
                    </Text>
                </Line>
                <Text role="label" dir="auto" tone="secondary">
                    {step.reason || step.detail}
                </Text>
                {step.ruleIds && step.ruleIds.length > 0 ? (
                    <Line label={t('activity.policy.rules')}>
                        <Text role="label" mono code dir="ltr" selectable>
                            {step.ruleIds.join(', ')}
                        </Text>
                    </Line>
                ) : null}
                {step.policyVersion ? (
                    <Line label={t('activity.policy.policy')}>
                        <Text role="label" mono code dir="ltr" selectable>
                            {step.policyVersion}
                        </Text>
                    </Line>
                ) : null}
                <Line label={t('activity.policy.receipt')}>
                    {step.receiptId ? (
                        <Text role="label" mono code dir="ltr" selectable>
                            {step.receiptId}
                        </Text>
                    ) : (
                        <Text role="caption" tone="muted">
                            {t('activity.policy.receiptUnavailable')}
                        </Text>
                    )}
                </Line>
            </Stack>
        </Surface>
    );
}
