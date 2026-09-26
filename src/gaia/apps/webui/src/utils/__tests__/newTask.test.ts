// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';

import { FLAGSHIP_AGENT_ID, planNewTask } from '../newTask';
import { DEFAULT_AGENT_ID } from '../sessionGrouping';
import type { AgentInfo, Session } from '../../types';

function agent(id: string, tiers?: AgentInfo['model_tiers']): AgentInfo {
    return {
        id,
        name: id,
        description: '',
        source: 'installed',
        conversation_starters: [],
        models: [],
        ...(tiers ? { model_tiers: tiers } : {}),
    };
}

function session(over: Partial<Session> = {}): Session {
    return {
        id: 's1',
        title: 'New Task',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        model: '',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        ...over,
    };
}

const base = { outgoingIsDraft: false, agents: [] as AgentInfo[], modelTier: 'full' };

describe('a new chat runs the flagship', () => {
    it('defaults to gaia when nothing was chosen', () => {
        const plan = planNewTask({ ...base });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
    });

    it('is not the legacy grouping default', () => {
        // The two constants are easy to conflate; they answer different
        // questions and unifying them breaks one side or the other.
        expect(FLAGSHIP_AGENT_ID).not.toBe(DEFAULT_AGENT_ID);
    });

    // The regression this file exists for: the shell used to read the agent
    // picker, which is synced from whichever session you last opened. Opening
    // an old doc chat therefore made your *next* new chat a doc chat.
    it('ignores the agent of the session being left behind', () => {
        const plan = planNewTask({
            ...base,
            outgoingSession: session({ agent_type: 'doc', message_count: 12 }),
        });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
    });

    it('treats an empty agent id as no choice at all', () => {
        expect(planNewTask({ ...base, explicitAgentId: '' })).toEqual({
            action: 'create',
            agentType: 'gaia',
        });
    });
});

describe('a deliberate agent choice still wins', () => {
    it('honours an explicit agent id', () => {
        expect(planNewTask({ ...base, explicitAgentId: 'email' })).toEqual({
            action: 'create',
            agentType: 'email',
        });
    });

    it('honours an explicit choice of a non-flagship agent over the default', () => {
        expect(planNewTask({ ...base, explicitAgentId: DEFAULT_AGENT_ID }).action).toBe('create');
        expect(planNewTask({ ...base, explicitAgentId: DEFAULT_AGENT_ID })).toMatchObject({
            agentType: 'chat',
        });
    });
});

describe('draft reuse (#2119)', () => {
    it('reuses an untouched draft that already runs the agent asked for', () => {
        const plan = planNewTask({
            ...base,
            outgoingIsDraft: true,
            outgoingSession: session({ agent_type: FLAGSHIP_AGENT_ID }),
        });
        expect(plan).toEqual({ action: 'reuse-draft' });
    });

    it('does not reuse a draft belonging to a different agent', () => {
        // Reusing it would leave the row on the wrong agent_type.
        const plan = planNewTask({
            ...base,
            outgoingIsDraft: true,
            outgoingSession: session({ agent_type: 'doc' }),
        });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
    });

    it('does not reuse a session that is not a draft', () => {
        const plan = planNewTask({
            ...base,
            outgoingIsDraft: false,
            outgoingSession: session({ agent_type: FLAGSHIP_AGENT_ID, message_count: 3 }),
        });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
    });

    it('does not reuse a legacy draft with no agent_type at all', () => {
        const plan = planNewTask({ ...base, outgoingIsDraft: true, outgoingSession: session() });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
    });
});

describe('model tier resolution (#1162)', () => {
    const agents = [
        agent('gaia', [
            { name: 'full', label: 'Full', models: [] },
            { name: 'lite', label: 'Lite (~4B)', models: ['Gemma-4-E4B-it-GGUF', 'Qwen3.5-4B-GGUF'] },
        ]),
    ];

    it('pins the first model of the selected tier', () => {
        expect(planNewTask({ ...base, agents, modelTier: 'lite' })).toEqual({
            action: 'create',
            agentType: 'gaia',
            model: 'Gemma-4-E4B-it-GGUF',
        });
    });

    it('leaves the model unset when the tier names none', () => {
        // "full" defers to the agent's own default — sending a model here would
        // override it with whatever the UI last had.
        const plan = planNewTask({ ...base, agents, modelTier: 'full' });
        expect(plan).toEqual({ action: 'create', agentType: 'gaia' });
        expect(plan).not.toHaveProperty('model');
    });

    it('resolves the tier against the agent actually being created', () => {
        // The tier belongs to gaia; an explicit doc task must not inherit it.
        expect(planNewTask({ ...base, agents, modelTier: 'lite', explicitAgentId: 'doc' })).toEqual({
            action: 'create',
            agentType: 'doc',
        });
    });

    it('leaves the model unset when the agent is not in the loaded list', () => {
        expect(planNewTask({ ...base, agents: [], modelTier: 'lite' })).toEqual({
            action: 'create',
            agentType: 'gaia',
        });
    });
});
