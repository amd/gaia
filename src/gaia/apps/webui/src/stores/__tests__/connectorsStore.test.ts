// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach } from 'vitest';
import { useConnectionsStore } from '../connectorsStore';
import type { ConnectorInfo } from '../../types';

const conn = (provider: string): ConnectorInfo => ({
    provider,
    account_email: `test@${provider}.com`,
    scopes: [],
    connected_at: null,
});

describe('connectorsStore — pendingMailProvider clearing', () => {
    beforeEach(() => {
        useConnectionsStore.setState({
            connections: [],
            grants: {},
            loading: false,
            error: null,
            pendingMailProvider: undefined,
        });
    });

    describe('removeConnection', () => {
        it('clears pendingMailProvider when the removed provider matches', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().removeConnection('google');
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });

        it('preserves pendingMailProvider when a different provider is removed', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().removeConnection('microsoft');
            expect(useConnectionsStore.getState().pendingMailProvider).toBe('google');
        });

        it('is a no-op on pendingMailProvider when it is already undefined', () => {
            useConnectionsStore.setState({
                connections: [conn('google')],
                pendingMailProvider: undefined,
            });
            useConnectionsStore.getState().removeConnection('google');
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });
    });

    describe('setConnections', () => {
        it('clears pendingMailProvider when that provider is no longer in the new connection list', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'microsoft',
            });
            // Simulate a SSE bulk refresh that drops microsoft
            useConnectionsStore.getState().setConnections([conn('google')]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });

        it('preserves pendingMailProvider when the provider is still connected after refresh', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().setConnections([conn('google')]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBe('google');
        });

        it('preserves undefined pendingMailProvider across any refresh', () => {
            useConnectionsStore.setState({
                connections: [conn('google')],
                pendingMailProvider: undefined,
            });
            useConnectionsStore.getState().setConnections([]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });
    });
});
