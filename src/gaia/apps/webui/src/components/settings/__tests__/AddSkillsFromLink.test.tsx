// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AddSkillsFromLink } from '../AddSkillsFromLink';
import { useEscapeToClose } from '../../../hooks/useEscapeToClose';
import * as api from '../../../services/api';
import type { LinkPreview, LinkSkill } from '../../../types';
import { setUiLocale } from '../../../test/dates';
import { stripIsolates } from '../../../i18n/registry';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);
const LINK = 'https://github.com/owner/library';

function found(name: string, extra: Partial<LinkSkill> = {}): LinkSkill {
    return { name, description: `${name} does a job`, path: `skills/${name}`, has_code: false, permissions: [], permissions_unreadable: false, verdict: 'ALLOW', findings: [], installed: false, update: false, error: null, ...extra };
}

function InDialog({ onClose }: { onClose: () => void }) {
    useEscapeToClose(true, onClose);
    return <AddSkillsFromLink onAdded={vi.fn()} />;
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.previewSkillLink.mockResolvedValue({
        link: LINK,
        source: 'owner/library',
        pin: 'commit:abc',
        warning: 'Skills from a link are written by someone else.',
        skills: [
            found('deslop', { permissions: [{ grant: 'network:read:*.example.com', text: 'Read pages and data from sites matching *.example.com' }] }),
            found('babysit', { has_code: true }),
            found('evil', { verdict: 'BLOCK', error: 'Blocked by the security audit: curl | sh' }),
            found('bro', { installed: true }),
            found('tdd', { installed: true, update: true }),
        ],
    });
    mockedApi.listFeaturedSkillLibraries.mockResolvedValue({ libraries: [{ id: 'pstack', name: 'pstack', url: 'https://github.com/michael-denyer/pstack-claude', description: 'Engineering workflows' }] });
    mockedApi.addSkillsFromLink.mockResolvedValue({ added: [{ name: 'deslop', updated: false, verdict: 'ALLOW', has_code: false, findings: [] }], failed: {} });
});

