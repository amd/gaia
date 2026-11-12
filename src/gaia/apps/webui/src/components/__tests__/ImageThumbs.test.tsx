// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Images the user sends show as small tiles that open full size, and the
 * viewer closes, steps and hands focus back the way a dialog should.
 */

import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ImageThumbs } from '../ImageThumbs';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const images = [
    { src: '/api/files/uploads/a.png', name: 'harbor.png' },
    { src: '/api/files/uploads/b.png', name: 'sunset.png' },
    { src: '/api/files/uploads/c.png', name: 'ridge.png' },
];

const sent = (content: string): Message => ({
    id: 1, session_id: 's', role: 'user', content, created_at: '2026-10-27T00:00:00.000Z', rag_sources: null,
});

describe('image thumbnails', () => {
    it('shows each image as a small tile, not a full-size picture', () => {
        const { container } = render(<ImageThumbs images={images} />);
        const tiles = container.querySelectorAll('.image-thumb');
        expect(tiles).toHaveLength(3);
        expect(container.querySelectorAll('.image-thumb img')).toHaveLength(3);
        expect(container.querySelector('.image-viewer')).toBeNull();
    });

    it('opens the clicked image in a viewer', () => {
        render(<ImageThumbs images={images} />);
        fireEvent.click(screen.getByRole('button', { name: /sunset\.png/ }));
        const dialog = screen.getByRole('dialog');
        expect(dialog.querySelector('img')?.getAttribute('src')).toBe('/api/files/uploads/b.png');
        expect(dialog.textContent).toContain('2 of 3');
    });

    it('opens from the keyboard', async () => {
        const user = (await import('@testing-library/user-event')).default.setup();
        render(<ImageThumbs images={images} />);
        screen.getByRole('button', { name: /harbor\.png/ }).focus();
        await user.keyboard('{Enter}');
        expect(screen.getByRole('dialog')).toBeTruthy();
        await user.keyboard('{Escape}');
        await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: /harbor\.png/ })));
        await user.keyboard(' ');
        expect(screen.getByRole('dialog')).toBeTruthy();
    });

    it('closes on Escape and returns focus to the thumbnail', async () => {
        render(<ImageThumbs images={images} />);
        const tile = screen.getByRole('button', { name: /harbor\.png/ });
        fireEvent.click(tile);
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).toBeNull();
        await waitFor(() => expect(document.activeElement).toBe(tile));
    });

    it('closes from the close button and from the backdrop', () => {
        render(<ImageThumbs images={images} />);
        fireEvent.click(screen.getByRole('button', { name: /harbor\.png/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Close' }));
        expect(screen.queryByRole('dialog')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /harbor\.png/ }));
        fireEvent.click(screen.getByRole('dialog'));
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('steps between the images of the message, wrapping at the ends', () => {
        render(<ImageThumbs images={images} />);
        fireEvent.click(screen.getByRole('button', { name: /harbor\.png/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
        expect(screen.getByRole('dialog').textContent).toContain('2 of 3');
        fireEvent.keyDown(document, { key: 'ArrowRight' });
        expect(screen.getByRole('dialog').textContent).toContain('3 of 3');
        fireEvent.keyDown(document, { key: 'ArrowRight' });
        expect(screen.getByRole('dialog').textContent).toContain('1 of 3');
        fireEvent.keyDown(document, { key: 'ArrowLeft' });
        expect(screen.getByRole('dialog').textContent).toContain('3 of 3');
        fireEvent.click(screen.getByRole('button', { name: 'Previous image' }));
        expect(screen.getByRole('dialog').textContent).toContain('2 of 3');
    });

    it('gives focus to the thumbnail now shown when closed after stepping', async () => {
        render(<ImageThumbs images={images} />);
        fireEvent.click(screen.getByRole('button', { name: /harbor\.png/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Next image' }));
        fireEvent.keyDown(document, { key: 'Escape' });
        await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: /sunset\.png/ })));
    });

    it('has no step buttons for a single image', () => {
        render(<ImageThumbs images={[images[0]]} />);
        fireEvent.click(screen.getByRole('button', { name: /harbor\.png/ }));
        expect(screen.queryByRole('button', { name: 'Next image' })).toBeNull();
        fireEvent.keyDown(document, { key: 'ArrowRight' });
        expect(screen.getByRole('dialog').querySelector('img')?.getAttribute('src')).toBe('/api/files/uploads/a.png');
    });
});

describe('images in a sent message', () => {
    it('become tiles under the text, and the text is shown without the image links', () => {
        const { container } = render(<MessageBubble message={sent([
            'Which one reads best?', '',
            '![harbor.png](/api/files/uploads/a.png)',
            '![sunset.png](/api/files/uploads/b.png)',
        ].join('\n'))} />);
        expect(container.querySelectorAll('.image-thumb')).toHaveLength(2);
        expect(container.querySelector('.md-content img')).toBeNull();
        expect(container.querySelector('.md-content')?.textContent).toContain('Which one reads best?');
    });

    it('leaves a picture linked from elsewhere where it was written', () => {
        const { container } = render(<MessageBubble message={sent('![x](https://example.com/x.png)')} />);
        expect(container.querySelectorAll('.image-thumb')).toHaveLength(0);
        expect(container.querySelector('.md-content img')).not.toBeNull();
    });
});
