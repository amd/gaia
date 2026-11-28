// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A sent message marks the skill command it ran, with the skill's description on hover. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import * as api from '../../services/api';
import type { Message, SkillInfo } from '../../types';

vi.mock('../../services/api');

const HUMANIZER = 'Rewrite text so it reads like a person wrote it. Use when editing prose for AI tells, filler and sales language.';

beforeEach(() => {
    vi.mocked(api.listSkills).mockResolvedValue({
        skills: [{ name: 'humanizer', description: HUMANIZER } as SkillInfo, { name: 'pdf', description: 'Make PDFs.' } as SkillInfo],
        invalid: {},
    });
});

function message(role: Message['role'], content: string): Message {
    return { id: 1, session_id: 's', role, content, created_at: '', rag_sources: null };
}

describe('a skill command in a sent message', () => {
    it('is marked where the user typed it, and only the one that ran', async () => {
        const { container } = render(<MessageBubble message={message('user', 'rewrite this with /Humanizer, then /pdf it')} />);

        await waitFor(() => expect(container.querySelector('.skill-mention')).not.toBeNull());
        const marks = container.querySelectorAll('.skill-mention');
        expect(marks).toHaveLength(1);
        expect(marks[0]).toHaveTextContent('/Humanizer');
        expect(container.querySelector('.md-content')).toHaveTextContent('rewrite this with /Humanizer, then /pdf it');
    });

    it("shows the skill's description on hover and on keyboard focus", async () => {
        const { container } = render(<MessageBubble message={message('user', '/humanizer the intro')} />);
        const mark = await waitFor(() => {
            const el = container.querySelector<HTMLElement>('.skill-mention');
            expect(el).not.toBeNull();
            return el!;
        });

        fireEvent.mouseEnter(mark);
        const tip = screen.getByRole('tooltip');
        expect(tip).toHaveTextContent('/humanizer');
        expect(tip).toHaveTextContent(HUMANIZER);
        expect(mark).toHaveAttribute('aria-describedby', tip.id);
        fireEvent.mouseLeave(mark);
        expect(screen.queryByRole('tooltip')).toBeNull();

        fireEvent.focus(mark);
        expect(screen.getByRole('tooltip')).toHaveTextContent(HUMANIZER);
    });

    it('leaves code, paths and unknown names unmarked', async () => {
        const { container } = render(<MessageBubble message={message('user', 'see `/humanizer` and /tmp/x.txt and /zzz')} />);
        await waitFor(() => expect(api.listSkills).toHaveBeenCalled());
        await Promise.resolve();
        expect(container.querySelector('.skill-mention')).toBeNull();
    });

    it("leaves an answer as written, even when it names a skill", async () => {
        const { container } = render(<MessageBubble message={message('assistant', 'I used /humanizer for that.')} />);
        await Promise.resolve();
        expect(container.querySelector('.skill-mention')).toBeNull();
        expect(api.listSkills).not.toHaveBeenCalled();
    });

    it('reads no skill list for a message that names no skill', async () => {
        render(<MessageBubble message={message('user', 'plain question about /etc/hosts')} />);
        await Promise.resolve();
        expect(api.listSkills).not.toHaveBeenCalled();
    });
});
