// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Regression tests for the notification center's store subscription.
 *
 * Opening the panel blanked the whole Agent UI: the visible-notifications
 * selector filters into a fresh array, and zustand v5 hands that straight to
 * useSyncExternalStore, which re-renders forever on an uncached snapshot
 * ("Maximum update depth exceeded", React #185). These tests mount the panel
 * for real so a selector that stops being cached crashes the suite, not a user.
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationCenter } from '../NotificationCenter';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';

const notif = (id: string, message: string): GaiaNotification => ({
    id,
    type: 'info',
    agentId: 'gaia',
    agentName: 'GAIA',
    title: 'Heads up',
    message,
    timestamp: Date.now(),
    read: false,
    dismissed: false,
    priority: 'medium',
});

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], typeFilter: null });
});

describe('NotificationCenter', () => {
    it('renders an empty panel without looping', () => {
        expect(() => render(<NotificationCenter onClose={vi.fn()} />)).not.toThrow();
        expect(screen.getByText('No notifications')).toBeTruthy();
    });

    it('renders notifications without looping', () => {
        useNotificationStore.setState({
            notifications: [notif('n-1', 'Model finished loading'), notif('n-2', 'Document indexed')],
        });
        expect(() => render(<NotificationCenter onClose={vi.fn()} />)).not.toThrow();
        expect(screen.getByText('Model finished loading')).toBeTruthy();
        expect(screen.getByText('Document indexed')).toBeTruthy();
    });

    it('hides dismissed notifications', () => {
        useNotificationStore.setState({
            notifications: [
                { ...notif('n-1', 'Still here'), dismissed: false },
                { ...notif('n-2', 'Gone'), dismissed: true },
            ],
        });
        render(<NotificationCenter onClose={vi.fn()} />);
        expect(screen.getByText('Still here')).toBeTruthy();
        expect(screen.queryByText('Gone')).toBeNull();
    });
});
