// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Goal } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Button, Icon, Row, Stack, Surface, Text } from '@/ui';
import { Zap } from '@/ui/icons';

interface SuggestedGoalsProps {
    goals: Goal[];
    /** The goal whose Accept or Reject is running, if any. */
    busyId: string | null;
    onAccept: (goal: Goal) => void;
    onReject: (goal: Goal) => void;
}

/** Goals GAIA suggested, each with Accept and Reject at full width so neither is a stretch for a thumb. */
export function SuggestedGoals({ goals, busyId, onAccept, onReject }: SuggestedGoalsProps) {
    const t = useT();
    if (goals.length === 0) return null;
    return (
        <Surface tone="secondary" radius="lg" border="accent" padding="md" testID="goals-suggested">
            <Stack gap="md">
                <Row gap="sm">
                    <Icon icon={Zap} size="sm" tone="accent" />
                    <Stack flex={1}>
                        <Text role="label" weight="semibold" accessibilityRole="header">
                            {t('mem.goals.suggested', { n: goals.length })}
                        </Text>
                    </Stack>
                </Row>
                {goals.map((goal) => (
                    <Surface key={goal.id} tone="card" radius="md" border padding="md" testID={`goals-suggested-${goal.id}`}>
                        <Stack gap="md">
                            <Stack gap="xs">
                                <Text weight="semibold" dir="auto">
                                    {goal.title}
                                </Text>
                                {goal.description ? (
                                    <Text tone="secondary" dir="auto">
                                        {goal.description}
                                    </Text>
                                ) : null}
                            </Stack>
                            <Row gap="sm" stack>
                                <Stack flex={1}>
                                    <Button
                                        label={t.bare('mem.goals.reject')}
                                        accessibilityLabel={`${t('mem.goals.reject')}: ${goal.title}`}
                                        variant="danger"
                                        disabled={busyId === goal.id}
                                        onPress={() => onReject(goal)}
                                        testID={`goals-reject-${goal.id}`}
                                    />
                                </Stack>
                                <Stack flex={1}>
                                    <Button
                                        label={t.bare('mem.goals.accept')}
                                        accessibilityLabel={`${t('mem.goals.accept')}: ${goal.title}`}
                                        variant="primary"
                                        busy={busyId === goal.id}
                                        onPress={() => onAccept(goal)}
                                        testID={`goals-accept-${goal.id}`}
                                    />
                                </Stack>
                            </Row>
                        </Stack>
                    </Surface>
                ))}
            </Stack>
        </Surface>
    );
}
