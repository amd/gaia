// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaError } from '@/api/errors';

import { homeNetworkOrigin } from './homeNetwork';

/** What the PC's pairing QR code carries (the PC's DevicePairingOffer). */
export interface PairingInvite {
    v: number;
    name: string;
    pc_id: string;
    origins: string[];
    pin: string;
    invite: string;
    exp: number;
}

export const PAIRING_OFFER_VERSION = 1;

const PIN_HEX = /^[0-9a-f]{64}$/i;
// The PC's id is 16 random bytes in hex (gaia/ui/devices/store.py).
const PC_ID = /^[0-9a-f]{32}$/;
// The PC's own limit for a device name (sanitize_device_name).
const NAME_MAX = 64;
// Control and bidi-override characters, which could disguise the name the phone asks about.
const HIDDEN = /[\u0000-\u001f\u007f-\u009f\u061c\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g;
// The PC allows invitations up to 64 characters.
const INVITE_MAX = 64;

export class PairingInviteError extends GaiaError {}

/** The JSON text inside `text`: itself, a URL parameter or fragment holding it, or base64url of it. */
function candidates(text: string): string[] {
    const out = [text];
    const hash = text.indexOf('#');
    const query = text.indexOf('?');
    const tail = [query >= 0 ? text.slice(query + 1, hash > query ? hash : undefined) : '', hash >= 0 ? text.slice(hash + 1) : ''];
    for (const part of tail) {
        const params = new URLSearchParams(part);
        for (const key of ['offer', 'code', 'invite']) {
            const v = params.get(key);
            if (v) out.push(v);
        }
        if (part && !part.includes('=')) out.push(part);
    }
    return out;
}

function decode(candidate: string): unknown {
    const trimmed = candidate.trim();
    if (trimmed.startsWith('{')) return JSON.parse(trimmed);
    const b64 = trimmed.replace(/-/g, '+').replace(/_/g, '/');
    if (!/^[A-Za-z0-9+/]+=*$/.test(b64)) throw new SyntaxError('not base64');
    const bytes = atob(b64);
    return JSON.parse(decodeURIComponent(bytes.split('').map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
}

function parsedJson(text: string): Record<string, unknown> | null {
    for (const c of candidates(text.trim())) {
        try {
            const value = decode(c);
            if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
        } catch {
            // Not this candidate; the next may be the code.
        }
    }
    return null;
}

const incomplete = (field: string) => new PairingInviteError('mobileApp.pairing.invite.incomplete', { field });

/**
 * The invitation in a pasted pairing link or code, in the shape the QR carries.
 * Throws PairingInviteError (a catalog message) when it can't be used.
 */
export function parseInvite(text: string, nowSeconds: number = Date.now() / 1000): PairingInvite {
    if (!text.trim()) throw new PairingInviteError('mobileApp.pairing.invite.empty');
    const raw = parsedJson(text);
    if (!raw) throw new PairingInviteError('mobileApp.pairing.invite.unreadable');
    if (raw.v !== PAIRING_OFFER_VERSION) {
        if (typeof raw.v === 'number') throw new PairingInviteError('mobileApp.pairing.invite.version');
        throw incomplete('v');
    }
    const name = typeof raw.name === 'string' ? raw.name.replace(/\s+/g, ' ').replace(HIDDEN, '').trim().slice(0, NAME_MAX).trim() : '';
    if (!name) throw incomplete('name');
    if (typeof raw.pc_id !== 'string' || !PC_ID.test(raw.pc_id)) throw incomplete('pc_id');
    if (!Array.isArray(raw.origins) || raw.origins.length === 0 || !raw.origins.every((o) => typeof o === 'string')) throw incomplete('origins');
    if (typeof raw.pin !== 'string' || !PIN_HEX.test(raw.pin)) throw incomplete('pin');
    if (typeof raw.invite !== 'string' || !raw.invite || raw.invite.length > INVITE_MAX) throw incomplete('invite');
    if (typeof raw.exp !== 'number' || !Number.isFinite(raw.exp)) throw incomplete('exp');
    // The phone pins whatever certificate the named address shows, so only a home-network address will do.
    const origins = (raw.origins as string[]).map(homeNetworkOrigin);
    if (origins.some((o) => o === null)) throw new PairingInviteError('mobileApp.pairing.invite.notHomeNetwork');
    if (raw.exp <= nowSeconds) throw new PairingInviteError('mobileApp.pairing.invite.expired');
    return {
        v: raw.v,
        name,
        pc_id: raw.pc_id,
        origins: origins as string[],
        pin: raw.pin.toLowerCase(),
        invite: raw.invite,
        exp: raw.exp,
    };
}
