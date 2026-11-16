// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useGoalStats } from '@/data';
import { useDirection, useT } from '@/i18n/useT';
import { Badge, Divider, HScroll, Icon, Pressable, Row, Stack, Text, type Tone } from '@/ui';
import { Brain, Sparkles, Target, User, type LucideIcon } from '@/ui/icons';

import { MEMORY_TABS, type MemoryTabKey } from './tabs';

const TAB_ICONS: Record<MemoryTabKey, LucideIcon> = { about: User, goals: Target, profile: Sparkles, advanced: Brain };

/** The desktop's underlined tab strip; it scrolls sideways when the labels don't fit, and a segmented control would fall back to a list. */
export function MemoryTabs({ value, onChange }: { value: MemoryTabKey; onChange: (key: MemoryTabKey) => void }) {
    const t = useT();
    const dir = useDirection();
    const pending = useGoalStats().data?.goals['pending_approval'] ?? 0;
    return (
        <Stack gap="none">
            <HScroll dir={dir} testID="memory-tabs">
                <Stack paddingX="md" accessibilityRole="tablist">
                    <Row>
                        {MEMORY_TABS.map((tab) => {
                            const selected = tab.key === value;
                            const tone: Tone = selected ? 'primary' : 'muted';
                            return (
                                <Pressable
                                    key={tab.key}
                                    onPress={() => onChange(tab.key)}
                                    accessibilityRole="tab"
                                    accessibilityLabel={t(tab.label)}
                                    accessibilityState={{ selected }}
                                    testID={`memory-tab-${tab.key}`}
                                >
                                    <Stack>
                                        <Stack paddingX="md" paddingY="md">
                                            <Row gap="xs">
                                                <Icon icon={TAB_ICONS[tab.key]} tone={tone} size="sm" />
                                                <Text role="label" weight="medium" tone={tone} numberOfLines={1} compact>
                                                    {t.bare(tab.label)}
                                                </Text>
                                                {tab.key === 'goals' && pending > 0 ? <Badge label={String(pending)} tone="warning" shape="pill" /> : null}
                                            </Row>
                                        </Stack>
                                        <Divider line={selected ? 'accent' : 'clear'} />
                                    </Stack>
                                </Pressable>
                            );
                        })}
                    </Row>
                </Stack>
            </HScroll>
            <Divider />
        </Stack>
    );
}
