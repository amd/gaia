// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Generated music and sound effects play inline in the chat: a card with
 * play/pause, length, model, prompt and Open / Show in folder. A payload that
 * is not an absolute .wav path never reaches the audio route.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../services/api', () => {
    class ApiError extends Error {
        detail: string;
        constructor(message: string, detail = '') {
            super(message);
            this.detail = detail;
        }
    }
    return {
        ApiError,
        audioFileUrl: vi.fn((p: string) => `http://api/files/audio?path=${encodeURIComponent(p)}`),
        openFileInApp: vi.fn(() => Promise.resolve({ status: 'ok', path: '' })),
        openFileOrFolder: vi.fn(() => Promise.resolve({ status: 'ok', path: '' })),
        getFileStatus: vi.fn(() => Promise.resolve({ files: [{ path: '', exists: true, mtime: 1 }] })),
    };
});

import * as api from '../../services/api';
import {
    GeneratedAudioCard, AUDIO_GONE_ERROR, AUDIO_LOAD_ERROR, AUDIO_OUT_OF_SCOPE_ERROR, NOT_A_WAV_ERROR, parentFolder,
} from '../GeneratedAudioCard';
import { MessageBubble } from '../MessageBubble';
import { AgentActivity } from '../AgentActivity';
import { OutputsSection } from '../session-panel/OutputsSection';
import {
    formatAudioDuration, generatedAudioOf, isPlayableWavPath, toGeneratedAudio,
} from '../../utils/generatedAudio';
import type { AgentStep, GeneratedAudio, Message } from '../../types';

const WAV = 'C:\\Users\\me\\.gaia\\cache\\audio\\generations\\music_20261029_120000_ab12.wav';

const music: GeneratedAudio = {
    path: WAV,
    model: 'ACE-Step-Music',
    kind: 'music',
    format: 'wav',
    durationSeconds: 12.04,
    durationRequested: 12,
    prompt: 'lo-fi piano, rainy mood, 70 bpm',
};

beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
        Object.defineProperty(this, 'paused', { value: false, configurable: true });
        this.dispatchEvent(new Event('play'));
        return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
        Object.defineProperty(this, 'paused', { value: true, configurable: true });
        this.dispatchEvent(new Event('pause'));
    });
    vi.mocked(api.openFileInApp).mockClear();
    vi.mocked(api.openFileOrFolder).mockClear();
    vi.mocked(api.getFileStatus).mockClear();
});

afterEach(() => vi.restoreAllMocks());

