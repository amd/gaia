// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConnectorStep } from '../ConnectorStep';
import type { ConnectorRow } from '../../../types';

vi.mock('../../../services/api', () => ({
    listConnectors: vi.fn(),
    getConnector: vi.fn(),
    authorizeConnector: vi.fn(),
}));

import * as api from '../../../services/api';

function google(over: Partial<ConnectorRow>): ConnectorRow {
    return {
        id: 'google',
        display_name: 'Google',
        description: 'Connect GAIA to your Google account.',
        configured: false,
        configurable: true,
        config_error: null,
        ...over,
    } as ConnectorRow;
}

describe('ConnectorStep', () => {
    it('tells a new user Google needs setup, without developer instructions', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [google({
                configurable: false,
                config_error:
                    "OAuth credentials for 'google' are not configured. Set GAIA_GOOGLE_CLIENT_ID and GAIA_GOOGLE_CLIENT_SECRET.",
            })],
        } as Awaited<ReturnType<typeof api.listConnectors>>);
        render(<ConnectorStep />);

        expect(await screen.findByTestId('connector-setup-note')).toHaveTextContent(
            'Settings → Connectors',
        );
        expect(screen.queryByText(/GAIA_GOOGLE_CLIENT_ID/)).toBeNull();
        expect(screen.queryByTestId('connector-connect')).toBeNull();
    });

    it('offers Connect when the connector is ready to authorize', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [google({})],
        } as Awaited<ReturnType<typeof api.listConnectors>>);
        render(<ConnectorStep />);

        expect(await screen.findByTestId('connector-connect')).toBeEnabled();
        expect(screen.queryByTestId('connector-setup-note')).toBeNull();
    });
});
