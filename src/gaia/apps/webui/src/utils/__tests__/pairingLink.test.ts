// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import type { DevicePairingOffer } from '../../services/devicesApi';
import { PAIRING_LINK_SCHEME, pairingLink } from '../pairingLink';

const OFFER: DevicePairingOffer = {
    v: 1, name: 'مكتب Zoë 🖥', pc_id: '0123456789abcdef'.repeat(2), origins: ['https://192.168.1.10:4243'],
    pin: 'ab'.repeat(32), invite: 'invitation', exp: 2_000_000_000,
};

function decodeOffer(link: string): unknown {
    const b64 = link.split('offer=')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, '=')), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
}

describe('pairingLink', () => {
    it('round-trips the offer as UTF-8 JSON, including a non-ASCII PC name', () => {
        expect(decodeOffer(pairingLink(OFFER))).toEqual(OFFER);
    });

    it('is unpadded base64url in the offer parameter', () => {
        const link = pairingLink(OFFER);
        expect(link.startsWith(`${PAIRING_LINK_SCHEME}://pair?offer=`)).toBe(true);
        expect(link.split('offer=')[1]).toMatch(/^[A-Za-z0-9_-]+$/);
    });
});
