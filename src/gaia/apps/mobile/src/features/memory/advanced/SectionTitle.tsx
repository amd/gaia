// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LucideIcon } from 'lucide-react-native';

import { Icon, Row, Stack, Text } from '@/ui';

/** The web's section title: a small icon and a secondary-tone label, with an optional note beside it. */
export function SectionTitle({ icon, title, note }: { icon: LucideIcon; title: string; note?: string }) {
    return (
        <Row gap="sm" wrap accessibilityRole="header">
            <Icon icon={icon} size="sm" tone="secondary" />
            <Text role="label" weight="semibold" tone="secondary">
                {title}
            </Text>
            {note ? (
                <Text role="caption" tone="muted">
                    {note}
                </Text>
            ) : null}
        </Row>
    );
}

/** A section's own empty line: icon and muted text, small enough to sit between sections. */
export function SectionEmpty({ icon, text }: { icon: LucideIcon; text: string }) {
    return (
        <Stack gap="sm" align="center" paddingY="lg">
            <Icon icon={icon} size="lg" tone="muted" />
            <Text tone="muted" align="center">
                {text}
            </Text>
        </Stack>
    );
}

