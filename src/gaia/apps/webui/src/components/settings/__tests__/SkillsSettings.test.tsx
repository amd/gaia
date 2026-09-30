// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsSettings } from '../SkillsSettings';
import * as api from '../../../services/api';
import type { SkillInfo } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

function skill(name: string, description: string, version: string | null = null): SkillInfo {
    return { name, description, version, tier: null, tools: [], path: `/skills/${name}/SKILL.md` };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listSkills.mockResolvedValue({
        skills: [skill('gh-cli', 'Work with GitHub from the terminal', '1.2.0'), skill('pdf-forms', 'Fill PDF forms')],
        invalid: {},
    });
});

describe('SkillsSettings', () => {
    it('lists the installed skills', async () => {
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByText('gh-cli')).toBeInTheDocument();
        expect(screen.getByText('Work with GitHub from the terminal')).toBeInTheDocument();
        expect(screen.getByText('v1.2.0')).toBeInTheDocument();
        expect(screen.getByText('pdf-forms')).toBeInTheDocument();
    });

    it('starts a new chat with the chosen skill', async () => {
        const onUseSkill = vi.fn();
        render(<SkillsSettings onUseSkill={onUseSkill} />);
        const row = (await screen.findByText('pdf-forms')).closest('li')!;
        fireEvent.click(within(row).getByRole('button', { name: 'Use pdf-forms in a new chat' }));
        expect(onUseSkill).toHaveBeenCalledExactlyOnceWith('pdf-forms');
    });

    it('names skills that could not be read', async () => {
        mockedApi.listSkills.mockResolvedValue({ skills: [], invalid: { '/skills/broken/SKILL.md': 'missing name' } });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('missing name');
        expect(screen.getByText('No skills are installed.')).toBeInTheDocument();
    });

    it('shows a load failure', async () => {
        mockedApi.listSkills.mockRejectedValue(new Error('Skills directory unreadable'));
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Skills directory unreadable');
    });
});
