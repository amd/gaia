// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsSettings } from '../SkillsSettings';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import type { SkillInfo, SkillSource } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

function skill(name: string, description: string, version: string | null = null, source: SkillSource | null = 'user'): SkillInfo {
    return { name, description, version, tier: null, tools: [], path: `/skills/${name}/SKILL.md`, source };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listFeaturedSkillLibraries.mockResolvedValue({ libraries: [] });
    mockedApi.listSkills.mockResolvedValue({
        skills: [skill('gh-cli', 'Work with GitHub from the terminal', '1.2.0'), skill('pdf-forms', 'Fill PDF forms')],
        invalid: {},
    });
});

afterEach(() => useChatStore.setState({ locale: 'en' }));

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

    it('labels always-on skills without offering a chat action', async () => {
        const onUseSkill = vi.fn();
        mockedApi.listSkills.mockResolvedValue({
            skills: [
                { ...skill('gaia-voice', 'Honesty floor'), always_on: true },
                { ...skill('gh-cli', 'Work with GitHub'), always_on: false },
            ],
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={onUseSkill} />);
        const row = (await screen.findByText('gaia-voice')).closest('li')!;
        expect(within(row).getByText('· Always on')).toBeInTheDocument();
        expect(within(row).queryByRole('button')).not.toBeInTheDocument();
        const selectableRow = screen.getByText('gh-cli').closest('li')!;
        fireEvent.click(within(selectableRow).getByRole('button', { name: 'Use gh-cli in a new chat' }));
        expect(onUseSkill).toHaveBeenCalledExactlyOnceWith('gh-cli');
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

    it('groups skills by where they came from', async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: [
                skill('web-research', 'Research', null, 'agent-bundled'),
                skill('old-helper', 'Imported', null, 'claude-import'),
            ],
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const bundled = await screen.findByRole('region', { name: 'Built into GAIA' });
        expect(within(bundled).getByText('web-research')).toBeInTheDocument();
        const imported = screen.getByRole('region', { name: 'From Claude Code' });
        expect(within(imported).getByText('old-helper')).toBeInTheDocument();
        expect(within(imported).queryByText('web-research')).not.toBeInTheDocument();
    });

    it('shows every skill, flat, when one comes from a source it has no group for', async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: [
                skill('web-research', 'Research', null, 'agent-bundled'),
                skill('future-helper', 'From a newer agent', null, 'some-future-root' as SkillSource),
            ],
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByText('future-helper')).toBeInTheDocument();
        expect(screen.getByText('web-research')).toBeInTheDocument();
        expect(screen.queryByRole('region', { name: 'Built into GAIA' })).not.toBeInTheDocument();
    });

    it("shows the project's own skills apart from other Claude Code skills", async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: [
                skill('deploy-helper', 'Deploys the app', null, 'claude-project'),
                skill('old-helper', 'Imported', null, 'claude-import'),
            ],
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const project = await screen.findByRole('region', { name: 'From this project' });
        expect(within(project).getByText('deploy-helper')).toBeInTheDocument();
        expect(within(project).queryByText('old-helper')).not.toBeInTheDocument();
        const imported = screen.getByRole('region', { name: 'From Claude Code' });
        expect(within(imported).queryByText('deploy-helper')).not.toBeInTheDocument();
    });

    it('says how to add your own skills when there are none', async () => {
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('web-research', 'Research', null, 'agent-bundled')], invalid: {} });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const yours = await screen.findByRole('region', { name: 'Yours' });
        expect(yours).toHaveTextContent('None yet');
    });

    it('lists a flat set when the agent does not report sources', async () => {
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('legacy', 'Old agent', null, null)], invalid: {} });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByText('legacy')).toBeInTheDocument();
        expect(screen.queryByRole('region')).not.toBeInTheDocument();
    });

    it('keeps the skills folder left to right in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('web-research', 'Research', null, 'agent-bundled')], invalid: {} });
        const { container } = render(<SkillsSettings onUseSkill={vi.fn()} />);
        await screen.findByRole('region', { name: /مهاراتك/ });
        const text = container.textContent ?? '';
        const raw = text.split('~/.gaia/skills').length - 1;
        const isolated = text.split('\u2068~/.gaia/skills\u2069').length - 1;
        expect(raw).toBeGreaterThanOrEqual(3);
        expect(isolated).toBe(raw);
    });

    it('keeps the project skills folder left to right in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('deploy-helper', 'Deploy', null, 'claude-project')], invalid: {} });
        const { container } = render(<SkillsSettings onUseSkill={vi.fn()} />);
        await screen.findByText('deploy-helper');
        const text = container.textContent ?? '';
        const raw = text.split('.claude/skills').length - 1;
        const isolated = text.split('\u2068.claude/skills\u2069').length - 1;
        expect(raw).toBe(2);
        expect(isolated).toBe(raw);
    });
});
