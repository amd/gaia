// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { MAX_ITEMS, isListPayload, truncationLabel } from '../../utils/renderCards.core';

export function ListCard({ data }: { data: unknown }) {
    if (!isListPayload(data)) {
        return <UnsupportedCard variant="invalid" render="list" data={data} />;
    }
    const truncated = data.items.length > MAX_ITEMS;
    const items = truncated ? data.items.slice(0, MAX_ITEMS) : data.items;
    const entries = items.map((item, i) => <li key={i}>{String(item)}</li>);
    return (
        <div className="render-list">
            {data.title && <div className="render-list__title">{data.title}</div>}
            {data.ordered ? (
                <ol className="render-list__items">{entries}</ol>
            ) : (
                <ul className="render-list__items">{entries}</ul>
            )}
            {truncated && (
                <div className="render-truncation">{truncationLabel(data.items.length)}</div>
            )}
        </div>
    );
}
