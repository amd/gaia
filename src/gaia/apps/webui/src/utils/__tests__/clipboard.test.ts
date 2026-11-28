// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from '../clipboard';

function setClipboard(writeText: ((text: string) => Promise<void>) | null): void {
    Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: writeText ? { writeText } : undefined,
    });
}

function setExecCommand(result: boolean | Error) {
    const exec = vi.fn(() => {
        if (result instanceof Error) throw result;
        return result;
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: exec });
    return exec;
}

afterEach(() => {
    setClipboard(null);
    Reflect.deleteProperty(document, 'execCommand');
    document.body.innerHTML = '';
});

describe('copyText', () => {
    it('resolves through the Clipboard API without touching the textarea route', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        setClipboard(writeText);
        const exec = setExecCommand(false);

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(writeText).toHaveBeenCalledWith('hello');
        expect(exec).not.toHaveBeenCalled();
    });

    it('falls back to execCommand when the Clipboard API refuses', async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        let copiedValue = '';
        const exec = vi.fn(() => {
            copiedValue = document.querySelector('textarea')?.value ?? '';
            return true;
        });
        Object.defineProperty(document, 'execCommand', { configurable: true, value: exec });

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(exec).toHaveBeenCalledWith('copy');
        expect(copiedValue).toBe('hello');
        expect(document.querySelector('textarea')).toBeNull();
    });

    it('falls back to execCommand when there is no Clipboard API', async () => {
        setClipboard(null);
        const exec = setExecCommand(true);

        await expect(copyText('hello')).resolves.toBeUndefined();
        expect(exec).toHaveBeenCalledWith('copy');
    });

    it('rejects with the Clipboard API reason when execCommand also fails', async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(false);

        await expect(copyText('hello')).rejects.toThrow('denied');
        expect(document.querySelector('textarea')).toBeNull();
    });

    it('rejects when execCommand throws and there is no Clipboard API', async () => {
        setClipboard(null);
        setExecCommand(new Error('not supported'));

        await expect(copyText('hello')).rejects.toThrow('Clipboard is not available here');
    });

    it('puts focus back where it was after the textarea route', async () => {
        setClipboard(null);
        setExecCommand(true);
        const button = document.createElement('button');
        document.body.appendChild(button);
        button.focus();

        await copyText('hello');
        expect(document.activeElement).toBe(button);
    });
});
