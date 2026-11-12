// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { CONNECTOR_LOGOS } from '../utils/connectorLogos';

/** Marks darker than this vanish on the dark theme, so they take the text colour instead. */
const DARK_MARK_LUMA = 60;

function luma(hex: string): number {
    const n = parseInt(hex.slice(1), 16);
    return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
}

/** The service's mark, or a lettered badge when GAIA ships none for it. Decorative: the name sits beside it. */
export function ConnectorLogo({ id, name }: { id: string; name: string }) {
    const logo = CONNECTOR_LOGOS[id];
    if (!logo) {
        return (
            <span className="connector-logo connector-logo--letter" aria-hidden="true">
                {name.trim().charAt(0).toUpperCase()}
            </span>
        );
    }
    return (
        <span className="connector-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18" focusable="false">
                <path d={logo.path} fill={luma(logo.hex) < DARK_MARK_LUMA ? 'currentColor' : logo.hex} />
            </svg>
        </span>
    );
}
