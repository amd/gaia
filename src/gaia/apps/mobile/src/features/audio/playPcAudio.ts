// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createAudioPlayer, setAudioModeAsync, type AudioPlayer, type AudioStatus } from 'expo-audio';
import { CryptoDigestAlgorithm, digest } from 'expo-crypto';
import { Directory, File, Paths } from 'expo-file-system';

import type { PcAudioBytes } from '@/api/client.types';

/** The phone couldn't play, or clean up after, audio the PC sent. */
export class PcAudioPlaybackError extends Error {}

const EXTENSIONS: Record<string, string> = {
    'audio/aac': 'aac',
    'audio/flac': 'flac',
    'audio/mp4': 'm4a',
    'audio/mpeg': 'mp3',
    'audio/ogg': 'ogg',
    'audio/wav': 'wav',
    'audio/wave': 'wav',
    'audio/webm': 'webm',
    'audio/x-wav': 'wav',
};

/** Where the playing file is, in seconds. */
export interface PcAudioProgress {
    currentTime: number;
    duration: number;
}

export interface PcAudioControls {
    pause(): void;
    resume(): void;
}

export interface PcAudioHooks {
    /** Called once the file starts, with its pause and resume. */
    onPlaying?(controls: PcAudioControls): void;
    onProgress?(progress: PcAudioProgress): void;
}

let current: { stop(): void } | null = null;
let calls = 0;
let audioModeSet: Promise<void> | null = null;

function extensionOf(contentType: string): string {
    const ext = EXTENSIONS[contentType.split(';')[0]!.trim().toLowerCase()];
    if (!ext) throw new PcAudioPlaybackError(`The PC sent audio of a type this phone doesn't play: "${contentType}".`);
    return ext;
}

async function hashOf(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
    const hash = new Uint8Array(await digest(CryptoDigestAlgorithm.SHA256, bytes));
    return Array.from(hash.subarray(0, 8), (b) => b.toString(16).padStart(2, '0')).join('');
}

// Speech the person asked for plays with the ring switch on silent, as the web UI's does.
function ensureAudioMode(): Promise<void> {
    audioModeSet ??= setAudioModeAsync({ playsInSilentMode: true }).catch((err: unknown) => {
        audioModeSet = null;
        throw err;
    });
    return audioModeSet;
}

function asPlaybackError(err: unknown): PcAudioPlaybackError {
    return err instanceof PcAudioPlaybackError ? err : new PcAudioPlaybackError(err instanceof Error ? err.message : String(err), { cause: err });
}

function deleteFile(file: File): void {
    try {
        if (file.exists) file.delete();
    } catch (err) {
        throw new PcAudioPlaybackError(`Couldn't delete the played audio at ${file.uri}: ${asPlaybackError(err).message}`, { cause: err });
    }
}

/**
 * Plays audio fetched through the GaiaClient. expo-audio can't pin TLS or send the device token, so it
 * never gets a PC URL: the bytes go to `<cache>/pc-audio/<hash>.<ext>` and play from that file, which
 * is deleted when playback ends, fails, is stopped through `signal`, or is replaced by the next call.
 * Resolves when playback ends or is stopped; rejects with PcAudioPlaybackError when it can't play.
 */
export function playPcAudio(audio: PcAudioBytes, signal?: AbortSignal, hooks?: PcAudioHooks): Promise<void> {
    current?.stop();
    const seq = ++calls;
    return new Promise<void>((resolve, reject) => {
        let player: AudioPlayer | null = null;
        let file: File | null = null;
        let settled = false;

        const finish = (failure?: unknown) => {
            if (settled) return;
            settled = true;
            signal?.removeEventListener('abort', stop);
            if (current === me) current = null;
            let error = failure;
            try {
                player?.pause();
                player?.remove();
                if (file) deleteFile(file);
            } catch (err) {
                error ??= err;
            }
            if (error === undefined) resolve();
            else reject(asPlaybackError(error));
        };
        const stop = () => finish();
        const me = { stop };
        current = me;
        if (signal?.aborted) {
            finish();
            return;
        }
        signal?.addEventListener('abort', stop);

        void (async () => {
            const ext = extensionOf(audio.contentType);
            const name = `${await hashOf(audio.bytes)}-${seq}`;
            await ensureAudioMode();
            if (settled) return;
            const folder = new Directory(Paths.cache, 'pc-audio');
            folder.create({ intermediates: true, idempotent: true });
            const written = new File(folder, `${name}.${ext}`);
            file = written;
            await written.write(audio.bytes);
            // Stopped while writing: finish already ran, maybe before the file existed.
            if (settled) {
                deleteFile(written);
                return;
            }
            const playing = createAudioPlayer({ uri: written.uri });
            player = playing;
            playing.addListener('playbackStatusUpdate', (status: AudioStatus) => {
                if (status.error) finish(new PcAudioPlaybackError(status.error));
                else if (status.didJustFinish) finish();
                else hooks?.onProgress?.({ currentTime: status.currentTime, duration: status.duration });
            });
            playing.play();
            hooks?.onPlaying?.({
                pause: () => {
                    if (!settled) playing.pause();
                },
                resume: () => {
                    if (!settled) playing.play();
                },
            });
        })().catch((err: unknown) => {
            // After a stop nothing awaits this call, so a late failure surfaces as an unhandled rejection.
            if (settled) return Promise.reject(err);
            finish(err);
        });
    });
}
