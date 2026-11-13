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
        // The version is for support, not for choosing a skill: it sits in the tooltip.
        expect(screen.queryByText(/v1\.2\.0/)).not.toBeInTheDocument();
        expect(screen.getByText('gh-cli')).toHaveAttribute('title', 'v1.2.0');
        expect(screen.getByText('pdf-forms')).toBeInTheDocument();
    });

    it('lets you read a description that is cut off', async () => {
        const full = 'Run a short personal check-in. Use when the user says good morning.';
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('check-in', full)], invalid: {} });
        const scroll = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(80);
        const client = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(36);
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const row = (await screen.findByText('check-in')).closest('li')!;
        const more = within(row).getByRole('button', { name: 'Show more' });
        expect(more).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(more);
        expect(within(row).getByText(full).parentElement).toHaveClass('is-open');
        expect(within(row).getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
        scroll.mockRestore();
        client.mockRestore();
    });

    it('offers no toggle for a description that fits', async () => {
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const row = (await screen.findByText('pdf-forms')).closest('li')!;
        expect(within(row).queryByRole('button', { name: /Show/ })).not.toBeInTheDocument();
    });

    it('starts a new chat with the chosen skill', async () => {
        const onUseSkill = vi.fn();
        render(<SkillsSettings onUseSkill={onUseSkill} />);
        const row = (await screen.findByText('pdf-forms')).closest('li')!;
        fireEvent.click(within(row).getByRole('button', { name: 'Try pdf-forms in a new chat' }));
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
        fireEvent.click(within(selectableRow).getByRole('button', { name: 'Try gh-cli in a new chat' }));
        expect(onUseSkill).toHaveBeenCalledExactlyOnceWith('gh-cli');
    });

    it('explains a skipped skill in one plain line, without paths or developer advice', async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: [],
            invalid: {
                '/home/me/.claude/skills/modsmith': "/home/me/.claude/skills/modsmith/SKILL.md: description is 1025 characters; the limit is 1024. It is a trigger signal, not documentation — move the detail into the Markdown body. See https://amd-gaia.ai/docs/spec/agent-skills#x",
                '/skills/broken': 'missing name',
            },
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent("modsmith couldn't be loaded: its description is too long for GAIA");
        expect(alert).toHaveTextContent("broken couldn't be loaded: it isn't a valid skill file");
        expect(alert).not.toHaveTextContent('/home/me');
        expect(alert).not.toHaveTextContent('trigger signal');
        expect(alert).not.toHaveTextContent('https://');
        expect(screen.getByText('No skills are installed.')).toBeInTheDocument();
    });

    it('explains a skipped skill in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        mockedApi.listSkills.mockResolvedValue({
            skills: [],
            invalid: { '/x/modsmith': 'description is 1025 characters; the limit is 1024.' },
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent(/تعذّر تحميل .*modsmith.*وصفه أطول/);
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

    it('counts only the matching skills in each group while searching', async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: [
                skill('image-gen', 'Make pictures', null, 'agent-bundled'),
                skill('web-research', 'Research', null, 'agent-bundled'),
                skill('summarize', 'Summarize', null, 'agent-bundled'),
                skill('coding', 'Code', null, 'agent-bundled'),
                skill('old-helper', 'Imported', null, 'claude-import'),
                skill('image-resize', 'Resize images', null, 'claude-import'),
                skill('pdf-tools', 'PDFs', null, 'claude-import'),
            ],
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const countOf = (title: string) =>
            screen.getByText(title).closest('summary')!.querySelector('.settings-disclosure-count')!.textContent;
        await screen.findByRole('region', { name: 'Built into GAIA' });
        expect(countOf('Built into GAIA')).toBe('4');
        expect(countOf('From Claude Code')).toBe('3');
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search skills' }), { target: { value: 'image' } });
        expect(countOf('Built into GAIA')).toBe('1');
        expect(countOf('From Claude Code')).toBe('1');
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

    it('opens the built-in list, not an empty Yours, by default', async () => {
        mockedApi.listSkills.mockResolvedValue({ skills: [skill('web-research', 'Research', null, 'agent-bundled')], invalid: {} });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const bundled = await screen.findByRole('region', { name: 'Built into GAIA' });
        expect((bundled.closest('details') as HTMLDetailsElement).open).toBe(true);
        const yours = screen.getByRole('region', { name: 'Yours' });
        expect((yours.closest('details') as HTMLDetailsElement).open).toBe(false);
    });

    it('picks up a skill added while the page is open when the window gets focus back', async () => {
        mockedApi.listSkills.mockResolvedValueOnce({ skills: [skill('web-research', 'Research', null, 'agent-bundled')], invalid: {} });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        await screen.findByText('web-research');
        mockedApi.listSkills.mockResolvedValueOnce({
            skills: [skill('web-research', 'Research', null, 'agent-bundled'), skill('my-notes', 'Mine')],
            invalid: {},
        });
        fireEvent.focus(window);
        expect(await screen.findByText('my-notes')).toBeInTheDocument();
    });

    it('says when a search matches nothing, with a way to clear it', async () => {
        mockedApi.listSkills.mockResolvedValue({
            skills: Array.from({ length: 8 }, (_, i) => skill(`skill-${i}`, 'Something', null, 'agent-bundled')),
            invalid: {},
        });
        render(<SkillsSettings onUseSkill={vi.fn()} />);
        const search = await screen.findByRole('searchbox', { name: 'Search skills' });
        fireEvent.change(search, { target: { value: 'zzzz' } });
        expect(screen.getByRole('status')).toHaveTextContent('No skills match “zzzz”.');
        fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
        expect(search).toHaveValue('');
        expect(search).toHaveFocus();
        expect(screen.getByText('skill-0')).toBeInTheDocument();
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
