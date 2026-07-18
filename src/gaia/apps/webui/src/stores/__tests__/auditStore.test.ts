// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * auditStore rollback — issue #2008.
 *
 * rollbackAction must actually perform the rollback (dispatch an
 * `agent/rollback` JSON-RPC to the agent that executed the action) and
 * only mark the entry rolled back after that succeeds. Failures must
 * raise actionable errors and leave the entry unmarked — a "successful"
 * rollback that reversed nothing is an audit-integrity violation.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useAuditStore } from '../auditStore';
import type { AuditEntry } from '../../types/agent';

const makeEntry = (overrides: Partial<AuditEntry> = {}): AuditEntry => ({
    id: 'a1',
    timestamp: 1750000000000,
    agentId: 'email-agent',
    agentName: 'Email Agent',
    tool: 'gmail_archive',
    tier: 'confirm',
    args: { threadId: 't-42' },
    success: true,
    resultSummary: 'archived thread t-42',
    reversible: true,
    ...overrides,
});

function getEntry(id: string): AuditEntry | undefined {
    return useAuditStore.getState().entries.find((e) => e.id === id);
}

describe('auditStore rollbackAction (issue #2008)', () => {
    beforeEach(() => {
        useAuditStore.setState({ entries: [], rollbackTarget: null });
    });

    afterEach(() => {
        delete (window as { gaiaAPI?: unknown }).gaiaAPI;
        vi.restoreAllMocks();
    });

    it('performs the rollback RPC before marking the entry rolled back', async () => {
        const sendRpc = vi.fn().mockResolvedValue({ ok: true });
        (window as { gaiaAPI?: unknown }).gaiaAPI = { agent: { sendRpc } };
        useAuditStore.getState().addEntry(makeEntry());

        await useAuditStore.getState().rollbackAction('a1');

        expect(sendRpc).toHaveBeenCalledWith('email-agent', 'agent/rollback', {
            audit_id: 'a1',
            tool: 'gmail_archive',
            args: { threadId: 't-42' },
        });
        expect(getEntry('a1')?.rolledBack).toBe(true);
        expect(useAuditStore.getState().rollbackTarget).toBeNull();
    });

    it('does NOT mark the entry rolled back when the rollback RPC fails', async () => {
        const sendRpc = vi
            .fn()
            .mockRejectedValue(new Error('Agent "email-agent" is not running'));
        (window as { gaiaAPI?: unknown }).gaiaAPI = { agent: { sendRpc } };
        useAuditStore.getState().addEntry(makeEntry());

        await expect(
            useAuditStore.getState().rollbackAction('a1')
        ).rejects.toThrow(/email-agent.*not running/);

        expect(getEntry('a1')?.rolledBack).toBeFalsy();
        expect(useAuditStore.getState().rollbackTarget).toBeNull();
    });

    it('raises an actionable error for irreversible actions and does not call the agent', async () => {
        const sendRpc = vi.fn();
        (window as { gaiaAPI?: unknown }).gaiaAPI = { agent: { sendRpc } };
        useAuditStore.getState().addEntry(makeEntry({ reversible: false }));

        await expect(
            useAuditStore.getState().rollbackAction('a1')
        ).rejects.toThrow(/not reversible/);

        expect(sendRpc).not.toHaveBeenCalled();
        expect(getEntry('a1')?.rolledBack).toBeFalsy();
    });

    it('rejects a second rollback of an already-rolled-back entry', async () => {
        const sendRpc = vi.fn();
        (window as { gaiaAPI?: unknown }).gaiaAPI = { agent: { sendRpc } };
        useAuditStore.getState().addEntry(makeEntry({ rolledBack: true }));

        await expect(
            useAuditStore.getState().rollbackAction('a1')
        ).rejects.toThrow(/already.*rolled back/i);

        expect(sendRpc).not.toHaveBeenCalled();
    });

    it('rejects unknown entry ids', async () => {
        await expect(
            useAuditStore.getState().rollbackAction('nope')
        ).rejects.toThrow(/not found/);
    });

    it('fails loudly when the Electron API is unavailable instead of faking success', async () => {
        // No window.gaiaAPI — browser mode has no rollback executor.
        useAuditStore.getState().addEntry(makeEntry());

        await expect(
            useAuditStore.getState().rollbackAction('a1')
        ).rejects.toThrow(/desktop app/);

        expect(getEntry('a1')?.rolledBack).toBeFalsy();
    });
});
