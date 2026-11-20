// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { RenderCardData } from '@webui/types';

import { Stack } from '@/ui';

import { CardErrorBoundary } from './CardErrorBoundary';
import { CalculatorCard } from './CalculatorCard';
import { DiffCard } from './DiffCard';
import { GuideCard } from './GuideCard';
import { KeyValueCard } from './KeyValueCard';
import { ListCard } from './ListCard';
import { TableCard } from './TableCard';
import { UnsupportedCard } from './UnsupportedCard';

/** The `tool_result.render` keys the phone draws; any other key shows the unsupported card. */
const CARD_REGISTRY: Record<string, ComponentType<{ data: unknown }>> = {
    calculator: CalculatorCard,
    diff: DiffCard,
    guide: GuideCard,
    key_value: KeyValueCard,
    list: ListCard,
    table: TableCard,
};

export function RenderCard({ render, data }: RenderCardData) {
    const Card = CARD_REGISTRY[render];
    if (!Card) return <UnsupportedCard variant="unknown" render={render} data={data} />;
    return (
        <CardErrorBoundary>
            <Card data={data} />
        </CardErrorBoundary>
    );
}

/** The cards of an answer, drawn above its text. */
export function AnswerCards({ cards }: { cards: readonly RenderCardData[] | undefined }) {
    if (!cards || cards.length === 0) return null;
    return (
        <Stack gap="sm" paddingY="xs" testID="answer-cards">
            {cards.map((card, i) => (
                <RenderCard key={i} render={card.render} data={card.data} />
            ))}
        </Stack>
    );
}
