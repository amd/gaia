// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Composer } from '../Composer';
import { useAttachments } from '../../hooks/useAttachments';
import * as api from '../../services/api';
import type { SkillInfo } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function skill(name: string, description: string): SkillInfo {
    return { name, description, version: null, tier: 'local', tools: [], path: '' } as unknown as SkillInfo;
}

const SKILLS = [
    skill('pdf', 'Make and edit PDF files. Use when the user asks for a PDF.'),
    skill('pptx', 'Build slide decks.'),
    skill('web-research', 'Research a topic on the web.'),
];

function Harness({ onSubmit = vi.fn() }) {
    const [value, setValue] = useState('');
    const attachments = useAttachments();
    return <Composer value={value} onChange={setValue} onSubmit={onSubmit} attachments={attachments} />;
}

function box() {
    return screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement;
}

function type(text: string) {
    fireEvent.change(box(), { target: { value: text } });
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listSkills.mockResolvedValue({ skills: SKILLS, invalid: {} });
});

describe('Composer "/" skill commands', () => {
    it('lists installed skills matching what is typed, with a short description', async () => {
        render(<Harness />);
        type('/p');

        const options = await screen.findAllByRole('option');
        expect(options.map((o) => o.textContent)).toEqual([
            '/pdfMake and edit PDF files.',
            '/pptxBuild slide decks.',
        ]);
        expect(box()).toHaveAttribute('aria-activedescendant', options[0].id);
    });

    it('fills in the chosen name with arrows and Enter, without sending', async () => {
        const onSubmit = vi.fn();
        render(<Harness onSubmit={onSubmit} />);
        type('/p');
        await screen.findAllByRole('option');

        fireEvent.keyDown(box(), { key: 'ArrowDown' });
        fireEvent.keyDown(box(), { key: 'Enter' });

        expect(box().value).toBe('/pptx ');
        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.queryByRole('listbox')).toBeNull();
    });

    it('sends a name typed in full on Enter', async () => {
        const onSubmit = vi.fn();
        render(<Harness onSubmit={onSubmit} />);
        type('/pdf');
        await screen.findAllByRole('option');

        fireEvent.keyDown(box(), { key: 'Enter' });

        expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    it('fills in a clicked skill', async () => {
        render(<Harness />);
        type('/');
        fireEvent.click(await screen.findByText('/web-research'));

        expect(box().value).toBe('/web-research ');
    });

    it('hides on Escape and claims the key so a running reply is not stopped', async () => {
        render(<Harness />);
        type('/p');
        await screen.findAllByRole('option');

        const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
        act(() => { box().dispatchEvent(escape); });

        expect(escape.defaultPrevented).toBe(true);
        expect(screen.queryByRole('listbox')).toBeNull();
    });

    it('stays shut for paths and ordinary text', async () => {
        render(<Harness />);
        type('/tmp/notes.txt');
        type('summarize /pdf');
        type('/zzz');
        await Promise.resolve();

        expect(screen.queryByRole('listbox')).toBeNull();
    });

    it('says so when the skills cannot be read', async () => {
        mockedApi.listSkills.mockRejectedValue(new Error('GAIA is not running'));
        render(<Harness />);
        type('/');

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your skills: GAIA is not running");
    });
});
