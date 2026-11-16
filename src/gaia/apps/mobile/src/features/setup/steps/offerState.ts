// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LemonadeFound, LemonadeTarget } from '@/api/client.types';

/** The statuses webui's `ExistingLemonadeOffer` (SetupScreen.tsx) shows a choice for. */
const OFFERED: readonly LemonadeFound['status'][] = ['ok', 'needs_key', 'too_old'];

export type OfferState =
    | { kind: 'looking' }
    /** Nothing to choose: setup carries on and installs GAIA's own. */
    | { kind: 'none' }
    | { kind: 'error'; error: unknown }
    /** Something took the connection but didn't answer: the PC decides, GAIA installs nothing beside it. */
    | { kind: 'noAnswer'; urls: string[] }
    | { kind: 'tooOld'; found: LemonadeFound }
    | { kind: 'offer'; found: LemonadeFound; needsKey: boolean }
    | { kind: 'chosen'; mode: 'external' | 'gaia'; url: string | null };

export interface OfferInputs {
    target: LemonadeTarget | undefined;
    targetError: unknown;
    detect: { found: LemonadeFound | null; unchecked?: string[] } | undefined;
    detectError: unknown;
    /** An offer was on screen, so a target with no offer left means the PC chose. */
    wasOffered: boolean;
}

/** What "This PC already runs a model server" shows, as webui's `detect` decides it. */
export function offerState({ target, targetError, detect, detectError, wasOffered }: OfferInputs): OfferState {
    if (targetError) return { kind: 'error', error: targetError };
    if (!target) return { kind: 'looking' };
    if (!target.offer) {
        return wasOffered ? { kind: 'chosen', mode: target.mode, url: target.base_url } : { kind: 'none' };
    }
    if (detectError) return { kind: 'error', error: detectError };
    if (!detect) return { kind: 'looking' };
    const { found, unchecked = [] } = detect;
    if (found && OFFERED.includes(found.status)) {
        return found.status === 'too_old'
            ? { kind: 'tooOld', found }
            : { kind: 'offer', found, needsKey: found.status === 'needs_key' };
    }
    return unchecked.length > 0 && !found ? { kind: 'noAnswer', urls: unchecked } : { kind: 'none' };
}

/** The offer is out of the way: setup may start GAIA's own server. */
export const offerSettled = (s: OfferState): boolean => s.kind === 'none' || s.kind === 'tooOld' || s.kind === 'chosen';
