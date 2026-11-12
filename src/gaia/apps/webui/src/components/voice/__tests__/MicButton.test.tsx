// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from '../../Composer';
import { useAttachments } from '../../../hooks/useAttachments';

vi.mock('../../../services/api');

const fx = vi.hoisted(() => ({
    sockets: [] as Array<Record<string, any>>,
    micError: null as unknown,
    status: null as unknown,
}));

vi.mock('../../../voice/socket', () => ({
    VoiceSocket: vi.fn(function (purpose: string, language: string, handlers: unknown) {
        const s = { purpose, language, handlers, commit: vi.fn(), close: vi.fn(), sendAudio: vi.fn() };
        fx.sockets.push(s);
        return s;
    }),
}));

vi.mock('../../../voice/audio', async (orig) => ({
    ...(await orig<typeof import('../../../voice/audio')>()),
    micSupported: () => true,
    createVoiceContext: () => ({ resume: vi.fn(), close: vi.fn(async () => undefined) }),
    MicCapture: vi.fn(function () {
        return { start: vi.fn(async () => { if (fx.micError) throw fx.micError; }), stop: vi.fn(), level: () => 0.4 };
    }),
}));

vi.mock('../../../services/voiceApi', () => ({
    getVoiceStatus: vi.fn(async () => fx.status),
    downloadVoiceModel: vi.fn(async (model: string) => ({ model, downloaded: true })),
}));

function Harness({ initial = '', onSubmit = vi.fn(), onVoice }: { initial?: string; onSubmit?: () => void; onVoice?: () => void }) {
    const [value, setValue] = useState(initial);
    const attachments = useAttachments();
    return <Composer value={value} onChange={setValue} onSubmit={onSubmit} attachments={attachments} onVoice={onVoice} />;
}

const socket = () => fx.sockets[fx.sockets.length - 1];
const serve = (event: object) => act(() => (socket().handlers as { onEvent: (e: object) => void }).onEvent(event));

beforeEach(() => {
    fx.sockets.length = 0;
    fx.micError = null;
    fx.status = null;
});

describe('the voice button', () => {
    it('stands in for Send while the box is empty, and opens voice mode', () => {
        const onVoice = vi.fn();
        render(<Harness onVoice={onVoice} />);
        expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Voice mode' }));
        expect(onVoice).toHaveBeenCalledOnce();
    });

    it('turns back into Send once something is typed', () => {
        render(<Harness onVoice={vi.fn()} />);
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } });
        expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
        expect(screen.queryByRole('button', { name: 'Voice mode' })).not.toBeInTheDocument();
    });

    it('is absent where voice mode is not offered', () => {
        render(<Harness />);
        expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
        expect(screen.queryByRole('button', { name: 'Voice mode' })).not.toBeInTheDocument();
    });
});

describe('dictation', () => {
    it('idle: a mic button after attach', () => {
        render(<Harness />);
        const mic = screen.getByRole('button', { name: 'Dictate' });
        expect(mic).toHaveAttribute('aria-pressed', 'false');
    });

    it('recording: the words as heard, a level meter and a timer replace the box', async () => {
        const { container } = render(<Harness initial="Note:" />);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        expect(socket().purpose).toBe('dictation');
        expect(screen.getByRole('button', { name: 'Stop dictation' })).toHaveAttribute('aria-pressed', 'true');
        expect(container.querySelector('.composer')).toHaveClass('is-recording');
        expect(screen.getByLabelText('Message')).not.toBeVisible();
        expect(container.querySelector('.composer-rec-time')).toHaveTextContent('0:00');
        expect(container.querySelector('canvas.composer-rec-wave')).not.toBeNull();
        serve({ type: 'partial', text: 'the demo moved' });
        expect(container.querySelector('.composer-livebox')).toHaveTextContent('Note: the demo moved');
        expect(container.querySelector('.composer-livebox-partial')).toHaveTextContent('the demo moved');
    });

    it('pauses join segments; stopping inserts the transcript with a space and returns focus', async () => {
        render(<Harness initial="Note:" />);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        serve({ type: 'final', text: 'The demo moved to Thursday.' });
        serve({ type: 'final', text: 'Who can bring the projector?' });
        fireEvent.click(screen.getByRole('button', { name: 'Stop dictation' }));
        const box = screen.getByLabelText('Message') as HTMLTextAreaElement;
        expect(box.value).toBe('Note: The demo moved to Thursday. Who can bring the projector?');
        await waitFor(() => expect(box).toHaveFocus());
        expect(socket().close).toHaveBeenCalled();
    });

    it('inserts where the cursor was, not at the end', async () => {
        render(<Harness initial="Dear team, thanks" />);
        const box = screen.getByLabelText('Message') as HTMLTextAreaElement;
        box.setSelectionRange(10, 10);
        fireEvent.select(box);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        serve({ type: 'final', text: 'the demo moved.' });
        fireEvent.click(screen.getByRole('button', { name: 'Stop dictation' }));
        expect(box.value).toBe('Dear team, the demo moved. thanks');
    });

    it('Enter stops dictation and does not send', async () => {
        const onSubmit = vi.fn();
        render(<Harness onSubmit={onSubmit} />);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        serve({ type: 'partial', text: 'hello there' });
        fireEvent.keyDown(window, { key: 'Enter' });
        expect(socket().commit).toHaveBeenCalledOnce();
        expect(screen.getByRole('button', { name: 'Stop dictation' })).toBeDisabled();
        serve({ type: 'final', text: 'Hello there.' });
        expect((screen.getByLabelText('Message') as HTMLTextAreaElement).value).toBe('Hello there.');
        expect(onSubmit).not.toHaveBeenCalled();
    });

    it('a refused microphone says so in plain words', async () => {
        fx.micError = new DOMException('Permission denied', 'NotAllowedError');
        render(<Harness />);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Microphone access is off');
        expect(screen.getByRole('button', { name: 'Dictate' })).toBeInTheDocument();
    });

    it('a missing recognizer offers its download, with the size, only on click', async () => {
        fx.status = {
            ready: false, error_code: 'model_missing', models: [{ id: 'Whisper-Base', purpose: 'transcription', downloaded: false, size_gb: 0.15 }],
        };
        render(<Harness />);
        fireEvent.click(screen.getByRole('button', { name: 'Dictate' }));
        act(() => (socket().handlers as { onFailure: (f: object) => void }).onFailure({ code: 'model_missing', detail: 'Whisper-Base' }));
        const download = await screen.findByRole('button', { name: /Download/ });
        expect(download).toHaveTextContent('0.15 GB');
        const { downloadVoiceModel } = await import('../../../services/voiceApi');
        expect(downloadVoiceModel).not.toHaveBeenCalled();
        fireEvent.click(download);
        await waitFor(() => expect(downloadVoiceModel).toHaveBeenCalledWith('Whisper-Base'));
    });
});
