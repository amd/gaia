// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

import { ENDPOINTS, UI_HEADERS } from '@/api/constants';
import { GaiaApiError, GaiaPinMismatchError, parseErrorDetail } from '@/api/errors';
import { onDeviceGone, pinnedFetch, transportError } from '@/platform/pinnedFetch';
import { useConnectionStore, type ConnectionConfig } from '@/state/connection';

import { GaiaTransport, transportAvailable } from '../../../modules/gaia-transport';
import { PairingInviteError, type PairingInvite } from './invite';
import { setPairingService } from './registry';
import type { PairedPc, PairingService, PairingStatus, RedeemResult } from './service';

// Only what isn't secret; each PC's token and pin live in the Keychain, under its id.
const STORAGE_KEY = 'gaia:paired-pcs';
// Set once this install has run. AsyncStorage is wiped on uninstall but the Keychain is not.
const INSTALL_MARKER_KEY = 'gaia:install-marker';
// The route a paired device unpairs itself with (gaia/ui/devices SELF_ROUTE).
const SELF_ROUTE = '/api/devices/me';

/** Where a redeemed code's secret waits for Allow, so a PC already paired keeps its own until then. */
const stagedId = (pcId: string) => `${pcId}.pending`;

interface SavedPc {
    id: string;
    name: string;
    origin: string;
    deviceId: string;
}

interface Saved {
    pcs: SavedPc[];
    currentId: string | null;
    /** Redeemed, and waiting for Allow on the PC. */
    pending: (SavedPc & { expiresAt?: number }) | null;
}

interface PairAnswer {
    device_id: string;
    pc_name: string;
    expires_at?: string;
}

function connectionOf(pc: SavedPc): ConnectionConfig {
    return { baseUrl: pc.origin, pcId: pc.id, pinned: true };
}

function pairedOf(pc: SavedPc): PairedPc {
    return { id: pc.id, name: pc.name, connection: connectionOf(pc) };
}

function devicePlatform(): 'ios' | 'ipados' | 'android' {
    if (Platform.OS === 'android') return 'android';
    return Platform.OS === 'ios' && Platform.isPad ? 'ipados' : 'ios';
}

/** The name the PC lists this phone under: the one its owner gave it, else its model. */
function deviceName(): string {
    const name = Device.deviceName ?? Device.modelName;
    if (!name) throw new Error("This phone reports neither a name nor a model, so the PC would have nothing to list it under.");
    return name;
}

const NO_ANSWER = new Set(['unreachable', 'not_home_network', 'network']);

