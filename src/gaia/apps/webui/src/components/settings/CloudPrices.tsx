// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { formatPrice } from './cloudPrice';
import type { CloudPriceList } from '../../types';

interface Props {
    provider: string;
    limit?: number;
    /** With `onPick`, each row is a choice; null shows the provider's top pick as chosen. */
    picked?: string | null;
    onPick?: (model: string) => void;
    pickDisabled?: boolean;
    /** Told the row shown as chosen when nothing is picked (null with no priced rows), once per load. */
    onDefault?: (model: string | null) => void;
}

/** A provider's list prices, shown before it is connected or a model is picked. */
export function CloudPrices({ provider, limit, picked = null, onPick, pickDisabled = false, onDefault }: Props) {
    const t = useT();
    const [list, setList] = useState<CloudPriceList | null>(null);
    const [error, setError] = useState<string | null>(null);
    const defaultTo = useRef(onDefault);
    defaultTo.current = onDefault;

    useEffect(() => {
        let live = true;
        setList(null);
        setError(null);
        defaultTo.current?.(null);
        api.getCloudPrices(provider)
            .then((l) => {
                if (!live) return;
                setList(l);
                defaultTo.current?.(l.models[0]?.model ?? null);
            })
            .catch((err) => { if (live) setError(err instanceof Error ? err.message : String(err)); });
        return () => { live = false; };
    }, [provider]);

    if (error) return <p className="settings-error" role="alert">{t('settings.price.loadFailed', { reason: error })}</p>;
    if (!list) return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.price.loading')}</p>;
    if (list.models.length === 0) {
        return <p className="settings-hint cloud-prices-note">{t('settings.price.unknownSentence')} <bdi>{list.note}</bdi></p>;
    }
    const shown = limit ? list.models.slice(0, limit) : list.models;
    // The list comes ranked: its first row is the model GAIA uses unless another is picked.
    const chosen = picked ?? shown[0]?.model;
    const pickable = onPick !== undefined;
    return (
        <div className="cloud-prices" aria-label={t('settings.model.prices')}>
            {pickable && <p className="cloud-prices-legend" id="cloud-prices-legend">{t('settings.price.pickLegend')}</p>}
            <ul className={`cloud-prices-list${pickable ? ' is-pickable' : ''}`} role={pickable ? 'radiogroup' : undefined} aria-labelledby={pickable ? 'cloud-prices-legend' : undefined}>
                {shown.map((m, i) => {
                    const name = (
                        <span className="cloud-prices-name">
                            <bdi>{m.name}</bdi>
                            {i === 0 && pickable && <span className="cloud-prices-default"> · {t('settings.price.default')}</span>}
                            {m.note && <span className="cloud-prices-note"> · <bdi>{m.note}</bdi></span>}
                        </span>
                    );
                    return (
                        <li key={m.model} className={pickable && chosen === m.model ? 'is-chosen' : undefined}>
                            {pickable ? (
                                <label className="cloud-prices-pick">
                                    <input type="radio" name={`cloud-model-${provider}`} value={m.model} checked={chosen === m.model} disabled={pickDisabled} onChange={() => onPick(m.model)} />
                                    {name}
                                </label>
                            ) : name}
                            <span className="cloud-prices-price">{formatPrice(m.price, t)}</span>
                        </li>
                    );
                })}
            </ul>
            <span className="settings-hint"><bdi>{list.source}</bdi>. {t('settings.price.payProvider')}</span>
        </div>
    );
}
