// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';

import type { StreamEvent } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Button, Row, Stack, Surface, Text } from '@/ui';

export interface ConfirmationCardProps {
    /** The machine tool name, shown as is. */
    action: string;
    /** Can carry tool arguments derived from untrusted content: shown only as plain text. */
    summary: string;
    onDismiss?: () => void;
}

/** The card's props from a needs_confirmation event; null when it lacks an action or summary. */
export function confirmationProps(event: Pick<StreamEvent, 'type' | 'action' | 'summary'>): ConfirmationCardProps | null {
    if (event.type !== 'needs_confirmation' || typeof event.action !== 'string' || typeof event.summary !== 'string') return null;
    return { action: event.action, summary: event.summary };
}

// No per-action lookup, so an unknown tool stays safe; the label comes from `action` alone.
export function humanizeAction(action: string): string {
    return action.replace(/_/g, ' ').trim() || 'action';
}

/** Informational only: the run is over and its answer carries the hand-off; Dismiss just hides the card. */
export function ConfirmationCard({ action, summary, onDismiss }: ConfirmationCardProps) {
    const t = useT();
    const [dismissed, setDismissed] = useState(false);

    if (dismissed) {
        return (
            <Surface tone="secondary" radius="md" padding="md" border>
                <Text role="caption" tone="muted">
                    {t('confirmation.dismissed')}
                </Text>
            </Surface>
        );
    }

    return (
        <Surface tone="card" radius="md" padding="md" border>
            <Stack gap="md">
                <Stack gap="xs">
                    <Row gap="sm" wrap>
                        <Text role="caption" weight="semibold" tone="warning">
                            {t('confirmation.badge')}
                        </Text>
                        <Text role="label" weight="semibold">
                            {humanizeAction(action)}
                        </Text>
                    </Row>
                    <Text role="caption" tone="muted" mono>
                        {isolate(action, 'ltr')}
                    </Text>
                </Stack>
                <Surface tone="recessed" radius="sm" padding="sm" border>
                    <Text role="caption" mono selectable>
                        {summary}
                    </Text>
                </Surface>
                <Text role="caption" tone="secondary">
                    {t('confirmation.note')}
                </Text>
                <Row justify="end">
                    <Button
                        label={t('banner.dismiss')}
                        variant="ghost"
                        size="sm"
                        onPress={() => {
                            setDismissed(true);
                            onDismiss?.();
                        }}
                    />
                </Row>
            </Stack>
        </Surface>
    );
}
