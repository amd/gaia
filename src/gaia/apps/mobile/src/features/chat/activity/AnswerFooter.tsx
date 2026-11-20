// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { InferenceStats } from '@webui/types';
import type { TurnEnding } from '@webui/utils/turnEnding.core';
import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { Disclosure, Stack, Text } from '@/ui';

import { answerFooterText } from './footerText';

export interface AnswerFooterProps {
    latencyMs?: number;
    stats?: InferenceStats;
    ended?: TurnEnding;
}

/** Under a finished answer: how long it took and where, with the exact numbers one tap away. */
export function AnswerFooter({ latencyMs, stats, ended }: AnswerFooterProps) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const { summary, exact } = answerFooterText(latencyMs, stats, ended, t);
    if (!summary && exact.length === 0) return null;
    return (
        <Disclosure title={summary ?? t('answer.details')} open={open} onToggle={() => setOpen((v) => !v)} testID="answer-footer">
            <Stack gap="xs">
                {exact.map((line) => (
                    <Text key={line} role="caption" tone="muted">
                        {line}
                    </Text>
                ))}
            </Stack>
        </Disclosure>
    );
}
