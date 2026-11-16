// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { requireOptionalNativeModule } from 'expo';
import { Platform } from 'react-native';

export interface NativeResponseHead {
    status: number;
    /** Header names lowercased. */
    headers: Record<string, string>;
}

export interface NativePairAnswer {
    status: number;
    /** The address that answered first, and got the pairing request. */
    origin: string;
    /** The PC's answer as text; on a 200 the token has been taken out and kept in the Keychain. */
    body: string;
}

/** A piece of a request body: text sent as UTF-8, or a `file://` file on this device sent from disk. */
export type NativeBodySegment = { text: string } | { file: string };

/** Rejections carry `code`: pin_mismatch, unreachable, not_home_network, not_home_origin, network, aborted, not_paired, origin_mismatch, keychain, invalid_pin, bad_response, unknown_request, upload_file. */
export interface GaiaTransportNative {
    pair(pcId: string, origins: string[], pin: string, headers: Record<string, string>, body: string): Promise<NativePairAnswer>;
    start(
        id: string,
        pcId: string,
        method: string,
        url: string,
        headers: Record<string, string>,
        body: string | null,
    ): Promise<NativeResponseHead>;
    /** As `start`, with the body the segments joined in order. Missing from builds made before uploads. */
    upload?(
        id: string,
        pcId: string,
        method: string,
        url: string,
        headers: Record<string, string>,
        segments: NativeBodySegment[],
    ): Promise<NativeResponseHead>;
    /** The body received since the last read; null once it has all arrived. */
    read(id: string): Promise<Uint8Array | null>;
    cancel(id: string): void;
    isPaired(pcId: string): boolean;
    drop(pcId: string): Promise<void>;
    /** Deletes every paired-PC secret this app holds, whatever its id. */
    dropAll(): Promise<void>;
    /** Moves the secret `pair` saved under `stagedId` to `pcId`, replacing what `pcId` had. */
    promote(stagedId: string, pcId: string): Promise<void>;
}

const UNSUPPORTED =
    Platform.OS === 'android'
        ? 'Pairing with a PC works on iPhone and iPad only; Android isn\'t supported yet.'
        : 'Pairing with a PC needs the GAIA app on an iPhone or iPad.';

function unsupported(): never {
    throw new Error(UNSUPPORTED);
}

const STUB: GaiaTransportNative = {
    pair: async () => unsupported(),
    start: async () => unsupported(),
    upload: async () => unsupported(),
    read: async () => unsupported(),
    cancel: () => unsupported(),
    isPaired: () => unsupported(),
    drop: async () => unsupported(),
    dropAll: async () => unsupported(),
    promote: async () => unsupported(),
};

const native = Platform.OS === 'ios' ? requireOptionalNativeModule<GaiaTransportNative>('GaiaTransport') : null;

/** Whether this build has the pinned transport (an iOS build made after the module was added). */
export const transportAvailable = native !== null;

export const GaiaTransport: GaiaTransportNative = native ?? STUB;