describe('GeneratedAudioCard', () => {
    it('shows play, length, model, prompt and the file name', () => {
        const { container } = render(<GeneratedAudioCard audio={music} />);
        expect(screen.getByRole('button', { name: /^Play music_/ })).toBeInTheDocument();
        expect(screen.getByText('Music · ACE-Step music · 0:12')).toBeInTheDocument();
        expect(screen.getByText('lo-fi piano, rainy mood, 70 bpm')).toBeInTheDocument();
        expect(screen.getByText('music_20261029_120000_ab12.wav')).toBeInTheDocument();
        const audio = container.querySelector('audio')!;
        expect(audio.getAttribute('src')).toBe(`http://api/files/audio?path=${encodeURIComponent(WAV)}`);
        expect(audio.getAttribute('preload')).toBe('metadata');
    });

    it('labels a sound effect from ThinkSound', () => {
        render(<GeneratedAudioCard audio={{ ...music, kind: 'sfx', model: 'ThinkSound-SFX', durationSeconds: 3 }} />);
        expect(screen.getByText('Sound effect · ThinkSound SFX · 0:03')).toBeInTheDocument();
    });

    it('toggles between play and pause', async () => {
        render(<GeneratedAudioCard audio={music} />);
        fireEvent.click(screen.getByRole('button', { name: /^Play / }));
        await waitFor(() => expect(screen.getByRole('button', { name: /^Pause / })).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: /^Pause / }));
        await waitFor(() => expect(screen.getByRole('button', { name: /^Play / })).toBeInTheDocument());
    });

    it('prefers the length the player measured', () => {
        const { container } = render(<GeneratedAudioCard audio={music} />);
        const audio = container.querySelector('audio')!;
        Object.defineProperty(audio, 'duration', { value: 75, configurable: true });
        fireEvent.loadedMetadata(audio);
        expect(screen.getByText('Music · ACE-Step music · 1:15')).toBeInTheDocument();
    });

    it('opens the file and reveals it in its folder', () => {
        render(<GeneratedAudioCard audio={music} />);
        fireEvent.click(screen.getByRole('button', { name: 'Open' }));
        expect(api.openFileInApp).toHaveBeenCalledWith(WAV);
        fireEvent.click(screen.getByRole('button', { name: /Show in folder/ }));
        expect(api.openFileOrFolder).toHaveBeenCalledWith(WAV, true);
    });

    it('says why an action failed', async () => {
        vi.mocked(api.openFileInApp).mockRejectedValueOnce(new Error('Path not found'));
        render(<GeneratedAudioCard audio={music} />);
        fireEvent.click(screen.getByRole('button', { name: 'Open' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/Couldn't open music_.*Path not found/);
    });

    it('says a deleted file is gone and opens its folder instead', async () => {
        vi.mocked(api.getFileStatus).mockResolvedValueOnce({ files: [{ path: WAV, exists: false }] });
        const { container } = render(<GeneratedAudioCard audio={music} />);
        fireEvent.error(container.querySelector('audio')!);
        expect(await screen.findByText(AUDIO_GONE_ERROR)).toBeInTheDocument();
        expect(api.getFileStatus).toHaveBeenCalledWith([WAV]);
        expect(screen.queryByRole('button', { name: /^Play / })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /Open its folder/ }));
        expect(api.openFileOrFolder).toHaveBeenCalledWith(parentFolder(WAV), false);
    });

    it('says a file outside home cannot be played here, and offers nothing that would fail', async () => {
        vi.mocked(api.getFileStatus).mockResolvedValueOnce({ files: [{ path: WAV, exists: null }] });
        const { container } = render(<GeneratedAudioCard audio={music} />);
        fireEvent.error(container.querySelector('audio')!);
        expect(await screen.findByText(AUDIO_OUT_OF_SCOPE_ERROR)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
        expect(screen.queryByRole('button', { name: /folder/ })).toBeNull();
    });

    it('keeps Open when an existing file will not decode', async () => {
        const { container } = render(<GeneratedAudioCard audio={music} />);
        fireEvent.error(container.querySelector('audio')!);
        expect(screen.getByRole('alert')).toHaveTextContent(AUDIO_LOAD_ERROR);
        await waitFor(() => expect(api.getFileStatus).toHaveBeenCalled());
        expect(screen.getByRole('alert')).toHaveTextContent(AUDIO_LOAD_ERROR);
        expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Show in folder/ })).toBeInTheDocument();
    });

    it('keeps the load error when the status check itself fails', async () => {
        vi.mocked(api.getFileStatus).mockRejectedValueOnce(new Error('offline'));
        const { container } = render(<GeneratedAudioCard audio={music} />);
        fireEvent.error(container.querySelector('audio')!);
        await waitFor(() => expect(api.getFileStatus).toHaveBeenCalled());
        expect(screen.getByRole('alert')).toHaveTextContent(AUDIO_LOAD_ERROR);
    });

    it('treats a pause during loading as a pause, not a broken file', async () => {
        vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() =>
            Promise.reject(Object.assign(new Error('interrupted'), { name: 'AbortError' })));
        render(<GeneratedAudioCard audio={music} />);
        fireEvent.click(screen.getByRole('button', { name: /^Play / }));
        await Promise.resolve();
        await Promise.resolve();
        expect(screen.queryByRole('alert')).toBeNull();
        expect(screen.getByRole('button', { name: /^Play / })).toBeInTheDocument();
    });

    it('reports a real playback failure', async () => {
        vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(() =>
            Promise.reject(Object.assign(new Error('no decoder'), { name: 'NotSupportedError' })));
        render(<GeneratedAudioCard audio={music} />);
        fireEvent.click(screen.getByRole('button', { name: /^Play / }));
        expect(await screen.findByRole('alert')).toHaveTextContent(AUDIO_LOAD_ERROR);
    });

    it.each([
        ['C:\\a\\b\\c.wav', 'C:\\a\\b'],
        ['/home/me/x.wav', '/home/me'],
    ])('parentFolder(%s) is %s', (path, folder) => {
        expect(parentFolder(path)).toBe(folder);
    });

    it.each([
        ['an empty path', ''],
        ['an mp3', 'C:\\music\\song.mp3'],
        ['a program', 'C:\\music\\song.wav.exe'],
        ['no extension', 'C:\\music\\song'],
        ['a relative path', 'music\\song.wav'],
        ['a traversal', 'C:\\Users\\me\\..\\..\\Windows\\song.wav'],
        ['a script url', 'javascript:alert(1)//.wav'],
        ['a control character', 'C:\\music\\so\nng.wav'],
    ])('refuses %s without requesting it', (_label, path) => {
        const { container } = render(<GeneratedAudioCard audio={{ ...music, path }} />);
        expect(container.querySelector('audio')).toBeNull();
        expect(screen.getByRole('alert')).toHaveTextContent(NOT_A_WAV_ERROR);
        expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
        expect(api.audioFileUrl).not.toHaveBeenCalledWith(path);
    });

    it('survives a missing path field', () => {
        const broken = { ...music, path: undefined } as unknown as GeneratedAudio;
        const { container } = render(<GeneratedAudioCard audio={broken} />);
        expect(container.querySelector('audio')).toBeNull();
        expect(screen.getByText('Generated audio')).toBeInTheDocument();
    });

    it.each([
        [Number.NaN],
        [Number.POSITIVE_INFINITY],
        [-5],
        [0],
    ])('shows no length for a bogus duration (%s)', (bad) => {
        render(<GeneratedAudioCard audio={{ ...music, durationSeconds: bad, durationRequested: bad }} />);
        expect(screen.getByText('Music · ACE-Step music')).toBeInTheDocument();
        expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
    });
});

