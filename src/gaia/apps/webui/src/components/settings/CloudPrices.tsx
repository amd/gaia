// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { formatPrice } from './cloudPrice';
import type { CloudPriceList } from '../../types';

/** A provider's list prices, shown before it is connected or a model is picked. */
export function CloudPrices({ provider, limit }: { provider: string; limit?: number }) {
    const [list, setList] = useState<CloudPriceList | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setList(null);
        setError(null);
        api.getCloudPrices(provider)
            .then(setList)
            .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    }, [provider]);

    if (error) return <p className="settings-error" role="alert">Prices could not be loaded: {error}</p>;
    if (!list) return <p className="settings-hint"><Loader2 size={13} className="spin" /> Loading prices…</p>;
    if (list.models.length === 0) {
        return <p className="settings-hint cloud-prices-note">Price unknown. {list.note}</p>;
    }
    const shown = limit ? list.models.slice(0, limit) : list.models;
    return (
        <div className="cloud-prices" aria-label="Prices">
            <ul className="cloud-prices-list">
                {shown.map((m) => (
                    <li key={m.model}>
                        <span className="cloud-prices-name">
                            {m.name}
                            {m.note && <span className="cloud-prices-note"> · {m.note}</span>}
                        </span>
                        <span className="cloud-prices-price">{formatPrice(m.price)}</span>
                    </li>
                ))}
            </ul>
            <span className="settings-hint">{list.source}. You pay the provider for what you use.</span>
        </div>
    );
}
