// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../../stores/chatStore';
import { useVoiceStore, VOICE_IDLE, type VoiceState } from '../../../stores/voiceStore';
import { VOICE_ERROR_CODES } from '../../../voice/protocol';

const controller = vi.hoisted(() => ({
    closeVoice: vi.fn(), primaryAction: vi.fn(), holdStart: vi.fn(), holdEnd: vi.fn(), setCaptions: vi.fn(),
    dismissPhoneNotice: vi.fn(), retryVoice: vi.fn(), downloadModel: vi.fn(async () => undefined),
    toggleMute: vi.fn(), setVolume: vi.fn(), setWakeWord: vi.fn(), setChime: vi.fn(), voiceVolume: vi.fn(() => 0.6),
}));
const speech = vi.hoisted(() => ({
    settings: {
        voice: 'af_bella', speed: 100, auto: false, min_speed: 50, max_speed: 200,
        voices: [
            { id: 'af_bella', name: 'Bella', accent: 'american', gender: 'female', quality: 'A-' },
            { id: 'bm_george', name: 'George', accent: 'british', gender: 'male', quality: 'C' },
        ],
    },
    update: vi.fn(),
}));
vi.mock('../../../services/speechApi', () => ({
    getSpeechSettings: vi.fn(async () => speech.settings),
    updateSpeechSettings: (change: object) => { speech.update(change); return Promise.resolve({ ...speech.settings, ...change }); },
}));
vi.mock('../../../voice/controller', () => ({
    ...controller,
    voiceLevels: { mic: () => 0, speech: () => 0 },
}));

import { VoiceScreen } from '../VoiceScreen';

const STATUS = {
    ready: true, error: null, error_code: null, language: 'en', asr_model: 'Moonshine-Tiny-Streaming', tts_model: 'kokoro-v1',
    speaks: true, models: [], turn_taking: 'hands_free' as const, silence_ms: 500, status_updates: true, captions: true,
};

function show(state: Partial<VoiceState>, phone = false) {
    act(() => useVoiceStore.setState({ ...VOICE_IDLE, open: true, sessionId: 's1', status: STATUS, ...state }));
    return render(<VoiceScreen phone={phone} />);
}

beforeEach(() => {
    vi.clearAllMocks();
    act(() => useChatStore.setState({ locale: 'en' }));
});
afterEach(() => act(() => useVoiceStore.setState({ ...VOICE_IDLE })));

