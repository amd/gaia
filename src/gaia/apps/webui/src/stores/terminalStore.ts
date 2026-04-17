// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for agent terminal output buffers.
 *
 * Each agent has its own circular buffer of terminal lines (max 10,000).
 * Lines come from IPC: agent:stdout (JSON-RPC) and agent:stderr (log text).
 * Uses Record<string, T> for Zustand devtools compatibility (Fix S4).
 *
 * See spec: docs/spec/agent-ui-server.mdx
 */

import { create } from 'zustand';
import type { TerminalLine, TerminalTab, TerminalLineType, JsonRpcMessage } from '../types/agent';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum lines kept in memory per agent. */
const MAX_BUFFER_SIZE = 10_000;

// ── State Interface ──────────────────────────────────────────────────────

interface TerminalState {
  /** Terminal lines per agent (id → lines). Circular buffer, max MAX_BUFFER_SIZE. */
  buffers: Record<string, TerminalLine[]>;
  /** Active text filter per agent (id → filter string). */
  filters: Record<string, string>;
  /** Whether auto-scroll is paused per agent (id → paused). */
  paused: Record<string, boolean>;
  /** Active tab per agent (id → tab). */
  activeTabs: Record<string, TerminalTab>;
  /** Auto-incrementing line ID counter per agent. */
  lineCounters: Record<string, number>;

  // ── UI State ─────────────────────────────────────────────────────────
  /** Which agent's terminal is currently visible (null = none). */
  activeTerminalAgentId: string | null;
  /** Whether the terminal panel is visible. */
  showTerminal: boolean;

  // ── Buffer Actions ───────────────────────────────────────────────────
  /**
   * Append a raw terminal line to an agent's buffer.
   * Enforces the circular buffer limit.
   */
  appendLine: (agentId: string, line: Omit<TerminalLine, 'id'>) => void;
  /**
   * Append a parsed stdout JSON-RPC message as a terminal line.
   * Classifies the line type based on the RPC method.
   */
  appendStdoutMessage: (agentId: string, message: JsonRpcMessage) => void;
  /**
   * Append a raw stderr text line.
   * Auto-classifies as info/warn/error based on content.
   */
  appendStderrLine: (agentId: string, text: string) => void;
  /** Clear all lines for an agent. */
  clearBuffer: (agentId: string) => void;
  /** Remove all buffers (e.g., on app reset). */
  clearAllBuffers: () => void;

  // ── Filter / Pause Actions ───────────────────────────────────────────
  setFilter: (agentId: string, filter: string) => void;
  togglePause: (agentId: string) => void;
  setPaused: (agentId: string, paused: boolean) => void;

  // ── Tab Actions ──────────────────────────────────────────────────────
  setActiveTab: (agentId: string, tab: TerminalTab) => void;

