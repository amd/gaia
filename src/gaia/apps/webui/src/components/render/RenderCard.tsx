// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { CARD_REGISTRY } from './registry';
import { CardErrorBoundary } from './CardErrorBoundary';
import { UnsupportedCard } from './UnsupportedCard';
import './render.css';

export interface RenderCardProps {
    /** The `tool_result.render` key naming which card to mount. */
    render: string;
    /** The `tool_result.data` payload, handed to the resolved card as-is. */
    data: unknown;
}

/**
 * Resolves a `render` key against the card registry and mounts the result.
 * Unknown keys fall back to UnsupportedCard; registered cards are wrapped in
 * CardErrorBoundary so one bad card can never blank the whole message.
 */
export function RenderCard({ render, data }: RenderCardProps) {
    const Card = CARD_REGISTRY[render];
    return (
        <div className="render-card">
            {Card ? (
                <CardErrorBoundary>
                    <Card data={data} />
                </CardErrorBoundary>
            ) : (
                <UnsupportedCard variant="unknown" render={render} data={data} />
            )}
        </div>
    );
}
