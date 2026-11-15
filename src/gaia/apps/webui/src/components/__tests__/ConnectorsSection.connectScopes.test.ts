// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What Settings > Connectors > Connect asks the provider for.

import { describe, expect, it } from 'vitest';
import { connectScopes } from '../ConnectorsSection';

const GOOGLE = {
    default_scopes: ['openid', 'email'],
    available_scopes: [
        'openid',
        'email',
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.modify',
        'https://www.googleapis.com/auth/drive.readonly',
    ],
};

describe('connectScopes', () => {
    it('sends nothing when it grants GAIA, so the backend asks only for what GAIA declares', () => {
        expect(connectScopes(GOOGLE, ['installed:gaia'])).toEqual([]);
    });

    it('keeps the connector list when no agent is granted', () => {
        expect(connectScopes(GOOGLE, [])).toEqual(GOOGLE.available_scopes);
        expect(connectScopes({ ...GOOGLE, available_scopes: [] }, [])).toEqual(GOOGLE.default_scopes);
    });
});
