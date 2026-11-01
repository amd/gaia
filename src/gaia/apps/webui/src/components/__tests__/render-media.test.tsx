// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Generated images and audio render inline (#540): the `image` card also
 * takes a generated-media reference, the `audio` card plays one, and a
 * reloaded answer gets back the cards its tool steps persisted.
 */

import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RenderCard } from '../render/RenderCard';
import { resolveMediaSrc } from '../render/mediaSrc';
import { fromApiMessages } from '../../utils/chatStream';

const BAD_REFS = [
    '',
    '/api/media/image/',
    '/api/media/image/../x.png',
    '/api/media/image/..%2Fx.png',
    '/api/media/image/..%5Cx.png',
    '/api/media/image/%2e%2e',
    '/api/media/image/sub/x.png',
    '/api/media/image/C%3A%5Cx.png',
    '/api/media/image/x.png%00',
    '/api/media/image/%E0%A4%A.png',
    '/api/media/image/x.png?download=1',
    '//evil.example/api/media/image/x.png',
    'http://127.0.0.1:4200/api/media/image/x.png',
    'file:///api/media/image/x.png',
    '/api/files/uploads/x.png',
    ' /api/media/image/x.png',
];

describe('resolveMediaSrc', () => {
    it('points a generated-media reference at the backend', () => {
        expect(resolveMediaSrc('image', '/api/media/image/sunset_1.png')).toBe('/api/media/image/sunset_1.png');
        expect(resolveMediaSrc('audio', '/api/media/audio/%E5%A4%95.wav')).toBe('/api/media/audio/%E5%A4%95.wav');
    });

    it.each(BAD_REFS)('rejects %j', (src) => {
        expect(resolveMediaSrc('image', src)).toBeNull();
    });

    it('rejects a reference of the other kind', () => {
        expect(resolveMediaSrc('image', '/api/media/audio/x.wav')).toBeNull();
        expect(resolveMediaSrc('audio', '/api/media/image/x.png')).toBeNull();
    });
});

describe('image card with a generated image', () => {
    it('shows the image from the backend with its caption, and expands on click', () => {
        const { container } = render(
            <RenderCard
                render="image"
                data={{ src: '/api/media/image/sunset_1.png', alt: 'sunset', caption: 'sunset', path: '/x/sunset_1.png' }}
            />,
        );
        const img = container.querySelector('img');
        expect(img?.getAttribute('src')).toBe('/api/media/image/sunset_1.png');
        expect(img?.getAttribute('alt')).toBe('sunset');
        expect(screen.getByText('sunset', { selector: 'figcaption' })).toBeInTheDocument();

        const toggle = screen.getByRole('button');
        expect(toggle.getAttribute('aria-expanded')).toBe('false');
        fireEvent.click(toggle);
        expect(toggle.getAttribute('aria-expanded')).toBe('true');
        expect(container.querySelector('.render-image--expanded')).not.toBeNull();
    });

    it.each(BAD_REFS)('rejects src %j — no <img>, invalid-payload fallback', (src) => {
        const { container } = render(<RenderCard render="image" data={{ src }} />);
        expect(container.querySelector('img')).toBeNull();
        expect(screen.getByText('Invalid image payload')).toBeInTheDocument();
    });
});

describe('audio card', () => {
    it('plays a generated track and shows its caption, file name and length', () => {
        const { container } = render(
            <RenderCard
                render="audio"
                data={{
                    src: '/api/media/audio/music_1.wav',
                    caption: 'calm piano',
                    path: 'C:\\Users\\u\\.gaia\\cache\\audio\\generations\\music_1.wav',
                    duration_seconds: 95.4,
                }}
            />,
        );
        const audio = container.querySelector('audio');
        expect(audio).not.toBeNull();
        expect(audio?.getAttribute('src')).toBe('/api/media/audio/music_1.wav');
        expect(audio?.hasAttribute('controls')).toBe(true);
        expect(screen.getByText('calm piano')).toBeInTheDocument();
        expect(container.textContent).toContain('music_1.wav · 1:35');
        expect(screen.queryByText(/Unsupported card type/)).toBeNull();
    });

    it('renders with only a src', () => {
        const { container } = render(<RenderCard render="audio" data={{ src: '/api/media/audio/sfx.wav' }} />);
        expect(container.querySelector('audio')).not.toBeNull();
        expect(container.querySelector('.render-audio__meta')).toBeNull();
    });

    it('shows a zero length rather than hiding it', () => {
        const { container } = render(
            <RenderCard render="audio" data={{ src: '/api/media/audio/sfx.wav', duration_seconds: 0 }} />,
        );
        expect(container.textContent).toContain('0:00');
    });

    it.each([
        ...BAD_REFS.map((src) => ({ src })),
        { src: 'data:audio/wav;base64,UklGRg==' },
        { src: '/api/media/audio/a.wav', duration_seconds: -1 },
        { src: '/api/media/audio/a.wav', duration_seconds: '95' },
        { src: '/api/media/audio/a.wav', duration_seconds: Number.NaN },
        { src: '/api/media/audio/a.wav', caption: 7 },
        { src: '/api/media/audio/a.wav', path: ['x'] },
    ])('rejects %j — no <audio>, invalid-payload fallback', (data) => {
        const { container } = render(<RenderCard render="audio" data={data} />);
        expect(container.querySelector('audio')).toBeNull();
        expect(screen.getByText('Invalid audio payload')).toBeInTheDocument();
    });

    it('rejects a non-object payload', () => {
        render(<RenderCard render="audio" data={null} />);
        expect(screen.getByText('Invalid audio payload')).toBeInTheDocument();
    });
});

describe('fromApiMessages card rehydration', () => {
    const base = { id: 3, session_id: 's', role: 'assistant', content: 'Done.', created_at: '', rag_sources: null };
    const audioData = { src: '/api/media/audio/music_1.wav', duration_seconds: 2 };

    it('rebuilds cards from the tool steps that persisted them, in order', () => {
        const [m] = fromApiMessages([
            {
                ...base,
                agent_steps: [
                    { id: 1, type: 'tool', label: 'a', timestamp: 0, render: 'image', data: { src: '/api/media/image/a.png' } },
                    { id: 2, type: 'tool', label: 'b', timestamp: 0 },
                    { id: 3, type: 'tool', label: 'c', timestamp: 0, render: 'audio', data: audioData },
                    { id: 4, type: 'tool', label: 'd', timestamp: 0, render: '' },
                ],
            },
        ]);
        expect(m.cards).toEqual([
            { render: 'image', data: { src: '/api/media/image/a.png' } },
            { render: 'audio', data: audioData },
        ]);
    });

    it('leaves cards unset when no step drew one, or there are no steps', () => {
        const [a, b] = fromApiMessages([
            { ...base, agent_steps: [{ id: 1, type: 'tool', label: 'a', timestamp: 0 }] },
            { ...base, id: 4, agent_steps: null },
        ]);
        expect(a.cards).toBeUndefined();
        expect(b.cards).toBeUndefined();
    });

    it('keeps cards the message already carries', () => {
        const own = [{ render: 'table', data: { columns: [], rows: [] } }];
        const [m] = fromApiMessages([
            { ...base, cards: own, agent_steps: [{ id: 1, type: 'tool', label: 'a', timestamp: 0, render: 'audio', data: audioData }] },
        ]);
        expect(m.cards).toBe(own);
    });
});