describe('formatAudioDuration', () => {
    it.each([
        [5, '0:05'],
        [12.04, '0:12'],
        [754, '12:34'],
        [3725, '1:02:05'],
        [1e9, '277777:46:40'],
        [Number.NaN, ''],
        [Number.POSITIVE_INFINITY, ''],
        [-1, ''],
        ['12', ''],
        [undefined, ''],
    ])('%s -> %j', (input, expected) => {
        expect(formatAudioDuration(input)).toBe(expected);
    });

    it('renders a huge duration without breaking the card', () => {
        render(<GeneratedAudioCard audio={{ ...music, durationSeconds: 1e9 }} />);
        expect(screen.getByText('Music · ACE-Step music · 277777:46:40')).toBeInTheDocument();
    });
});

describe('isPlayableWavPath', () => {
    it.each([
        ['C:\\a\\b.wav', true],
        ['C:/a/b.WAV', true],
        ['/home/me/b.wav', true],
        ['\\\\server\\share\\b.wav', true],
        ['b.wav', false],
        ['C:\\a\\..\\b.wav', false],
        ['C:\\a\\b.wav\u0000.exe', false],
        [42, false],
    ])('%j -> %s', (path, ok) => {
        expect(isPlayableWavPath(path)).toBe(ok);
    });
});

const step = (over: Partial<AgentStep>): AgentStep => ({
    id: 1,
    type: 'tool',
    label: 'Composing music',
    tool: 'generate_music',
    success: true,
    timestamp: 0,
    ...over,
});

describe('generatedAudioOf', () => {
    it('keeps successful tool steps once each, in order', () => {
        const sfx = { ...music, path: 'C:\\x\\sfx.wav', kind: 'sfx' };
        const items = generatedAudioOf([
            step({ id: 1, generatedAudio: music }),
            step({ id: 2, generatedAudio: music }),
            step({ id: 3, success: false, generatedAudio: sfx }),
            step({ id: 4, type: 'thinking', generatedAudio: sfx }),
            step({ id: 5, tool: 'generate_sound_effect', generatedAudio: sfx }),
            step({ id: 6 }),
        ]);
        expect(items.map((a) => a.path)).toEqual([WAV, 'C:\\x\\sfx.wav']);
    });

    it('maps the live event into the saved shape', () => {
        expect(toGeneratedAudio({
            path: WAV, model: 'ACE-Step-Music', kind: 'music', format: 'wav',
            duration_seconds: 12.04, duration_requested: 12, prompt: 'lofi',
        })).toEqual({
            path: WAV, model: 'ACE-Step-Music', kind: 'music', format: 'wav',
            durationSeconds: 12.04, durationRequested: 12, prompt: 'lofi',
        });
    });
});

