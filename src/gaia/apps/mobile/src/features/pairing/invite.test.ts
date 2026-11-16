// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { OFFERED_NETWORKS } from './homeNetwork';
import { parseInvite, PairingInviteError, type PairingInvite } from './invite';

const NOW = 1_800_000_000;
const offer: PairingInvite = {
    v: 1,
    name: 'Office PC',
    pc_id: 'a1'.repeat(16),
    origins: ['https://192.168.1.20:4243'],
    pin: 'AB'.repeat(32),
    invite: 'abc123_-xyz',
    exp: NOW + 600,
};
const json = JSON.stringify(offer);
const b64url = Buffer.from(json).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const keyOf = (fn: () => unknown) => {
    try {
        fn();
    } catch (e) {
        if (e instanceof PairingInviteError) return e.messageKey;
        throw e;
    }
    return null;
};

describe('parseInvite', () => {
    it('reads the QR payload, a link carrying it, or its base64url form, into the same shape', () => {
        const want = { ...offer, pin: offer.pin.toLowerCase() };
        expect(parseInvite(json, NOW)).toEqual(want);
        expect(parseInvite(`  ${json}\n`, NOW)).toEqual(want);
        expect(parseInvite(`gaia://pair?offer=${encodeURIComponent(json)}`, NOW)).toEqual(want);
        expect(parseInvite(`https://pc.example/pair#${b64url}`, NOW)).toEqual(want);
        expect(parseInvite(b64url, NOW)).toEqual(want);
    });

    it('refuses what it cannot use with the message that says what to do', () => {
        expect(keyOf(() => parseInvite('  ', NOW))).toBe('mobileApp.pairing.invite.empty');
        expect(keyOf(() => parseInvite('hello there', NOW))).toBe('mobileApp.pairing.invite.unreadable');
        expect(keyOf(() => parseInvite(JSON.stringify({ ...offer, v: 2 }), NOW))).toBe('mobileApp.pairing.invite.version');
        expect(keyOf(() => parseInvite(JSON.stringify({ ...offer, pin: 'abc' }), NOW))).toBe('mobileApp.pairing.invite.incomplete');
        expect(keyOf(() => parseInvite(JSON.stringify({ ...offer, origins: [] }), NOW))).toBe('mobileApp.pairing.invite.incomplete');
        expect(keyOf(() => parseInvite(JSON.stringify({ ...offer, exp: NOW - 1 }), NOW))).toBe('mobileApp.pairing.invite.expired');
    });

    it('pairs only with an https IPv4 address on a home network, as the PC offers', () => {
        const withOrigins = (origins: string[]) => JSON.stringify({ ...offer, origins });
        const accepted = ['https://10.0.0.5:4243', 'https://172.31.255.1:4243', 'https://192.168.1.20:4243/'];
        expect(parseInvite(withOrigins(accepted), NOW).origins).toEqual(['https://10.0.0.5:4243', 'https://172.31.255.1:4243', 'https://192.168.1.20:4243']);
        const refused = [
            'http://192.168.1.20:4243',
            'https://8.8.8.8:4243',
            'https://172.32.0.1:4243',
            'https://169.254.1.1:4243',
            'https://127.0.0.1:4243',
            'https://gaia-pc.example.net:4243',
            'https://[fd00::1]:4243',
            'https://user@192.168.1.20:4243',
            'https://192.168.1.20:4243/api',
            'https://192.168.1.20:4243?x=1',
            'https://192.168.1.20:4243#x',
            'https://192.168.1.20',
            'https://192.168.1.020:4243',
            'https://192.168.1.256:4243',
            'https://192.168.1.20:65536',
            'https://3232235796:4243',
        ];
        for (const origin of refused) {
            expect([origin, keyOf(() => parseInvite(withOrigins(['https://192.168.1.20:4243', origin]), NOW))]).toEqual([origin, 'mobileApp.pairing.invite.notHomeNetwork']);
        }
    });

    it('names the same home networks as the PC and the native transport', () => {
        const cidrs = (source: string, decl: RegExp) => [...decl.exec(source)![1].matchAll(/"([\d./]+)"/g)].map((m) => m[1]);
        const gaia = resolve(__dirname, '../../../../..');
        const python = readFileSync(join(gaia, 'ui/devices/__init__.py'), 'utf-8');
        const swift = readFileSync(join(gaia, 'apps/mobile/modules/gaia-transport/ios/HomeNetwork.swift'), 'utf-8');
        expect(cidrs(python, /^OFFERED_NETWORKS = \(([^)]*)\)/m)).toEqual([...OFFERED_NETWORKS]);
        expect(cidrs(swift, /^private let offeredNetworks = \[([^\]]*)\]/m)).toEqual([...OFFERED_NETWORKS]);
    });
});
