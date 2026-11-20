// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';

import type { PairingInvite } from './invite';
import { createPinnedPairingService } from './pinnedPairingService';

const mockVault = new Map<string, string>();
let mockHealth = 403;

jest.mock('expo-device', () => ({ deviceName: 'Test phone', modelName: 'iPhone' }));
jest.mock('../../../modules/gaia-transport', () => ({
    transportAvailable: true,
    GaiaTransport: {
        pair: async (vaultId: string, origins: string[]) => {
            mockVault.set(vaultId, `secret from ${origins[0]}`);
            return { status: 200, origin: origins[0], body: JSON.stringify({ device_id: 'dev-2', pc_name: 'Office PC' }) };
        },
        dropAll: async () => void mockVault.clear(),
        drop: async (vaultId: string) => void mockVault.delete(vaultId),
        promote: async (from: string, to: string) => {
            mockVault.set(to, mockVault.get(from)!);
            mockVault.delete(from);
        },
        isPaired: (vaultId: string) => mockVault.has(vaultId),
    },
}));
jest.mock('@/platform/pinnedFetch', () => ({
    onDeviceGone: () => undefined,
    transportError: (err: unknown) => err,
    pinnedFetch: async (vaultId: string) => {
        if (!mockVault.has(vaultId)) throw Object.assign(new Error('not paired'), { code: 'not_paired' });
        const body = mockHealth === 403 ? JSON.stringify({ code: 'device_pending' }) : '{}';
        return { ok: mockHealth === 200, status: mockHealth, text: async () => body };
    },
}));

const PC_ID = 'a1'.repeat(16);
const invite: PairingInvite = {
    v: 1,
    name: 'Office PC',
    pc_id: PC_ID,
    origins: ['https://192.168.1.99:4243'],
    pin: 'ab'.repeat(32),
    invite: 'abc',
    exp: Date.now() / 1000 + 600,
};

beforeEach(async () => {
    await AsyncStorage.removeItem('gaia:install-marker');
    mockVault.clear();
    mockVault.set(PC_ID, 'working secret');
    await AsyncStorage.setItem(
        'gaia:paired-pcs',
        JSON.stringify({ pcs: [{ id: PC_ID, name: 'Office PC', origin: 'https://192.168.1.20:4243', deviceId: 'dev-1' }], currentId: PC_ID, pending: null }),
    );
});

describe('re-pairing a PC this phone is already paired with', () => {
    it('keeps the working secret until the PC allows the new one, and keeps it when the PC refuses', async () => {
        const service = createPinnedPairingService();
        mockHealth = 403;
        expect((await service.redeem(invite)).state).toBe('pending');
        expect((await service.status()).state).toBe('pending');
        expect(mockVault.get(PC_ID)).toBe('working secret');

        mockHealth = 401;
        expect((await service.status()).state).toBe('refused');
        expect([...mockVault.entries()]).toEqual([[PC_ID, 'working secret']]);
        expect((await service.list()).map((pc) => pc.connection.baseUrl)).toEqual(['https://192.168.1.20:4243']);
    });

    it('swaps in the new secret and address once the PC allows it', async () => {
        const service = createPinnedPairingService();
        mockHealth = 403;
        await service.redeem(invite);
        mockHealth = 200;
        expect((await service.status()).state).toBe('paired');
        expect([...mockVault.entries()]).toEqual([[PC_ID, 'secret from https://192.168.1.99:4243']]);
        expect((await service.list()).map((pc) => pc.connection.baseUrl)).toEqual(['https://192.168.1.99:4243']);
    });
});

describe('a reinstalled app', () => {
    it('wipes the old install\'s secrets once, and leaves them alone after that', async () => {
        await AsyncStorage.removeItem('gaia:paired-pcs');
        const service = createPinnedPairingService();
        expect(await service.restore()).toBeNull();
        expect(mockVault.size).toBe(0);

        mockVault.set(PC_ID, 'paired after the reinstall');
        await createPinnedPairingService().restore();
        expect(mockVault.get(PC_ID)).toBe('paired after the reinstall');
    });

    it('keeps the secrets of an app updated from a build without the marker', async () => {
        await createPinnedPairingService().restore();
        expect(mockVault.get(PC_ID)).toBe('working secret');
    });
});