  // ── UI Actions ───────────────────────────────────────────────────────
  openTerminal: (agentId: string) => void;
  closeTerminal: () => void;
  setShowTerminal: (show: boolean) => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────

/** Get the next line ID for an agent and increment the counter. */
function nextLineId(state: TerminalState, agentId: string): [number, Record<string, number>] {
  const current = state.lineCounters[agentId] ?? 0;
  const nextId = current + 1;
  return [nextId, { ...state.lineCounters, [agentId]: nextId }];
}

/** Word-boundary regex test (avoids false positives like "CALLBACK" matching "CALL"). */
const wordMatch = (text: string, word: string): boolean =>
  new RegExp(`\\b${word}\\b`).test(text);

/** Classify a stderr text line into a TerminalLineType based on content. */
function classifyStderrLine(text: string): TerminalLineType {
  const upper = text.toUpperCase();
  if (wordMatch(upper, 'ERROR') || wordMatch(upper, 'FATAL') || wordMatch(upper, 'EXCEPTION')) return 'error';
  if (wordMatch(upper, 'WARN') || wordMatch(upper, 'WARNING')) return 'warn';
  if (wordMatch(upper, 'TOOL') || wordMatch(upper, 'TOOL_CALL')) return 'tool';
  if (wordMatch(upper, 'PERMISSION') || wordMatch(upper, 'CONFIRM')) return 'permission';
  return 'info';
}

/** Classify a JSON-RPC message into a TerminalLineType. */
function classifyRpcMessage(message: JsonRpcMessage): TerminalLineType {
  if ('method' in message) {
    const method = message.method;
    if (method.startsWith('notification/')) return 'permission';
    if (method.startsWith('tools/')) return 'tool';
    return 'rpc';
  }
  // Response messages
  if ('error' in message && message.error) return 'error';
  return 'rpc';
}

/** Produce a human-readable summary of a JSON-RPC message. */
function summarizeRpcMessage(message: JsonRpcMessage): string {
  if ('method' in message) {
    const params = 'params' in message && message.params ? message.params : {};
    const paramStr = Object.keys(params).length > 0
      ? ` (${Object.keys(params).join(', ')})`
      : '';
    return `${message.method}${paramStr}`;
  }
  if ('error' in message && message.error) {
    return `Error ${message.error.code}: ${message.error.message}`;
  }
  return `Response [id=${('id' in message ? message.id : '?')}]`;
}

/** Enforce circular buffer limit by trimming oldest entries. */
function trimBuffer(buffer: TerminalLine[]): TerminalLine[] {
  if (buffer.length <= MAX_BUFFER_SIZE) return buffer;
  return buffer.slice(buffer.length - MAX_BUFFER_SIZE);
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useTerminalStore = create<TerminalState>((set) => ({
  // State
  buffers: {},
  filters: {},
  paused: {},
  activeTabs: {},
  lineCounters: {},
  activeTerminalAgentId: null,
  showTerminal: false,

  // ── Buffer Actions ───────────────────────────────────────────────────

  appendLine: (agentId, lineData) =>
    set((state) => {
      const [id, lineCounters] = nextLineId(state, agentId);
      const line: TerminalLine = { ...lineData, id };
      const existing = state.buffers[agentId] ?? [];
      const updated = trimBuffer([...existing, line]);
      return {
        buffers: { ...state.buffers, [agentId]: updated },
        lineCounters,
      };
    }),

  appendStdoutMessage: (agentId, message) =>
    set((state) => {
      const [id, lineCounters] = nextLineId(state, agentId);
      const type = classifyRpcMessage(message);
      const content = summarizeRpcMessage(message);
      const line: TerminalLine = {
        id,
        timestamp: Date.now(),
        type,
        source: 'stdout',
        content,
        rpcMessage: message,
        expandable: true,
        detail: JSON.stringify(message, null, 2),
      };
      const existing = state.buffers[agentId] ?? [];
      const updated = trimBuffer([...existing, line]);
      return {
        buffers: { ...state.buffers, [agentId]: updated },
        lineCounters,
      };
    }),

  appendStderrLine: (agentId, text) =>
    set((state) => {
      const trimmed = text.trimEnd();
      if (!trimmed) return state; // Skip empty lines
      const [id, lineCounters] = nextLineId(state, agentId);
      const type = classifyStderrLine(trimmed);
      const line: TerminalLine = {
        id,
        timestamp: Date.now(),
        type,
        source: 'stderr',
        content: trimmed,
      };
      const existing = state.buffers[agentId] ?? [];
      const updated = trimBuffer([...existing, line]);
      return {
        buffers: { ...state.buffers, [agentId]: updated },
        lineCounters,
      };
    }),

  clearBuffer: (agentId) =>
    set((state) => ({
      buffers: { ...state.buffers, [agentId]: [] },
      lineCounters: { ...state.lineCounters, [agentId]: 0 },
    })),

  clearAllBuffers: () =>
    set({ buffers: {}, lineCounters: {} }),

  // ── Filter / Pause Actions ───────────────────────────────────────────

  setFilter: (agentId, filter) =>
    set((state) => ({
      filters: { ...state.filters, [agentId]: filter },
    })),

  togglePause: (agentId) =>
    set((state) => ({
      paused: { ...state.paused, [agentId]: !(state.paused[agentId] ?? false) },
    })),

  setPaused: (agentId: string, paused: boolean) =>
    set((state) => ({
      paused: { ...state.paused, [agentId]: paused },
    })),

  // ── Tab Actions ──────────────────────────────────────────────────────

  setActiveTab: (agentId: string, tab: TerminalTab) =>
    set((state) => ({
      activeTabs: { ...state.activeTabs, [agentId]: tab },
    })),

  // ── UI Actions ───────────────────────────────────────────────────────

  openTerminal: (agentId: string) =>
    set({ activeTerminalAgentId: agentId, showTerminal: true }),

  closeTerminal: () =>
    set({ showTerminal: false }),

  setShowTerminal: (show: boolean) =>
    set({ showTerminal: show }),
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get filtered lines for an agent based on the active tab and text filter. */
export function selectFilteredLines(
  state: TerminalState,
  agentId: string
): TerminalLine[] {
  const lines = state.buffers[agentId] ?? [];
  const filter = (state.filters[agentId] ?? '').toLowerCase();
  const tab = state.activeTabs[agentId] ?? 'activity';

  // Tab filtering
  let tabFiltered: TerminalLine[];
  switch (tab) {
    case 'activity':
      // Show parsed activity: tool calls, permissions, errors, info — from both sources
      tabFiltered = lines.filter(
        (l) => l.type === 'tool' || l.type === 'permission' || l.type === 'error' || l.type === 'info' || l.type === 'warn'
      );
      break;
    case 'logs':
      // Show raw stderr output
      tabFiltered = lines.filter((l) => l.source === 'stderr');
      break;
    case 'raw':
      // Show raw stdout (JSON-RPC messages)
      tabFiltered = lines.filter((l) => l.source === 'stdout');
      break;
    default:
      tabFiltered = lines;
  }

  // Text filtering
  if (!filter) return tabFiltered;
  return tabFiltered.filter(
    (l) =>
      l.content.toLowerCase().includes(filter) ||
      (l.detail && l.detail.toLowerCase().includes(filter))
  );
}

/** Get total line count for an agent (unfiltered). */
export const selectLineCount = (state: TerminalState, agentId: string): number =>
  (state.buffers[agentId] ?? []).length;

/** Check if auto-scroll is paused for an agent. */
export const selectIsPaused = (state: TerminalState, agentId: string): boolean =>
  state.paused[agentId] ?? false;
