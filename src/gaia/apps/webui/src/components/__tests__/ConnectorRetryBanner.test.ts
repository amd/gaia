// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { referencedProviderConnected, connectedProviderLabel } from '../ConnectorRetryBanner';
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
const MS_WORK_ERR = 'NOT_CONNECTED: microsoft_work is not currently connected.';
const AMBIGUOUS_ERR = 'AUTH_REQUIRED: connect an email account to continue.';

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

    it('fires for a work Microsoft 365 error once microsoft_work is connected', () => {
        expect(referencedProviderConnected(MS_WORK_ERR, [connector('microsoft_work', true)])).toBe(true);
    });

    it('does not fire for a work Microsoft 365 error when only personal Microsoft got connected', () => {
        expect(
            referencedProviderConnected(MS_WORK_ERR, [
                connector('microsoft', true),
                connector('microsoft_work', false),
            ])
        ).toBe(false);
    });

    it('does not fire for a work Microsoft 365 error when only Google got connected', () => {
        expect(
            referencedProviderConnected(MS_WORK_ERR, [connector('google', true), connector('microsoft_work', false)])
        ).toBe(false);
    });

    it('for an ambiguous error, any of the three providers connecting is enough', () => {
        expect(referencedProviderConnected(AMBIGUOUS_ERR, [connector('google', true)])).toBe(true);
        expect(referencedProviderConnected(AMBIGUOUS_ERR, [connector('microsoft', true)])).toBe(true);
        expect(referencedProviderConnected(AMBIGUOUS_ERR, [connector('microsoft_work', true)])).toBe(true);
        expect(referencedProviderConnected(AMBIGUOUS_ERR, [connector('google', false)])).toBe(false);
    });
});

describe('connectedProviderLabel', () => {
    it('labels an unambiguous Google message', () => {
        expect(connectedProviderLabel(GOOGLE_ERR, [connector('google', true)])).toBe('Google');
    });

    it('labels an unambiguous personal Microsoft message', () => {
        expect(connectedProviderLabel(MS_ERR, [connector('microsoft', true)])).toBe('Microsoft');
    });

    it('labels an unambiguous work Microsoft 365 message', () => {
        expect(connectedProviderLabel(MS_WORK_ERR, [connector('microsoft_work', true)])).toBe('Microsoft 365');
    });

    it('for an ambiguous message, prefers Google when Google is configured', () => {
        expect(
            connectedProviderLabel(AMBIGUOUS_ERR, [
                connector('google', true),
                connector('microsoft', true),
                connector('microsoft_work', true),
            ])
        ).toBe('Google');
    });

    it('for an ambiguous message, falls back to Microsoft when only Microsoft is configured', () => {
        expect(
            connectedProviderLabel(AMBIGUOUS_ERR, [
                connector('google', false),
                connector('microsoft', true),
                connector('microsoft_work', true),
            ])
        ).toBe('Microsoft');
    });

    it('for an ambiguous message, falls back to Microsoft 365 when only microsoft_work is configured', () => {
        expect(
            connectedProviderLabel(AMBIGUOUS_ERR, [
                connector('google', false),
                connector('microsoft', false),
                connector('microsoft_work', true),
            ])
        ).toBe('Microsoft 365');
    });
});
