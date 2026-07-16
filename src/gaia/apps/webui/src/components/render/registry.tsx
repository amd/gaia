// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Card registry — maps a `render` key (the `tool_result.render` field on
 * the frozen /query SSE contract, see docs/spec/agent-ui-query-sse-contract.md
 * §4.3) to the component that renders its `data` payload. RenderCard.tsx
 * resolves this map and falls back to UnsupportedCard for unknown keys.
 */

import type { ComponentType } from 'react';
import { EmailPreScanCard, isPreScanPayload } from '../email/EmailPreScanCard';
import { UnsupportedCard } from './UnsupportedCard';
import { TableCard } from './TableCard';
import { KeyValueCard } from './KeyValueCard';
import { ListCard } from './ListCard';
import { ImageCard } from './ImageCard';
import { DiffCard } from './DiffCard';

export type CardComponent = ComponentType<{ data: unknown }>;

/** Unwrap the `{ok, data}` envelope form, if present; otherwise pass the
 *  payload through unchanged. */
function resolveEmailPreScanPayload(data: unknown): unknown {
    if (data && typeof data === 'object' && (data as Record<string, unknown>).kind === 'email_pre_scan') {
        return data;
    }
    const enveloped = (data as { data?: unknown } | null | undefined)?.data;
    if (enveloped && typeof enveloped === 'object' && (enveloped as Record<string, unknown>).kind === 'email_pre_scan') {
        return enveloped;
    }
    return data;
}

/**
 * Adapter for the `email_pre_scan` render key. Contract-TARGET shapes only:
 * today's live wire still emits a {summary, success} stub for
 * `pre_scan_inbox` until #2109 lands the `result_data` population fix, so
 * this tolerates both the bare target shape and the `{ok, data}` envelope
 * without needing to change again once #2109 ships.
 */
function EmailPreScanAdapter({ data }: { data: unknown }) {
    const payload = resolveEmailPreScanPayload(data);
    if (!isPreScanPayload(payload)) {
        return <UnsupportedCard variant="invalid" render="email_pre_scan" data={data} />;
    }
    return <EmailPreScanCard payload={payload} />;
}

export const CARD_REGISTRY: Record<string, CardComponent> = {
    email_pre_scan: EmailPreScanAdapter,
    table: TableCard,
    key_value: KeyValueCard,
    list: ListCard,
    image: ImageCard,
    diff: DiffCard,
};