describe('VoiceScreen states', () => {
    const cases: Array<[VoiceState['phase'], string, string]> = [
        ['warming', 'Warming up', 'Warming up'],
        ['listening', 'Listening', 'Pause listening'],
        ['thinking', 'Thinking', 'Stop'],
        ['speaking', 'Speaking', 'Interrupt'],
        ['paused', 'Paused', 'Resume listening'],
    ];
    for (const [phase, word, button] of cases) {
        it(`${phase}: one state word, the shape, and the right big button`, () => {
            const { container } = show({ phase });
            expect(container.querySelector('.vs-state')).toHaveTextContent(word);
            expect(container.querySelector('canvas.vs-viz')).toHaveAttribute('data-state', phase);
            expect(screen.getByRole('button', { name: button })).toBeInTheDocument();
        });
    }

    it('shimmers the word only while a state is live', () => {
        const { container, unmount } = show({ phase: 'listening' });
        expect(container.querySelector('.vs-state')).toHaveClass('is-live');
        unmount();
        const paused = show({ phase: 'paused' });
        expect(paused.container.querySelector('.vs-state')).not.toHaveClass('is-live');
    });

    it('cannot be pressed while warming up', () => {
        show({ phase: 'warming' });
        expect(screen.getByRole('button', { name: 'Warming up' })).toBeDisabled();
    });

    it('the big button runs the action for the state', () => {
        show({ phase: 'speaking' });
        fireEvent.click(screen.getByRole('button', { name: 'Interrupt' }));
        expect(controller.primaryAction).toHaveBeenCalledOnce();
    });

    it('shows what you said live, then lights GAIA\'s words as they are heard', () => {
        const { container } = show({
            phase: 'speaking', heard: 'find the invoices', heardPartial: false,
            caption: { kind: 'spoken', text: 'Four invoices, about 3,400 dollars.', lit: 2 },
        });
        expect(container.querySelector('.vs-you')).toHaveTextContent('find the invoices');
        const words = container.querySelectorAll('.vs-w');
        expect(words).toHaveLength(5);
        expect([...words].map((w) => w.classList.contains('is-on'))).toEqual([true, true, false, false, false]);
    });

    it('hides the captions, not the work log, when captions are off', () => {
        const { container } = show({ phase: 'listening', captions: false, log: [{ id: 1, kind: 'user', text: 'hello' }] });
        expect(container.querySelector('.vs-caps')).toHaveAttribute('data-off');
        expect(screen.getByRole('complementary', { name: 'What GAIA did' })).toBeVisible();
    });

    it('lists the work with an icon per row and no text tag', () => {
        const { container } = show({
            phase: 'thinking', tools: 1,
            log: [
                { id: 1, kind: 'user', text: 'find invoices' },
                { id: 2, kind: 'status', text: 'Looking in Downloads.' },
                { id: 3, kind: 'tool', text: '', tool: 'search_files', label: 'Searching Downloads', done: true, ok: true, result: '4 PDFs found' },
                { id: 4, kind: 'answer', text: '| Vendor | Total |\n|---|---|\n| Northwind | $1,980 |' },
            ],
        });
        expect(container.querySelectorAll('.vs-row')).toHaveLength(3);
        expect(container.querySelector('.vs-row.is-tool bdi')).toHaveTextContent('search_files');
        expect(container.querySelector('.vs-row.is-tool .vs-call-res')).toHaveTextContent('4 PDFs found');
        expect(container.querySelector('.vs-result table')).not.toBeNull();
        expect(container.querySelector('.vs-count')).toHaveTextContent('1');
    });

    it('waiting for her name says so instead of "Listening"', () => {
        const { container } = show({ phase: 'listening', standby: true });
        expect(container.querySelector('.vs-state')).toHaveTextContent('Say "Hey GAIA"');
    });

    it('the microphone button mutes and says which way it will go', () => {
        const { unmount } = show({ phase: 'listening' });
        fireEvent.click(screen.getByRole('button', { name: 'Mute microphone' }));
        expect(controller.toggleMute).toHaveBeenCalledOnce();
        unmount();
        show({ phase: 'paused', muted: true });
        expect(screen.getByRole('button', { name: 'Unmute microphone' })).toHaveAttribute('aria-pressed', 'false');
    });

    it('push-to-talk has no mute button: the microphone is off until held', () => {
        show({ phase: 'ptt_idle', pushToTalk: true });
        expect(screen.queryByRole('button', { name: 'Mute microphone' })).toBeNull();
    });

    it('the settings menu changes volume, voice, speed and the wake word', async () => {
        show({ phase: 'listening', status: { ...STATUS, wake_word: true, volume: 60 } });
        fireEvent.click(screen.getByRole('button', { name: 'Voice settings' }));
        const voice = await screen.findByRole('combobox', { name: 'Voice' });
        await screen.findByRole('option', { name: /George/ });
        fireEvent.change(voice, { target: { value: 'bm_george' } });
        expect(speech.update).toHaveBeenCalledWith({ voice: 'bm_george' });
        fireEvent.change(screen.getByRole('combobox', { name: 'Speed' }), { target: { value: '125' } });
        expect(speech.update).toHaveBeenCalledWith({ speed: 125 });
        const volume = screen.getByRole('slider', { name: 'Volume' });
        expect(volume).toHaveValue('60');
        fireEvent.change(volume, { target: { value: '35' } });
        expect(controller.setVolume).toHaveBeenCalledWith(0.35);
        fireEvent.click(screen.getByRole('checkbox', { name: /Hey GAIA/ }));
        expect(controller.setWakeWord).toHaveBeenCalledWith(false);
        fireEvent.click(screen.getByRole('checkbox', { name: /tone/ }));
        expect(controller.setChime).toHaveBeenCalledWith(false);
    });

    it('a picture a tool made is shown under its row', () => {
        const { container } = show({
            phase: 'thinking',
            log: [{ id: 1, kind: 'tool', text: '', tool: 'generate_image', done: true, ok: true, image: 'C:\\Users\\me\\fox 1.png' }],
        });
        expect(container.querySelector('img.vs-shot')).toHaveAttribute('src', '/api/files/image?path=C%3A%5CUsers%5Cme%5Cfox%201.png');
    });

    it('Arabic voice: one notice, captions forced on', () => {
        show({ phase: 'listening', status: { ...STATUS, language: 'ar', speaks: false } });
        expect(screen.getByRole('note')).toHaveTextContent('Spoken replies are English-only for now. GAIA will answer on screen.');
        expect(screen.getByRole('button', { name: 'Captions' })).toBeDisabled();
    });

    it('Esc closes voice mode', () => {
        show({ phase: 'listening' });
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(controller.closeVoice).toHaveBeenCalledOnce();
    });

    it('push-to-talk: holding the button talks, releasing ends the turn', () => {
        show({ phase: 'ptt_idle', pushToTalk: true });
        const big = screen.getByRole('button', { name: 'Hold to talk' });
        fireEvent.pointerDown(big);
        expect(controller.holdStart).toHaveBeenCalledOnce();
        fireEvent.pointerUp(big);
        expect(controller.holdEnd).toHaveBeenCalledOnce();
    });

    it('phone: the work log is a sheet behind a "What GAIA did" bar, and the notice shows once', () => {
        const { container } = show({ phase: 'listening', phoneNotice: true, tools: 3, log: [{ id: 1, kind: 'user', text: 'hi' }] }, true);
        expect(container.querySelector('.voice-screen')).toHaveClass('is-phone');
        const bar = screen.getByRole('button', { name: /What GAIA did/ });
        expect(bar).toHaveTextContent('3');
        expect(container.querySelector('.vs-sheet')).not.toHaveClass('is-open');
        fireEvent.click(bar);
        expect(container.querySelector('.vs-sheet')).toHaveClass('is-open');
        fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
        expect(controller.dismissPhoneNotice).toHaveBeenCalledOnce();
        expect(screen.getByRole('button', { name: 'Back to chat' })).toBeInTheDocument();
    });
});

