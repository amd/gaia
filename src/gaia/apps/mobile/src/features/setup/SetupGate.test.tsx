// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import { Text } from 'react-native';

import { ThemeProvider } from '@/ui';

import { reopenSetup, SetupGate } from './SetupGate';

const mockOpen = jest.fn();

jest.mock('@/data', () => ({
    useGaiaClient: () => ({}),
    useSetupGate: () => ({ gate: { state: 'ready' }, recheck: jest.fn(), finish: jest.fn(), open: mockOpen }),
}));
jest.mock('@/features/pairing', () => ({ PinMismatchScreen: () => null, usePairAgain: () => ({ mutate: jest.fn(), isPending: false }) }));
jest.mock('./SetupScreen', () => ({ SetupScreen: () => null }));
jest.mock('./StartupScreen', () => ({ StartupScreen: () => null }));

describe('reopenSetup', () => {
    it('opens setup in re-run mode while the gate is mounted, and says so when it is not', async () => {
        expect(() => reopenSetup()).toThrow(/not mounted/);
        const { unmount } = await render(<SetupGate><Text>App</Text></SetupGate>, { wrapper: ThemeProvider });
        expect(screen.getByText('App')).toBeTruthy();
        reopenSetup();
        expect(mockOpen).toHaveBeenCalledTimes(1);
        await unmount();
        expect(() => reopenSetup()).toThrow(/not mounted/);
    });
});
