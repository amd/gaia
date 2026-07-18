// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { referencedProviderConnected } from '../ConnectorRetryBanner';
import type { ConnectorRow } from '../../types';

function connector(id: string, configured: boolean): ConnectorRow {
    return {
        id,
        display_name: id,
        icon: null,
        category: 'email',
        tier: 'verified',
        type: 'oauth_pkce',
        description: '',
        product_url: null,
        docs_url: null,
        configured,
        configurable: true,
        config_error: null,
        enabled: true,
        account_id: configured ? 'acct' : null,
        scopes: [],
        activations: {},
        last_tested_at: null,
        mcp_env_keys: [],
        default_scopes: [],
        available_scopes: [],
        oauth_setup_fields: [],
    };
}

const GOOGLE_ERR = 'NOT_CONNECTED: google is not currently connected. Connect it in Settings → Connectors → Google.';
const MS_ERR = 'NOT_CONNECTED: microsoft is not currently connected.';

describe('referencedProviderConnected', () => {
    it('is false when the referenced Google account is still not connected', () => {
        expect(referencedProviderConnected(GOOGLE_ERR, [connector('google', false)])).toBe(false);
    });

    it('is true once the referenced Google account is connected', () => {
        expect(referencedProviderConnected(GOOGLE_ERR, [connector('google', true)])).toBe(true);
    });

    it('does not fire for Google when only Microsoft got connected', () => {
        expect(
            referencedProviderConnected(GOOGLE_ERR, [connector('google', false), connector('microsoft', true)])
        ).toBe(false);
    });

    it('fires for a Microsoft error once Microsoft is connected', () => {
        expect(referencedProviderConnected(MS_ERR, [connector('microsoft', true)])).toBe(true);
    });

    it('for an ambiguous error, either provider connecting is enough', () => {
        const ambiguous = 'AUTH_REQUIRED: connect an email account to continue.';
        expect(referencedProviderConnected(ambiguous, [connector('google', true)])).toBe(true);
        expect(referencedProviderConnected(ambiguous, [connector('microsoft', true)])).toBe(true);
        expect(referencedProviderConnected(ambiguous, [connector('google', false)])).toBe(false);
    });
});
