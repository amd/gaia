// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AudioStatus } from 'expo-audio';

import { playPcAudio } from './playPcAudio';

type MockListener = (status: Partial<AudioStatus>) => void;
const mockFiles = new Map<string, Uint8Array>();
const mockPlayers: { uri: string; status?: MockListener; play: jest.Mock; removed: boolean }[] = [];

jest.mock('expo-file-system', () => {
    const join = (parts: unknown[]) => parts.map((p) => (typeof p === 'string' ? p : (p as { uri: string }).uri)).join('/');
    return {
        Paths: { cache: { uri: 'file:///cache' } },
        Directory: class {
            uri: string;
            constructor(...parts: unknown[]) {
                this.uri = join(parts);
            }
            create() {}
        },
        File: class {
            uri: string;
            constructor(...parts: unknown[]) {
                this.uri = join(parts);
            }
            get exists() {
                return mockFiles.has(this.uri);
            }
            async write(bytes: Uint8Array) {
                mockFiles.set(this.uri, bytes);
            }
            delete() {
                mockFiles.delete(this.uri);
            }
        },
    };
});
jest.mock('expo-crypto', () => ({
    CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
    digest: async () => new Uint8Array([0xab, 0xcd, 1, 2, 3, 4, 5, 6]).buffer,
}));
jest.mock('expo-audio', () => ({
    setAudioModeAsync: async () => undefined,
    createAudioPlayer: (source: { uri: string }) => {
        const player = {
            uri: source.uri,
            status: undefined as MockListener | undefined,
            play: jest.fn(),
            removed: false,
            pause: jest.fn(),
            remove() {
                player.removed = true;
            },
            addListener(_event: string, listener: MockListener) {
                player.status = listener;
            },
        };
        mockPlayers.push(player);
        return player;
    },
}));

const WAV = { bytes: new Uint8Array([82, 73, 70, 70]), contentType: 'audio/wav' };

it('plays the PC audio from a cache file and deletes the file when playback ends', async () => {
    const done = playPcAudio(WAV);
    while (mockPlayers.length === 0) await new Promise((r) => setTimeout(r, 0));

    const player = mockPlayers[0]!;
    expect(player.uri).toMatch(/^file:\/\/\/cache\/pc-audio\/abcd010203040506-\d+\.wav$/);
    expect(mockFiles.get(player.uri)).toEqual(WAV.bytes);
    expect(player.play).toHaveBeenCalled();

    player.status!({ didJustFinish: true, error: null });
    await done;
    expect(mockFiles.size).toBe(0);
    expect(player.removed).toBe(true);
});
