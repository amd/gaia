// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Card registry — maps a `render` key (the `tool_result.render` field on
 * the frozen /query SSE contract, see docs/spec/agent-ui-query-sse-contract.md
 * §4.3) to the component that renders its `data` payload. RenderCard.tsx
 * resolves this map and falls back to UnsupportedCard for unknown keys.
 */

import type { ComponentType } from 'react';
import { TableCard } from './TableCard';
import { KeyValueCard } from './KeyValueCard';
import { ListCard } from './ListCard';
import { ImageCard } from './ImageCard';
import { AudioCard } from './AudioCard';
import { DiffCard } from './DiffCard';
import { ConfirmationCard } from './ConfirmationCard';
import { NeedsInputCard } from './NeedsInputCard';

export type CardComponent = ComponentType<{ data: unknown }>;

export const CARD_REGISTRY: Record<string, CardComponent> = {
    table: TableCard,
    key_value: KeyValueCard,
    list: ListCard,
    image: ImageCard,
    audio: AudioCard,
    diff: DiffCard,
    needs_confirmation: ConfirmationCard,
    needs_input: NeedsInputCard,
};
