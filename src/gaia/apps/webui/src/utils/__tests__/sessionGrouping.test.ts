// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import {
    groupSessionsByAgent,
    resolveAgentName,
    resolveAgentIcon,
    DEFAULT_AGENT_ID,
} from '../sessionGrouping';
import type { Session, AgentInfo } from '../../types';

function session(id: string, agent_type: string | undefined, updated_at: string): Session {
    return {
        id,
        title: `Task ${id}`,
        created_at: updated_at,
        updated_at,
        model: 'm',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        ...(agent_type !== undefined ? { agent_type } : {}),
    };
}

function agent(id: string, name: string, icon?: string): AgentInfo {
    return {
        id,
        name,
        description: '',
        source: 'builtin',
        conversation_starters: [],
        models: [],
        ...(icon ? { icon } : {}),
    };
}

const AGENTS = [agent('chat', 'Chat Agent', 'message-circle'), agent('email', 'Email Triage', 'mail')];

describe('groupSessionsByAgent', () => {
    it('buckets sessions by agent_type', () => {
        const sessions = [
            session('a', 'chat', '2026-07-10T10:00:00Z'),
            session('b', 'email', '2026-07-10T11:00:00Z'),
            session('c', 'chat', '2026-07-10T09:00:00Z'),
        ];
        const groups = groupSessionsByAgent(sessions, AGENTS);
        expect(groups).toHaveLength(2);
        const chat = groups.find((g) => g.agentId === 'chat')!;
        expect(chat.name).toBe('Chat Agent');
        expect(chat.icon).toBe('message-circle');
        expect(chat.sessions.map((s) => s.id)).toEqual(['a', 'c']); // newest first
    });

    it('orders groups by most-recent session', () => {
        const sessions = [
            session('a', 'chat', '2026-07-10T10:00:00Z'),
            session('b', 'email', '2026-07-10T12:00:00Z'), // newest overall
        ];
        const groups = groupSessionsByAgent(sessions, AGENTS);
        expect(groups[0].agentId).toBe('email');
        expect(groups[1].agentId).toBe('chat');
    });

    it('falls back to the default agent id when agent_type is missing', () => {
        const groups = groupSessionsByAgent([session('a', undefined, '2026-07-10T10:00:00Z')], AGENTS);
        expect(groups).toHaveLength(1);
        expect(groups[0].agentId).toBe(DEFAULT_AGENT_ID);
        expect(groups[0].unknown).toBe(false);
    });

    it('marks unknown agents but still groups their sessions by id', () => {
        const groups = groupSessionsByAgent([session('a', 'ghost', '2026-07-10T10:00:00Z')], AGENTS);
        expect(groups[0].agentId).toBe('ghost');
        expect(groups[0].name).toBe('ghost'); // falls back to the id
        expect(groups[0].unknown).toBe(true);
    });

    it('returns an empty array for no sessions', () => {
        expect(groupSessionsByAgent([], AGENTS)).toEqual([]);
    });
});

describe('resolveAgentName / resolveAgentIcon', () => {
    it('resolves a known agent', () => {
        const s = session('a', 'email', '2026-07-10T10:00:00Z');
        expect(resolveAgentName(s, AGENTS)).toBe('Email Triage');
        expect(resolveAgentIcon(s, AGENTS)).toBe('mail');
    });

    it('falls back to the default agent when agent_type is missing', () => {
        const s = session('a', undefined, '2026-07-10T10:00:00Z');
        expect(resolveAgentName(s, AGENTS)).toBe('Chat Agent');
    });

    it('returns the raw id for an unknown agent', () => {
        const s = session('a', 'ghost', '2026-07-10T10:00:00Z');
        expect(resolveAgentName(s, AGENTS)).toBe('ghost');
        expect(resolveAgentIcon(s, AGENTS)).toBeUndefined();
    });
});
