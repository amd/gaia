// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Check, CircleAlert } from '@/ui/icons';
import { Icon, Pressable, Row, Spinner, Stack, Surface, Text } from '@/ui';

import { STAGE_LABELS, type RailStep, type SetupStage } from './flow';

/**
 * All five steps in one row, so none is cut off at 320 wide; each label wraps under its marker.
 * Only steps setup has reached can be opened.
 */
export function SetupRail({ steps, onOpen }: { steps: RailStep[]; onOpen: (id: SetupStage) => void }) {
    const t = useT();
    return (
        <Stack gap="sm" accessibilityRole="header">
            <Text role="label" weight="semibold" tone="secondary">{t('setup.title')}</Text>
            <Row gap="xs" align="start" accessibilityRole="list">
                {steps.map((step, i) => {
                    const label = t(STAGE_LABELS[step.id]);
                    const body = (
                        <Stack gap="xs" align="center" flex={1}>
                            <Surface
                                tone={step.shown ? 'card' : 'secondary'}
                                radius="full"
                                padding="sm"
                                border={step.shown ? 'accent' : true}
                            >
                                <Marker status={step.status} n={i + 1} />
                            </Surface>
                            <Text
                                role="caption"
                                align="center"
                                weight={step.shown ? 'semibold' : 'regular'}
                                tone={step.shown ? 'accent' : step.reachable ? 'primary' : 'muted'}
                            >
                                {label}
                            </Text>
                        </Stack>
                    );
                    return (
                        <Stack key={step.id} flex={1}>
                            {step.reachable && !step.shown ? (
                                <Pressable onPress={() => onOpen(step.id)} accessibilityLabel={label} accessibilityRole="button">
                                    {body}
                                </Pressable>
                            ) : (
                                <Stack accessibilityRole="text">{body}</Stack>
                            )}
                        </Stack>
                    );
                })}
            </Row>
        </Stack>
    );
}

function Marker({ status, n }: { status: RailStep['status']; n: number }) {
    if (status === 'done') return <Icon icon={Check} size="sm" tone="success" />;
    if (status === 'failed') return <Icon icon={CircleAlert} size="sm" tone="danger" />;
    if (status === 'active') return <Spinner size="sm" tone="accent" />;
    return <Text role="caption" weight="semibold" tone={status === 'current' ? 'accent' : 'muted'}>{String(n)}</Text>;
}