const answer: Message = {
    id: 9,
    session_id: 's',
    role: 'assistant',
    content: 'Here is your track.',
    created_at: '2026-10-29T00:00:00.000Z',
    rag_sources: null,
};

describe('MessageBubble', () => {
    it('plays a reloaded turn\'s track above the answer, once', () => {
        const { container } = render(
            <MessageBubble message={answer} agentSteps={[step({ generatedAudio: music }), step({ id: 2, generatedAudio: music })]} />,
        );
        expect(container.querySelectorAll('.gen-audio')).toHaveLength(1);
        const card = container.querySelector('.gen-audio')!;
        const body = container.querySelector('.md-content')!;
        expect(card.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('shows the card while the turn is still streaming', () => {
        const { container } = render(
            <MessageBubble message={{ ...answer, content: '' }} isStreaming agentStepsActive agentSteps={[step({ generatedAudio: music })]} />,
        );
        expect(container.querySelector('.gen-audio')).not.toBeNull();
    });

    it('shows no card for a failed generation', () => {
        const { container } = render(
            <MessageBubble message={answer} agentSteps={[step({ success: false, result: 'Lemonade audio generation failed (503)' })]} />,
        );
        expect(container.querySelector('.gen-audio')).toBeNull();
    });
});

describe('AgentActivity rows', () => {
    it('names the audio tools in plain words', () => {
        render(
            <AgentActivity
                isActive={false}
                steps={[
                    step({ id: 1, tool: 'generate_music' }),
                    step({ id: 2, tool: 'generate_sound_effect', result: 'Saved sfx.wav' }),
                    step({ id: 3, tool: 'list_audio_generation_models', result: 'ok' }),
                ]}
            />,
        );
        fireEvent.click(screen.getByRole('button', { expanded: false }));
        expect(screen.getByText('Composed music')).toBeInTheDocument();
        expect(screen.getByText('Generated sound effect')).toBeInTheDocument();
        expect(screen.getByText('Checked audio models')).toBeInTheDocument();
    });

    it('shows the narration and a ticking timer while a long generation runs', () => {
        const started = Date.now() - 95_000;
        render(
            <AgentActivity
                isActive
                running
                steps={[step({ active: true, success: undefined, narration: 'Composing 120 s of music: orchestral', timestamp: started })]}
            />,
        );
        expect(screen.getAllByText(/Composing 120 s of music/).length).toBeGreaterThan(0);
        expect(screen.getByText(/^1m 3\ds$/)).toBeInTheDocument();
    });

    it('keeps a failed generation\'s reason on its row', () => {
        render(
            <AgentActivity
                isActive={false}
                steps={[step({ success: false, result: "'Nope' is not a downloaded audio-generation model" })]}
            />,
        );
        expect(screen.getByText(/not a downloaded audio-generation model/)).toBeInTheDocument();
    });
});

describe('Outputs rail', () => {
    it('lists generated audio with an Audio label', async () => {
        const getOutputs = vi.fn().mockResolvedValue({
            items: [{
                id: 'audio:9:x', kind: 'audio', name: 'music_1.wav', path: WAV, detail: 'lofi',
                message_id: 9, created_at: null, change: null, state: 'present', openable: true, revealable: true,
            }],
        });
        (api as unknown as Record<string, unknown>).getOutputs = getOutputs;
        render(<OutputsSection sessionId="s" busy={false} onJumpToMessage={() => true} />);
        expect(await screen.findByText('music_1.wav')).toBeInTheDocument();
        expect(screen.getByText(/^Audio/)).toBeInTheDocument();
    });
});
