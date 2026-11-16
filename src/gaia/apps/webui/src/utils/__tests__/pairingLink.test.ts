// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { transformWithOxc } from 'vite';
import type { DevicePairingOffer } from '../../services/devicesApi';
import { PAIRING_LINK_SCHEME, pairingLink } from '../pairingLink';

const MOBILE = resolve(__dirname, '../../../../mobile');

async function compile(file: string): Promise<string> {
    const source = readFileSync(resolve(MOBILE, 'src/features/pairing', file), 'utf8');
    const { code } = await transformWithOxc(source, file, { lang: 'ts', tsconfig: false, target: 'es2020' });
    return code.replace(/^export /gm, '');
}

/** The phone app's real parseInvite, compiled here: its tsconfig extends Expo's, which webui doesn't install. */
async function loadParseInvite(): Promise<(text: string, nowSeconds: number) => unknown> {
    const homeNetwork = await compile('homeNetwork.ts');
    const stubbed = (await compile('invite.ts'))
        .replace(/^import \{ GaiaError \} from ['"]@\/api\/errors['"];$/m, 'class GaiaError extends Error {}')
        .replace(/^import \{ homeNetworkOrigin \} from ['"]\.\/homeNetwork['"];$/m, () => homeNetwork);
    if (stubbed.includes('import ')) throw new Error('invite.ts gained an import; load it in this test');
    return new Function(`${stubbed}\nreturn parseInvite;`)();
}

const OFFER: DevicePairingOffer = {
    v: 1, name: 'مكتب Zoë 🖥', pc_id: '0123456789abcdef'.repeat(2), origins: ['https://192.168.1.10:4243'],
    pin: 'ab'.repeat(32), invite: 'invitation', exp: 2_000_000_000,
};

describe('pairingLink', () => {
    it('round-trips through the phone app parseInvite, including a non-ASCII PC name', async () => {
        const parseInvite = await loadParseInvite();
        expect(parseInvite(pairingLink(OFFER), 1_900_000_000)).toEqual(OFFER);
    });

    it('is unpadded base64url in the offer parameter', () => {
        const link = pairingLink(OFFER);
        expect(link.startsWith(`${PAIRING_LINK_SCHEME}://pair?offer=`)).toBe(true);
        expect(link.split('offer=')[1]).toMatch(/^[A-Za-z0-9_-]+$/);
    });

    it("uses the phone app's production scheme", () => {
        const config = readFileSync(resolve(MOBILE, 'app.config.ts'), 'utf8');
        expect(config).toContain(`production: '${PAIRING_LINK_SCHEME}'`);
    });
});
