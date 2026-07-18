// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for audit log management.
 *
 * Tracks all tool invocations across agents for security auditing,
 * compliance reporting, and rollback support.
 */

import { create } from 'zustand';
import type { AuditEntry, PermissionTier } from '../types/agent';

// ── Filter Types ─────────────────────────────────────────────────────────

export interface AuditFilters {
  agentId?: string;
  tool?: string;
  tier?: PermissionTier;
  success?: boolean;
  startDate?: number;  // timestamp
  endDate?: number;    // timestamp
  searchQuery?: string;
}

// ── State Interface ──────────────────────────────────────────────────────

interface AuditState {
  /** All audit entries (newest first). */
  entries: AuditEntry[];
  /** Active filters. */
  filters: AuditFilters;
  /** Whether the audit panel is open. */
  showPanel: boolean;
  /** Entry currently being rolled back (null if none). */
  rollbackTarget: string | null;

  // ── Entry Actions ───────────────────────────────────────────────────
  addEntry: (entry: AuditEntry) => void;
  addEntries: (entries: AuditEntry[]) => void;
  clearEntries: () => void;

  // ── Filter Actions ──────────────────────────────────────────────────
  setFilters: (filters: Partial<AuditFilters>) => void;
  clearFilters: () => void;

  // ── Rollback Actions ────────────────────────────────────────────────
  /**
   * Undo the recorded action by dispatching `agent/rollback` to the agent
   * that executed it, then mark the entry rolled back. Throws (and leaves
   * the entry unmarked) when the entry is missing, irreversible, already
   * rolled back, or the agent rejects/cannot be reached.
   */
  rollbackAction: (id: string) => Promise<void>;
  setRollbackTarget: (id: string | null) => void;

  // ── Export Actions ──────────────────────────────────────────────────
  exportCSV: () => void;

  // ── UI Actions ──────────────────────────────────────────────────────
  setShowPanel: (show: boolean) => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────

function formatCSVField(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function entriesToCSV(entries: AuditEntry[]): string {
  const headers = ['ID', 'Timestamp', 'Agent ID', 'Agent Name', 'Tool', 'Tier', 'Success', 'Result Summary', 'Reversible', 'Rolled Back'];
  const rows = entries.map((e) => [
    e.id,
    new Date(e.timestamp).toISOString(),
    e.agentId,
    formatCSVField(e.agentName),
    e.tool,
    e.tier,
    String(e.success),
    formatCSVField(e.resultSummary || ''),
    String(e.reversible),
    String(e.rolledBack ?? false),
  ].join(','));
  return [headers.join(','), ...rows].join('\n');
}

function downloadCSV(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // Delay revokeObjectURL to ensure the browser has started the download
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useAuditStore = create<AuditState>((set, get) => ({
  entries: [],
  filters: {},
  showPanel: false,
  rollbackTarget: null,

  addEntry: (entry) =>
    set((state) => ({
      entries: [entry, ...state.entries],
    })),

  addEntries: (entries) =>
    set((state) => ({
      entries: [...entries, ...state.entries],
    })),

  clearEntries: () => set({ entries: [] }),

  setFilters: (filters) =>
    set((state) => ({
      filters: { ...state.filters, ...filters },
    })),

  clearFilters: () => set({ filters: {} }),

  rollbackAction: async (id) => {
    const { entries } = get();
    const entry = entries.find((e) => e.id === id);
    if (!entry) {
      throw new Error(`Audit entry ${id} not found — it may have been cleared.`);
    }
    if (!entry.reversible) {
      throw new Error(
        `Action "${entry.tool}" (audit entry ${id}) is not reversible — ` +
          'the agent recorded it as irreversible, so there is nothing to undo.'
      );
    }
    if (entry.rolledBack) {
      throw new Error(`Audit entry ${id} has already been rolled back.`);
    }

    const sendRpc = window.gaiaAPI?.agent?.sendRpc;
    if (!sendRpc) {
      throw new Error(
        'Rollback unavailable: window.gaiaAPI.agent is not exposed. ' +
          'Rollback dispatches an agent/rollback RPC to the agent that ran ' +
          'the action, which requires the GAIA desktop app (Electron).'
      );
    }

    set({ rollbackTarget: id });
    try {
      // The agent that executed the tool is the only party that can undo
      // it — it must implement the `agent/rollback` JSON-RPC method and
      // reject when it cannot revert the action.
      await sendRpc(entry.agentId, 'agent/rollback', {
        audit_id: entry.id,
        tool: entry.tool,
        args: entry.args,
      });
      // Only mark rolled back AFTER the agent confirmed the undo.
      set((state) => ({
        entries: state.entries.map((e) =>
          e.id === id ? { ...e, rolledBack: true } : e
        ),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      throw new Error(
        `Rollback of "${entry.tool}" (audit entry ${id}) failed — the entry ` +
          `was NOT marked rolled back: ${message}`
      );
    } finally {
      set({ rollbackTarget: null });
    }
  },

  setRollbackTarget: (id) => set({ rollbackTarget: id }),

  exportCSV: () => {
    const { entries, filters } = get();
    const filtered = applyFilters(entries, filters);
    const csv = entriesToCSV(filtered);
    const timestamp = new Date().toISOString().slice(0, 10);
    downloadCSV(csv, `gaia-audit-log-${timestamp}.csv`);
  },

  setShowPanel: (show) => set({ showPanel: show }),
}));

// ── Filter Application ───────────────────────────────────────────────────

export function applyFilters(entries: AuditEntry[], filters: AuditFilters): AuditEntry[] {
  return entries.filter((e) => {
    if (filters.agentId && e.agentId !== filters.agentId) return false;
    if (filters.tool && e.tool !== filters.tool) return false;
    if (filters.tier && e.tier !== filters.tier) return false;
    if (filters.success !== undefined && e.success !== filters.success) return false;
    if (filters.startDate && e.timestamp < filters.startDate) return false;
    if (filters.endDate && e.timestamp > filters.endDate) return false;
    if (filters.searchQuery) {
      const query = filters.searchQuery.toLowerCase();
      const searchable = `${e.agentName} ${e.tool} ${e.resultSummary || ''}`.toLowerCase();
      if (!searchable.includes(query)) return false;
    }
    return true;
  });
}

// ── Selectors ────────────────────────────────────────────────────────────

/** Get filtered entries based on current filter state. */
export const selectFilteredEntries = (state: AuditState): AuditEntry[] =>
  applyFilters(state.entries, state.filters);

/** Get unique agent names from entries. */
export const selectUniqueAgents = (state: AuditState): string[] =>
  [...new Set(state.entries.map((e) => e.agentId))];

/** Get unique tool names from entries. */
export const selectUniqueTools = (state: AuditState): string[] =>
  [...new Set(state.entries.map((e) => e.tool))];
