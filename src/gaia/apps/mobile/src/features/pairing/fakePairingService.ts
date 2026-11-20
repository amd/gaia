// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { PairingInvite } from './invite';
import type { PairedPc, PairingService, PairingStatus, RedeemResult } from './service';

export interface FakePairingOptions {
    /** What redeeming ends in. `paired` goes through `pending` first, as the PC makes the person choose Allow. */
    outcome?: 'paired' | 'refused' | 'unreachable';
    /** Status polls that answer `pending` before the outcome. */
    pendingPolls?: number;
}

/** A PairingService with no network, for development and tests. */
export function createFakePairingService({ outcome = 'paired', pendingPolls = 2 }: FakePairingOptions = {}): PairingService {
    let invite: PairingInvite | null = null;
    let polls = 0;
    let paired: PairedPc[] = [];

    const settled = (): RedeemResult => {
        if (outcome === 'refused') return { state: 'refused' };
        if (outcome === 'unreachable') return { state: 'unreachable' };
        const pc: PairedPc = { id: invite!.pc_id, name: invite!.name, connection: { baseUrl: invite!.origins[0] } };
        paired = [...paired.filter((p) => p.id !== pc.id), pc];
        return { state: 'paired', pc };
    };

    return {
        async redeem(next) {
            invite = next;
            polls = 0;
            if (outcome !== 'paired') {
                const result = settled();
                invite = null;
                return result;
            }
            return { state: 'pending', pcName: next.name, expiresAt: next.exp };
        },
        async status(): Promise<PairingStatus> {
            if (!invite) return { state: 'none' };
            if (++polls <= pendingPolls) return { state: 'pending', pcName: invite.name, expiresAt: invite.exp };
            return settled();
        },
        async cancel() {
            invite = null;
        },
        async list() {
            return paired;
        },
        async use(pcId) {
            if (!paired.some((p) => p.id === pcId)) throw new Error(`No paired PC has the id ${pcId}.`);
        },
        async forget(pcId) {
            paired = paired.filter((p) => p.id !== pcId);
        },
    };
}