/** The PairingService of a phone build: everything over TLS pinned to the PC, the token kept natively. */
export function createPinnedPairingService(): PairingService & { restore(): Promise<ConnectionConfig | null>; deviceGone(pcId: string): Promise<void> } {
    let saved: Saved | null = null;

    async function load(): Promise<Saved> {
        if (!saved) {
            const raw = await AsyncStorage.getItem(STORAGE_KEY);
            saved = raw ? (JSON.parse(raw) as Saved) : { pcs: [], currentId: null, pending: null };
        }
        return saved;
    }

    async function save(next: Saved): Promise<void> {
        saved = next;
        await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    }

    /** The PC's answer to this phone's token: allowed, still pending, or unknown (unpaired, or never allowed in time). */
    async function check(pc: SavedPc): Promise<'allowed' | 'pending' | 'unknown'> {
        const res = await pinnedFetch(stagedId(pc.id), `${pc.origin}${ENDPOINTS.health}`, { headers: { ...UI_HEADERS } });
        const text = await res.text();
        if (res.ok) return 'allowed';
        if (res.status === 401) return 'unknown';
        if (res.status === 403 && (JSON.parse(text) as { code?: string }).code === 'device_pending') return 'pending';
        throw new GaiaApiError(res.status, parseErrorDetail(text));
    }

    /** Tells the PC to forget this phone; a 401 means it already had. */
    async function unpairOnPc(pc: SavedPc, vaultId: string = pc.id): Promise<void> {
        const res = await pinnedFetch(vaultId, `${pc.origin}${SELF_ROUTE}`, { method: 'DELETE', headers: { ...UI_HEADERS } });
        const text = await res.text();
        // 401: the PC no longer knows this phone.
        if (!res.ok && res.status !== 401) throw new GaiaApiError(res.status, parseErrorDetail(text));
    }

    /** A reinstalled app finds the old install's secrets in the Keychain; a fresh install has no marker and no saved PCs. */
    async function wipeKeychainOnReinstall(): Promise<void> {
        if ((await AsyncStorage.getItem(INSTALL_MARKER_KEY)) !== null) return;
        // An app updated from a build without the marker still has its saved PCs, so its secrets are its own.
        if ((await AsyncStorage.getItem(STORAGE_KEY)) === null) await GaiaTransport.dropAll();
        await AsyncStorage.setItem(INSTALL_MARKER_KEY, new Date().toISOString());
    }

    return {
        /** The saved current PC's connection; null when there is none, or its token is no longer in the Keychain. */
        async restore() {
            await wipeKeychainOnReinstall();
            const state = await load();
            const current = state.pcs.find((p) => p.id === state.currentId);
            if (!current) return null;
            if (!GaiaTransport.isPaired(current.id)) {
                console.warn(`${current.name}'s key is no longer on this phone, so it has to be paired again.`);
                await save({ ...state, pcs: state.pcs.filter((p) => p.id !== current.id), currentId: null });
                return null;
            }
            return connectionOf(current);
        },

        /** The PC refused this phone's token: drop it, and go back to pairing when it was the one in use. */
        async deviceGone(pcId) {
            const state = await load();
            const pc = state.pcs.find((p) => p.id === pcId);
            if (!pc) return;
            await GaiaTransport.drop(pcId);
            await save({ ...state, pcs: state.pcs.filter((p) => p.id !== pcId), currentId: state.currentId === pcId ? null : state.currentId });
            const connection = useConnectionStore.getState();
            if (connection.connection?.pcId === pcId) connection.connectionRemoved(pc.name);
        },

        async redeem(invite: PairingInvite): Promise<RedeemResult> {
            const state = await load();
            const body = JSON.stringify({ invitation: invite.invite, device_name: deviceName(), platform: devicePlatform() });
            await GaiaTransport.drop(stagedId(invite.pc_id));
            let answer;
            try {
                answer = await GaiaTransport.pair(stagedId(invite.pc_id), invite.origins, invite.pin, { ...UI_HEADERS, 'Content-Type': 'application/json' }, body);
            } catch (err) {
                const e = transportError(err);
                const code = String((e as { code?: unknown }).code);
                if (code === 'not_home_origin') throw new PairingInviteError('mobileApp.pairing.invite.notHomeNetwork', undefined, { cause: err });
                if (NO_ANSWER.has(code)) return { state: 'unreachable' };
                throw e;
            }
            if (answer.status === 403) return { state: 'refused' };
            if (answer.status === 429) return { state: 'refused', rateLimited: true };
            if (answer.status !== 200) throw new GaiaApiError(answer.status, parseErrorDetail(answer.body));
            const paired = JSON.parse(answer.body) as PairAnswer;
            const expiresAt = paired.expires_at ? Date.parse(paired.expires_at) / 1000 : undefined;
            const pending = { id: invite.pc_id, name: paired.pc_name || invite.name, origin: answer.origin, deviceId: paired.device_id, expiresAt };
            // A PC already paired keeps its saved token, and stays the one in use, until this one is allowed.
            await save({ ...state, pending });
            return { state: 'pending', pcName: pending.name, expiresAt };
        },

        async status(): Promise<PairingStatus> {
            const state = await load();
            const { pending } = state;
            if (!pending) return { state: 'none' };
            let answer;
            try {
                answer = await check(pending);
            } catch (err) {
                if (err instanceof GaiaPinMismatchError || err instanceof GaiaApiError) throw err;
                const code = String((err as { code?: unknown }).code);
                if (NO_ANSWER.has(code)) return { state: 'unreachable' };
                // The new key isn't on this phone, so this redemption can't finish.
                if (code !== 'not_paired') throw err;
                answer = 'unknown' as const;
            }
            if (answer === 'pending') return { state: 'pending', pcName: pending.name, expiresAt: pending.expiresAt };
            if (answer === 'unknown') {
                await GaiaTransport.drop(stagedId(pending.id));
                await save({ ...state, pending: null });
                return { state: 'refused' };
            }
            await GaiaTransport.promote(stagedId(pending.id), pending.id);
            const pc: SavedPc = { id: pending.id, name: pending.name, origin: pending.origin, deviceId: pending.deviceId };
            await save({ pcs: [...state.pcs.filter((p) => p.id !== pc.id), pc], currentId: pc.id, pending: null });
            return { state: 'paired', pc: pairedOf(pc) };
        },

        async cancel() {
            const state = await load();
            const { pending } = state;
            if (!pending) return;
            try {
                await unpairOnPc(pending, stagedId(pending.id));
            } catch (err) {
                // The PC drops a device nobody allowed after 10 minutes anyway.
                console.warn(`Couldn't tell ${pending.name} to forget this phone; it drops it in 10 minutes.`, err);
            }
            await GaiaTransport.drop(stagedId(pending.id));
            await save({ ...state, pending: null });
        },

        async list() {
            return (await load()).pcs.map(pairedOf);
        },

        async use(pcId) {
            const state = await load();
            const pc = state.pcs.find((p) => p.id === pcId);
            if (!pc) throw new Error(`This phone isn't paired with a PC whose id is ${pcId}. Pair it from the PC first.`);
            await save({ ...state, currentId: pcId });
            useConnectionStore.getState().setConnection(connectionOf(pc));
        },

        async forget(pcId) {
            const state = await load();
            const pc = state.pcs.find((p) => p.id === pcId);
            if (!pc) throw new Error(`This phone isn't paired with a PC whose id is ${pcId}.`);
            try {
                await unpairOnPc(pc);
            } catch (err) {
                // A changed certificate stops the request at the handshake, before the token is sent.
                console.warn(`Couldn't tell ${pc.name} to unpair this phone; forgetting it here anyway.`, err);
            }
            await GaiaTransport.drop(pcId);
            const pcs = state.pcs.filter((p) => p.id !== pcId);
            const next = state.currentId === pcId ? (pcs[0] ?? null) : (pcs.find((p) => p.id === state.currentId) ?? null);
            await save({ ...state, pcs, currentId: next?.id ?? null });
            const connection = useConnectionStore.getState();
            if (next) connection.setConnection(connectionOf(next));
            else connection.clearConnection();
        },
    };
}

/**
 * Installs the pinned service on a build that has the transport, and reconnects to the PC used last.
 * A development build pointed at a dev backend (EXPO_PUBLIC_GAIA_URL) keeps the fake.
 */
export function installPinnedPairing(): void {
    if (!transportAvailable || (__DEV__ && process.env.EXPO_PUBLIC_GAIA_URL)) return;
    const service = createPinnedPairingService();
    setPairingService(service);
    onDeviceGone((pcId) => {
        service.deviceGone(pcId).catch((err: unknown) => console.error(`Could not forget the PC ${pcId} after it refused this phone`, err));
    });
    const connection = useConnectionStore.getState();
    connection.beginRestore();
    service
        .restore()
        .then((restored) => useConnectionStore.getState().finishRestore(restored))
        .catch((err: unknown) => {
            console.error('Could not read the paired PC saved on this phone', err);
            useConnectionStore.getState().finishRestore(null);
        });
}
