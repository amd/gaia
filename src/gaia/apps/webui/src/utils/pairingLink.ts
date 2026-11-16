// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { DevicePairingOffer } from '../services/devicesApi';

// The production phone app's scheme: `scheme: id` with APP_IDS.production in apps/mobile/app.config.ts (pairingLink.test.ts checks it).
export const PAIRING_LINK_SCHEME = 'ai.amd.gaia.mobile';

/** A link the phone's "Paste the pairing link or code" screen reads (apps/mobile pairing/invite.ts parseInvite). */
export function pairingLink(offer: DevicePairingOffer): string {
    let binary = '';
    for (const byte of new TextEncoder().encode(JSON.stringify(offer))) binary += String.fromCharCode(byte);
    const b64url = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return `${PAIRING_LINK_SCHEME}://pair?offer=${b64url}`;
}
