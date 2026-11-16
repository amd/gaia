// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import * as speechApi from '../../services/speechApi';
import { useReadAloudStore } from '../readAloudStore';

vi.mock('../../services/speechApi', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/speechApi')>();
    return { ...actual, speak: vi.fn(), getSpeechModel: vi.fn() };
});

const speak = vi.mocked(speechApi.speak);
const getSpeechModel = vi.mocked(speechApi.getSpeechModel);

/** Plays instantly, or never ends when `hang` is set, so a test can stop mid-piece. */
class FakeAudio {
    static played: string[] = [];
    static hang = false;
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    error = null;
    src: string;
    constructor(src: string) { this.src = src; }
    play() {
        FakeAudio.played.push(this.src);
        if (!FakeAudio.hang) queueMicrotask(() => this.onended?.());
        return Promise.resolve();
    }
    pause() {}
}

let urls = 0;

beforeEach(() => {
    FakeAudio.played = [];
    FakeAudio.hang = false;
    vi.stubGlobal('Audio', FakeAudio);
    URL.createObjectURL = vi.fn(() => `blob:${++urls}`);
    URL.revokeObjectURL = vi.fn();
    speak.mockReset();
    speak.mockImplementation(async (text) => new Blob([text]));
    getSpeechModel.mockReset();
    getSpeechModel.mockResolvedValue({ downloaded: true, size_gb: 0.35 });
    useReadAloudStore.setState({ messageId: null, status: null, error: null, voiceReady: false, downloadGb: null });
});

afterEach(() => {
    useReadAloudStore.getState().stop();
    vi.unstubAllGlobals();
});

const reply = Array.from({ length: 30 }, (_, i) => `Sentence ${i} is here.`).join(' ');

describe('readAloudStore', () => {
    it('speaks a reply piece by piece, in order, then goes idle', async () => {
        await useReadAloudStore.getState().read('s:1', reply);
        const sent = speak.mock.calls.map((c) => c[0]);
        expect(sent.length).toBeGreaterThan(1);
        expect(sent.join(' ')).toBe(reply);
        expect(FakeAudio.played).toHaveLength(sent.length);
        expect(useReadAloudStore.getState()).toMatchObject({ messageId: null, status: null, error: null });
        expect(URL.revokeObjectURL).toHaveBeenCalledTimes(sent.length);
    });

    it('stop cancels the reading and the piece fetched ahead', async () => {
        FakeAudio.hang = true;
        const reading = useReadAloudStore.getState().read('s:1', reply);
        await vi.waitFor(() => expect(useReadAloudStore.getState().status).toBe('playing'));
        useReadAloudStore.getState().stop();
        await reading;
        expect(FakeAudio.played).toHaveLength(1);
        const signal = speak.mock.calls[1][1]?.signal;
        expect(signal?.aborted).toBe(true);
        expect(useReadAloudStore.getState()).toMatchObject({ messageId: null, status: null, error: null });
    });

    it('starting another reply stops the first', async () => {
        FakeAudio.hang = true;
        void useReadAloudStore.getState().read('s:1', 'First reply.');
        await vi.waitFor(() => expect(useReadAloudStore.getState().status).toBe('playing'));
        void useReadAloudStore.getState().read('s:2', 'Second reply.');
        await vi.waitFor(() => expect(FakeAudio.played).toHaveLength(2));
        expect(useReadAloudStore.getState().messageId).toBe('s:2');
    });

    it.each([
        [503, 'unreachable'],
        [502, 'speech'],
        [500, 'other'],
    ] as const)('a %i from the server is reported as %s with its detail', async (status, kind) => {
        speak.mockRejectedValue(new api.ApiError('friendly', status, 'Lemonade said no', null));
        await useReadAloudStore.getState().read('s:1', 'Hello.');
        expect(useReadAloudStore.getState().error).toEqual({ messageId: 's:1', kind, detail: 'Lemonade said no' });
        expect(useReadAloudStore.getState().messageId).toBeNull();
    });

    it('says the voice model is downloading on a first read, then reads, and checks only once', async () => {
        getSpeechModel.mockResolvedValue({ downloaded: false, size_gb: 0.35 });
        let finish: (b: Blob) => void = () => undefined;
        speak.mockImplementationOnce(() => new Promise<Blob>((r) => { finish = r; }));
        const reading = useReadAloudStore.getState().read('s:1', 'Hello.');
        await vi.waitFor(() => expect(useReadAloudStore.getState().status).toBe('downloading'));
        expect(useReadAloudStore.getState().downloadGb).toBe(0.35);
        finish(new Blob(['Hello.']));
        await reading;
        expect(FakeAudio.played).toHaveLength(1);
        expect(useReadAloudStore.getState()).toMatchObject({ status: null, downloadGb: null, voiceReady: true });
        await useReadAloudStore.getState().read('s:2', 'Again.');
        expect(getSpeechModel).toHaveBeenCalledTimes(1);
    });

    it('a downloaded voice model reads straight away', async () => {
        await useReadAloudStore.getState().read('s:1', 'Hello.');
        expect(FakeAudio.played).toHaveLength(1);
        expect(useReadAloudStore.getState().downloadGb).toBeNull();
    });

    it('a failed voice-model check is reported like a failed reading', async () => {
        getSpeechModel.mockRejectedValue(new api.ApiError('x', 503, 'Lemonade Server is not reachable', null));
        await useReadAloudStore.getState().read('s:1', 'Hello.');
        expect(useReadAloudStore.getState().error).toEqual({ messageId: 's:1', kind: 'unreachable', detail: 'Lemonade Server is not reachable' });
        expect(speak).not.toHaveBeenCalled();
    });

    it('a reply with nothing to say requests nothing and says so', async () => {
        await useReadAloudStore.getState().read('s:1', '```\ncode only\n```');
        expect(speak).not.toHaveBeenCalled();
        expect(useReadAloudStore.getState().messageId).toBeNull();
        expect(useReadAloudStore.getState().error).toEqual({ messageId: 's:1', kind: 'nothing', detail: '' });
    });
});