async function readLink(first = 'deslop') {
    render(<AddSkillsFromLink onAdded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Link to a skill or skills library'), { target: { value: LINK } });
    fireEvent.click(screen.getByRole('button', { name: 'Read' }));
    await screen.findByText(first);
}

describe('AddSkillsFromLink', () => {
    it('shows the security warning before anything can be added', async () => {
        await readLink();
        expect(mockedApi.previewSkillLink).toHaveBeenCalledWith(LINK);
        expect(screen.getByRole('note')).toHaveTextContent('Only add skills from people you trust.');
        expect(screen.getByRole('note')).toHaveTextContent('written by someone else');
        expect(mockedApi.addSkillsFromLink).not.toHaveBeenCalled();
    });

    it('says what each skill may do before it can be added', async () => {
        await readLink();
        const perms = screen.getByRole('list', { name: 'What deslop can do' });
        expect(perms).toHaveTextContent('Read pages and data from sites matching *.example.com');
        expect(perms).toHaveTextContent('network:read:*.example.com');
        // Shown outright, not folded into Details.
        expect(perms.closest('details')).toBeNull();
        expect(screen.getAllByText("Asks for no permissions beyond GAIA's own tools.")).toHaveLength(4);
    });

    it('says a skill whose permissions could not be read is unknown, not empty', async () => {
        mockedApi.previewSkillLink.mockResolvedValue({
            link: LINK,
            source: 'owner/library',
            pin: 'commit:abc',
            warning: 'Skills from a link are written by someone else.',
            skills: [
                found('deslop'),
                found('garbled', { permissions_unreadable: true, error: "unknown permission 'teleport:everywhere'" }),
            ],
        });
        await readLink();
        expect(screen.getByText('Its permissions could not be read.')).toBeInTheDocument();
        expect(screen.getAllByText("Asks for no permissions beyond GAIA's own tools.")).toHaveLength(1);
    });

    it('preselects only instruction-only skills that are new and pass the audit', async () => {
        await readLink();
        expect(screen.getByLabelText('Add deslop')).toBeChecked();
        expect(screen.getByLabelText('Add babysit')).not.toBeChecked();
        expect(screen.getByLabelText('Add babysit')).toBeDisabled();
        expect(screen.getByLabelText('Add evil')).toBeDisabled();
        expect(screen.getByText('Blocked by the security audit: curl | sh')).toBeInTheDocument();
        expect(screen.getByLabelText('Add bro')).toBeDisabled();
        expect(screen.getByLabelText('Add tdd')).toBeChecked();
        expect(screen.getByText('update')).toBeInTheDocument();
    });

    it('needs an explicit opt-in before a skill that ships code can be picked', async () => {
        await readLink();
        fireEvent.click(screen.getByLabelText('Include skills with code'));
        fireEvent.click(screen.getByLabelText('Add babysit'));
        fireEvent.click(screen.getByRole('button', { name: 'Add 3' }));
        await screen.findByRole('status');
        expect(mockedApi.addSkillsFromLink).toHaveBeenCalledWith(LINK, ['deslop', 'tdd', 'babysit'], true, 'commit:abc');
    });

    it('adds the picked skills and reports them', async () => {
        const onAdded = vi.fn();
        render(<AddSkillsFromLink onAdded={onAdded} />);
        fireEvent.change(screen.getByLabelText('Link to a skill or skills library'), { target: { value: LINK } });
        fireEvent.click(screen.getByRole('button', { name: 'Read' }));
        fireEvent.click(await screen.findByLabelText('Add tdd'));
        fireEvent.click(await screen.findByRole('button', { name: 'Add 1' }));
        expect(await screen.findByRole('status')).toHaveTextContent('Added deslop.');
        expect(mockedApi.addSkillsFromLink).toHaveBeenCalledWith(LINK, ['deslop'], false, 'commit:abc');
        expect(onAdded).toHaveBeenCalledOnce();
    });

    it('reads a suggested library in one click', async () => {
        render(<AddSkillsFromLink onAdded={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: /Get pstack/ }));
        await screen.findByText('deslop');
        expect(mockedApi.previewSkillLink).toHaveBeenCalledWith('https://github.com/michael-denyer/pstack-claude');
    });

    it('shows why a link could not be read', async () => {
        mockedApi.previewSkillLink.mockRejectedValue(new Error('Nothing found at the link'));
        render(<AddSkillsFromLink onAdded={vi.fn()} />);
        fireEvent.change(screen.getByLabelText('Link to a skill or skills library'), { target: { value: 'owner/missing' } });
        fireEvent.click(screen.getByRole('button', { name: 'Read' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Nothing found at the link');
    });

    describe('a link with nothing behind it', () => {
        const MISSING = 'https://github.com/example-nonexistent-zz/meal-skills';
        // The 400 the backend sends for it, as api.ts parses it.
        const notFound = () => Object.assign(Object.create(api.ApiError.prototype), {
            httpStatus: 400,
            reason: 'link_not_found',
            body: { status: 'link_not_found', link: MISSING },
            detail: `Nothing found at ${MISSING}. Check the link — a private repository can't be read without signing in. If you have a copy of the skill, put its folder in the skills folder inside .gaia in your home folder.`,
        });

        afterEach(() => setUiLocale('en'));

        it('names the link the user pasted, in Arabic', async () => {
            setUiLocale('ar');
            mockedApi.previewSkillLink.mockRejectedValue(notFound());
            render(<AddSkillsFromLink onAdded={vi.fn()} />);
            fireEvent.change(screen.getByRole('textbox'), { target: { value: MISSING } });
            fireEvent.click(screen.getByRole('button', { name: /قراءة|Read/ }));
            const alert = stripIsolates((await screen.findByRole('alert')).textContent ?? '');
            expect(alert.startsWith(`لم يُعثر على شيء في ${MISSING}.`)).toBe(true);
            expect(alert).not.toContain('codeload');
            expect(alert).not.toContain('reopen');
        });
    });

    it('leaves the link alone on Escape while it is being read', async () => {
        let done: (p: LinkPreview) => void = () => {};
        mockedApi.previewSkillLink.mockReturnValue(new Promise((resolve) => { done = resolve; }));
        render(<AddSkillsFromLink onAdded={vi.fn()} />);
        const box = screen.getByLabelText('Link to a skill or skills library');
        fireEvent.change(box, { target: { value: LINK } });
        fireEvent.click(screen.getByRole('button', { name: 'Read' }));
        await screen.findByText('Reading…');
        fireEvent.keyDown(box, { key: 'Escape' });
        expect(box).toHaveValue(LINK);
        done({ link: LINK, source: 'owner/library', pin: 'commit:abc', warning: 'w', skills: [found('deslop')] });
        expect(await screen.findByText('deslop')).toBeInTheDocument();
    });

    it('dismisses the preview on Escape in the link box instead of closing Settings', async () => {
        const onClose = vi.fn();
        render(<InDialog onClose={onClose} />);
        const box = screen.getByLabelText('Link to a skill or skills library');
        fireEvent.change(box, { target: { value: LINK } });
        fireEvent.click(screen.getByRole('button', { name: 'Read' }));
        await screen.findByText('deslop');
        fireEvent.keyDown(box, { key: 'Escape' });
        expect(screen.queryByText('deslop')).not.toBeInTheDocument();
        expect(onClose).not.toHaveBeenCalled();
        expect(box).toHaveValue(LINK);
        // The box still holds the link, so the next Escape clears it; the one after closes Settings.
        fireEvent.keyDown(box, { key: 'Escape' });
        expect(box).toHaveValue('');
        expect(onClose).not.toHaveBeenCalled();
        fireEvent.keyDown(box, { key: 'Escape' });
        expect(onClose).toHaveBeenCalledOnce();
    });

    it('keeps the cursor in the link box while it reads, so a wrong link can be fixed', async () => {
        let fail: (e: Error) => void = () => {};
        mockedApi.previewSkillLink.mockReturnValue(new Promise((_, reject) => { fail = reject; }));
        render(<AddSkillsFromLink onAdded={vi.fn()} />);
        const box = screen.getByLabelText('Link to a skill or skills library');
        box.focus();
        fireEvent.change(box, { target: { value: 'owner/missing' } });
        fireEvent.submit(box.closest('form')!);
        await screen.findByText('Reading…');
        expect(box).not.toBeDisabled();
        expect(box).toHaveFocus();
        expect(box).toHaveAttribute('aria-busy', 'true');
        // A paste while reading neither changes the link nor starts a second read.
        fireEvent.paste(box, { clipboardData: { getData: () => 'owner/other' } });
        expect(box).toHaveValue('owner/missing');
        expect(mockedApi.previewSkillLink).toHaveBeenCalledTimes(1);
        fail(new Error('Nothing found at the link'));
        await screen.findByRole('alert');
        expect(box).toHaveFocus();
        expect(box).not.toHaveAttribute('readonly');
        expect(box).toHaveAttribute('aria-busy', 'false');
    });

    it('lets you read a cut-off description before adding the skill', async () => {
        const full = 'Draft a weekly meal plan from what is in the fridge, then list what is missing and group it by shop aisle.';
        mockedApi.previewSkillLink.mockResolvedValue({
            link: LINK, source: 'owner/library', pin: 'commit:abc', warning: 'w',
            skills: [found('meal-planner', { description: full })],
        });
        const scroll = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(80);
        const client = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(18);
        await readLink('meal-planner');
        const more = screen.getByRole('button', { name: 'Show more' });
        fireEvent.click(more);
        expect(screen.getByText(full).parentElement).toHaveClass('is-open');
        expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
        // Opening the text does not tick the skill's checkbox.
        expect(screen.getByLabelText('Add meal-planner')).toBeChecked();
        fireEvent.click(screen.getByText(full));
        expect(screen.getByLabelText('Add meal-planner')).toBeChecked();
        scroll.mockRestore();
        client.mockRestore();
    });
});
