// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { MAX_ITEMS, isKeyValuePayload, scalarText, truncationLabel } from '../../utils/renderCards.core';

export function KeyValueCard({ data }: { data: unknown }) {
    if (!isKeyValuePayload(data)) {
        return <UnsupportedCard variant="invalid" render="key_value" data={data} />;
    }
    const truncated = data.items.length > MAX_ITEMS;
    const items = truncated ? data.items.slice(0, MAX_ITEMS) : data.items;
    return (
        <div className="render-kv">
            {data.title && <div className="render-kv__title">{data.title}</div>}
            <dl className="render-kv__list">
                {items.map((item, i) => (
                    <div className="render-kv__row" key={i}>
                        <dt className="render-kv__key">{item.key}</dt>
                        <dd className="render-kv__value">{scalarText(item.value)}</dd>
                    </div>
                ))}
            </dl>
            {truncated && (
                <div className="render-truncation">{truncationLabel(data.items.length)}</div>
            )}
        </div>
    );
}