describe('VoiceScreen error cards', () => {
    for (const code of VOICE_ERROR_CODES) {
        it(`${code}: a translated title, never the raw message`, () => {
            const { container } = show({ phase: 'paused', error: { code, detail: 'Traceback: ConnectionRefusedError(111)' } });
            const card = container.querySelector('.vs-error');
            expect(card).not.toBeNull();
            expect(card?.querySelector('.vs-error-title')?.textContent).toBeTruthy();
            expect(card).not.toHaveTextContent('Traceback');
            expect(card).not.toHaveTextContent(/\brun\b|lemonade-server|gaia init/i);
            // The shape rests while the card is up.
            expect(container.querySelector('canvas.vs-viz')).toHaveAttribute('data-state', 'paused');
        });
    }

    it('model_missing offers each missing model with its size, and downloads only on click', () => {
        show({
            phase: 'paused',
            error: { code: 'model_missing', detail: 'kokoro-v1' },
            status: { ...STATUS, ready: false, models: [
                { id: 'Moonshine-Tiny-Streaming', purpose: 'transcription', downloaded: true, size_gb: 0.1 },
                { id: 'kokoro-v1', purpose: 'tts', downloaded: false, size_gb: 0.34 },
            ] },
        });
        const button = screen.getByRole('button', { name: /Download/ });
        expect(button).toHaveTextContent('0.34 GB');
        expect(controller.downloadModel).not.toHaveBeenCalled();
        fireEvent.click(button);
        expect(controller.downloadModel).toHaveBeenCalledWith('kokoro-v1');
    });

    it('a download in progress shows a spinner and blocks a second one', () => {
        show({
            phase: 'paused', downloading: 'kokoro-v1',
            error: { code: 'model_missing', detail: 'kokoro-v1' },
            status: { ...STATUS, ready: false, models: [{ id: 'kokoro-v1', purpose: 'tts', downloaded: false, size_gb: 0.34 }] },
        });
        expect(screen.getByRole('button', { name: /Downloading/ })).toBeDisabled();
    });

    it('mic_busy and session_lost offer Try again', () => {
        const { unmount } = show({ phase: 'paused', error: { code: 'mic_busy' } });
        expect(screen.getByText('Your microphone is in use by another app.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(controller.retryVoice).toHaveBeenCalledOnce();
        unmount();
        show({ phase: 'paused', error: { code: 'session_lost' } });
        expect(screen.getByText('Lost the connection to speech.')).toBeInTheDocument();
    });

    it('mic_denied opens the OS microphone pane in the desktop app', () => {
        const openMicSettings = vi.fn(async () => true);
        (window as unknown as { gaiaAPI: unknown }).gaiaAPI = { system: { openMicSettings } };
        try {
            show({ phase: 'paused', error: { code: 'mic_denied' } });
            fireEvent.click(screen.getByRole('button', { name: 'Open microphone settings' }));
            expect(openMicSettings).toHaveBeenCalledOnce();
        } finally {
            delete (window as unknown as { gaiaAPI?: unknown }).gaiaAPI;
        }
    });

    it('a remote model server has no retry: nothing on this PC can fix it from here', () => {
        show({ phase: 'paused', error: { code: 'remote_lemonade' } });
        expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    });
});

describe('VoiceScreen in Arabic', () => {
    it('labels the state and buttons in Arabic', () => {
        act(() => useChatStore.setState({ locale: 'ar' }));
        const { container } = show({ phase: 'listening' });
        expect(container.querySelector('.vs-state')).toHaveTextContent('يستمع');
        expect(container.querySelector('.vs-state')).not.toHaveTextContent('Listening');
    });
});
