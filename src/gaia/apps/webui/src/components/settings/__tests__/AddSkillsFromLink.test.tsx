// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddSkillsFromLink } from '../AddSkillsFromLink';
import * as api from '../../../services/api';
import type { LinkSkill } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);
const LINK = 'https://github.com/owner/library';

function found(name: string, extra: Partial<LinkSkill> = {}): LinkSkill {
    return { name, description: `${name} does a job`, path: `skills/${name}`, has_code: false, verdict: 'ALLOW', findings: [], installed: false, update: false, error: null, ...extra };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.previewSkillLink.mockResolvedValue({
        link: LINK,
        source: 'owner/library',
        pin: 'commit:abc',
        warning: 'Skills from a link are written by someone else.',
        skills: [
            found('deslop'),
            found('babysit', { has_code: true }),
            found('evil', { verdict: 'BLOCK', error: 'Blocked by the security audit: curl | sh' }),
            found('bro', { installed: true }),
            found('tdd', { installed: true, update: true }),
        ],
    });
    mockedApi.listFeaturedSkillLibraries.mockResolvedValue({ libraries: [{ id: 'pstack', name: 'pstack', url: 'https://github.com/michael-denyer/pstack-claude', description: 'Engineering workflows' }] });
    mockedApi.addSkillsFromLink.mockResolvedValue({ added: [{ name: 'deslop', updated: false, verdict: 'ALLOW', has_code: false, findings: [] }], failed: {} });
});

async function readLink() {
    render(<AddSkillsFromLink onAdded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Link to a skill or skills library'), { target: { value: LINK } });
    fireEvent.click(screen.getByRole('button', { name: 'Read' }));
    await screen.findByText('deslop');
}

describe('AddSkillsFromLink', () => {
    it('shows the security warning before anything can be added', async () => {
        await readLink();
        expect(mockedApi.previewSkillLink).toHaveBeenCalledWith(LINK);
        expect(screen.getByRole('note')).toHaveTextContent('Only add skills from people you trust.');
        expect(screen.getByRole('note')).toHaveTextContent('written by someone else');
        expect(mockedApi.addSkillsFromLink).not.toHaveBeenCalled();
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
});
