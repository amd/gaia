// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from '../Composer';
import { useAttachments } from '../../hooks/useAttachments';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function Harness({ initial = '', streaming = false, disabledReason = null as string | null, onSubmit = vi.fn(), onStop = vi.fn() }) {
    const [value, setValue] = useState(initial);
    const attachments = useAttachments();
    return (
        <Composer
            value={value}
            onChange={setValue}
            onSubmit={onSubmit}
            onStop={onStop}
            streaming={streaming}
            disabledReason={disabledReason}
            attachments={attachments}
        />
    );
}

beforeEach(() => {
    vi.clearAllMocks();
    // jsdom has no object URLs; image attachments ask for one.
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
});

describe('Composer', () => {
    it('sends on Enter', () => {
        const onSubmit = vi.fn();
        render(<Harness initial="hello" onSubmit={onSubmit} />);
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
        expect(onSubmit).toHaveBeenCalledOnce();
    });

    it('does not send on Shift+Enter', () => {
        const onSubmit = vi.fn();
        render(<Harness initial="hello" onSubmit={onSubmit} />);
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter', shiftKey: true });
        expect(onSubmit).not.toHaveBeenCalled();
    });

    it('disables Send and ignores Enter while the message is empty', () => {
        const onSubmit = vi.fn();
        render(<Harness initial="   " onSubmit={onSubmit} />);
        expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
        expect(onSubmit).not.toHaveBeenCalled();
    });

    it('enables Send once text is typed', () => {
        render(<Harness />);
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } });
        expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
    });

    it('shows Stop instead of Send while streaming', () => {
        const onStop = vi.fn();
        render(<Harness initial="next" streaming onStop={onStop} />);
        expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
        expect(onStop).toHaveBeenCalledOnce();
    });

    it('blocks typing and shows the reason when disabled', () => {
        render(<Harness initial="x" disabledReason="GAIA is starting…" />);
        const box = screen.getByLabelText('Message');
        expect(box).toBeDisabled();
        expect(box).toHaveAttribute('placeholder', 'GAIA is starting…');
        expect(screen.getByRole('button', { name: 'Attach files' })).toBeDisabled();
    });

    it('uploads attached files, lists them, and allows sending a file alone', async () => {
        let finish!: (v: Awaited<ReturnType<typeof api.uploadFile>>) => void;
        mockedApi.uploadFile.mockImplementation(() => new Promise((r) => { finish = r; }));
        const { container } = render(<Harness />);
        const input = container.querySelector('input[type="file"]') as HTMLInputElement;
        const file = new File(['a,b'], 'data.csv', { type: 'text/csv' });

        fireEvent.change(input, { target: { files: [file] } });

        expect(mockedApi.uploadFile).toHaveBeenCalledWith(file);
        const list = screen.getByRole('list', { name: 'Attached files' });
        expect(list).toHaveTextContent('data.csv');
        expect(list).toHaveTextContent('Uploading…');
        expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();

        finish({ url: '/api/files/uploads/data.csv' } as Awaited<ReturnType<typeof api.uploadFile>>);
        await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
        expect(list).not.toHaveTextContent('Uploading…');
    });

    it('shows an upload failure on the attachment', async () => {
        mockedApi.uploadFile.mockRejectedValue(new Error('File too large'));
        const { container } = render(<Harness />);
        const input = container.querySelector('input[type="file"]') as HTMLInputElement;
        fireEvent.change(input, { target: { files: [new File(['x'], 'big.bin')] } });

        expect(await screen.findByRole('alert')).toHaveTextContent('File too large');
        expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    });

    it('removes an attachment', async () => {
        mockedApi.uploadFile.mockResolvedValue({ url: '/u/a.txt' } as Awaited<ReturnType<typeof api.uploadFile>>);
        const { container } = render(<Harness />);
        const input = container.querySelector('input[type="file"]') as HTMLInputElement;
        fireEvent.change(input, { target: { files: [new File(['x'], 'a.txt')] } });

        fireEvent.click(await screen.findByRole('button', { name: 'Remove a.txt' }));
        expect(screen.queryByRole('list', { name: 'Attached files' })).not.toBeInTheDocument();
    });
});
