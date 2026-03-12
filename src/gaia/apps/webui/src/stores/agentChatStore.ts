// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for per-agent interactive chat sessions.
 *
 * Separate from chatStore.ts (which handles the main ChatView HTTP SSE chat).
 * AgentChat uses IPC → JSON-RPC transport, not HTTP SSE.
 * See spec: docs/spec/agent-ui-server.mdx
 *
 * Uses Record<string, T> for Zustand devtools compatibility (Fix S4).
 */

import { create } from 'zustand';
import type {
  AgentChatMessage,
  AgentChatSession,
  AgentToolCall,
  QuickAction,
} from '../types/agent';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum messages kept per agent session. */
const MAX_MESSAGES_PER_AGENT = 100;

// ── State Interface ──────────────────────────────────────────────────────

interface AgentChatState {
  /** Chat sessions per agent (id → session). */
  sessions: Record<string, AgentChatSession>;

  // ── UI State ─────────────────────────────────────────────────────────
  /** Currently active agent chat (null = no chat open). */
  activeAgentId: string | null;
  /** Whether the agent chat panel is visible. */
  showAgentChat: boolean;
  /** Current input text (per-agent). */
  inputText: Record<string, string>;
  /** Whether a response is being awaited (per-agent). */
  isWaiting: Record<string, boolean>;

  // ── Session Actions ──────────────────────────────────────────────────
  /** Initialize or get a chat session for an agent. */
  getOrCreateSession: (agentId: string, agentName: string) => AgentChatSession;
  /** Set quick actions for an agent. */
  setQuickActions: (agentId: string, actions: QuickAction[]) => void;
  /** Clear all messages for an agent. */
  clearSession: (agentId: string) => void;
  /** Remove a session entirely. */
  removeSession: (agentId: string) => void;

  // ── Message Actions ──────────────────────────────────────────────────
  /** Add a user message and send it to the agent via IPC. */
  sendMessage: (agentId: string, content: string) => Promise<void>;
  /** Add a response message from the agent. */
  addAgentMessage: (agentId: string, message: AgentChatMessage) => void;
  /** Update the last agent message (e.g., for streaming or adding tool calls). */
  updateLastAgentMessage: (agentId: string, updates: Partial<AgentChatMessage>) => void;
  /** Execute a quick action (sends the corresponding RPC call). */
  executeQuickAction: (agentId: string, action: QuickAction) => Promise<void>;

  // ── Cross-Store Actions ─────────────────────────────────────────────
  /**
   * Clear the waiting state for an agent (e.g., when the agent is stopped
   * externally via agentStore.stopAgent while a chat RPC is in flight).
   */
  clearWaitingState: (agentId: string) => void;

  // ── UI Actions ───────────────────────────────────────────────────────
  openChat: (agentId: string, agentName: string) => void;
  closeChat: () => void;
  setShowAgentChat: (show: boolean) => void;
  setInputText: (agentId: string, text: string) => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────

/** Generate a unique message ID. */
function generateMessageId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Trim messages to the per-agent limit (keep newest). */
function trimMessages(messages: AgentChatMessage[]): AgentChatMessage[] {
  if (messages.length <= MAX_MESSAGES_PER_AGENT) return messages;
  return messages.slice(messages.length - MAX_MESSAGES_PER_AGENT);
}

/**
 * Null-safe helper to append a message to an agent's session.
 * Returns unchanged state if the session was removed during an async gap.
 */
function appendToSession(
  state: { sessions: Record<string, AgentChatSession>; isWaiting: Record<string, boolean> },
  agentId: string,
  message: AgentChatMessage,
  waiting: boolean
): Partial<typeof state> {
  const session = state.sessions[agentId];
  if (!session) return {};
  return {
    sessions: {
      ...state.sessions,
      [agentId]: {
        ...session,
        messages: trimMessages([...session.messages, message]),
      },
    },
    isWaiting: { ...state.isWaiting, [agentId]: waiting },
  };
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useAgentChatStore = create<AgentChatState>((set, get) => ({
  // State
  sessions: {},
  activeAgentId: null,
  showAgentChat: false,
  inputText: {},
  isWaiting: {},

  // ── Session Actions ──────────────────────────────────────────────────

  getOrCreateSession: (agentId, agentName) => {
    const existing = get().sessions[agentId];
    if (existing) return existing;
    const session: AgentChatSession = {
      agentId,
      agentName,
      messages: [],
      quickActions: [],
    };
    set((state) => {
      // Guard inside set() to prevent TOCTOU race if called concurrently
      if (state.sessions[agentId]) return state;
      return { sessions: { ...state.sessions, [agentId]: session } };
    });
    // Re-read from store to return the actual session (in case the guard fired
    // and a different caller's session was kept instead of ours)
    return get().sessions[agentId] ?? session;
  },

  setQuickActions: (agentId, actions) =>
    set((state) => {
      const session = state.sessions[agentId];
      if (!session) return state;
      return {
        sessions: {
          ...state.sessions,
          [agentId]: { ...session, quickActions: actions },
        },
      };
    }),

  clearSession: (agentId) =>
    set((state) => {
      const session = state.sessions[agentId];
      if (!session) return state;
      return {
        sessions: {
          ...state.sessions,
          [agentId]: { ...session, messages: [] },
        },
      };
    }),

  removeSession: (agentId) =>
    set((state) => {
      const { [agentId]: _removed, ...rest } = state.sessions;
      const { [agentId]: _removedInput, ...restInputs } = state.inputText;
      const { [agentId]: _removedWaiting, ...restWaiting } = state.isWaiting;
      return {
        sessions: rest,
        inputText: restInputs,
        isWaiting: restWaiting,
        activeAgentId: state.activeAgentId === agentId ? null : state.activeAgentId,
      };
    }),

  // ── Message Actions ──────────────────────────────────────────────────

  sendMessage: async (agentId, content) => {
    const { sessions } = get();
    const session = sessions[agentId];
    if (!session) {
      console.error(`[agentChatStore] No session for agent ${agentId}`);
      return;
    }

    // Add user message
    const userMessage: AgentChatMessage = {
      id: generateMessageId(),
      agentId,
      role: 'user',
      content,
      timestamp: Date.now(),
    };

    set((state) => {
      const sessionUpdate = appendToSession(state, agentId, userMessage, true);
      // Only clear input if the session still exists (wasn't removed during async gap)
      if (!state.sessions[agentId]) return state;
      return {
        ...sessionUpdate,
        inputText: { ...state.inputText, [agentId]: '' },
      };
    });

    // Send via IPC
    const api = window.gaiaAPI;
    if (!api) {
      // Fallback: add an error message
      const errorMessage: AgentChatMessage = {
        id: generateMessageId(),
        agentId,
        role: 'agent',
        content: 'Electron API not available. Agent chat requires the desktop app.',
        timestamp: Date.now(),
      };
      set((state) => appendToSession(state, agentId, errorMessage, false));
      return;
    }

    try {
      const result = await api.agent.sendRpc(agentId, 'agent/chat', {
        message: content,
        context: 'interactive_session',
      });

      // Parse response
      const response = result as {
        message?: string;
        tool_calls?: Array<{
          tool: string;
          args: Record<string, unknown>;
          result_summary?: string;
          success?: boolean;
        }>;
      };

      const toolCalls: AgentToolCall[] = (response.tool_calls ?? []).map((tc) => ({
        tool: tc.tool,
        args: tc.args,
        resultSummary: tc.result_summary,
        success: tc.success,
      }));

      const agentMessage: AgentChatMessage = {
        id: generateMessageId(),
        agentId,
        role: 'agent',
        content: response.message ?? JSON.stringify(result),
        timestamp: Date.now(),
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      };

      set((state) => appendToSession(state, agentId, agentMessage, false));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[agentChatStore] RPC error for ${agentId}:`, err);
      const errorMessage: AgentChatMessage = {
        id: generateMessageId(),
        agentId,
        role: 'agent',
        content: `Error: ${message}`,
        timestamp: Date.now(),
      };
      set((state) => appendToSession(state, agentId, errorMessage, false));
    }
  },

  addAgentMessage: (agentId, message) =>
    set((state) => {
      const session = state.sessions[agentId];
      if (!session) return state;
      return {
        sessions: {
          ...state.sessions,
          [agentId]: {
            ...session,
            messages: trimMessages([...session.messages, message]),
          },
        },
      };
    }),

  updateLastAgentMessage: (agentId, updates) =>
    set((state) => {
      const session = state.sessions[agentId];
      if (!session || session.messages.length === 0) return state;
      const messages = [...session.messages];
      // Find last agent message
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'agent') {
          messages[i] = { ...messages[i], ...updates };
          return {
            sessions: {
              ...state.sessions,
              [agentId]: { ...session, messages },
            },
          };
        }
      }
      return state;
    }),

  executeQuickAction: async (agentId, action) => {
    const api = window.gaiaAPI;
    if (!api) {
      console.warn('[agentChatStore] Electron API not available for quick action');
      return;
    }

    // Add a user-like message showing the quick action
    const actionMessage: AgentChatMessage = {
      id: generateMessageId(),
      agentId,
      role: 'user',
      content: `[${action.label}]`,
      timestamp: Date.now(),
    };

    set((state) => appendToSession(state, agentId, actionMessage, true));

    try {
      const result = await api.agent.sendRpc(agentId, action.method, action.params ?? {});

      const response = result as { message?: string };
      const agentMessage: AgentChatMessage = {
        id: generateMessageId(),
        agentId,
        role: 'agent',
        content: response.message ?? JSON.stringify(result, null, 2),
        timestamp: Date.now(),
      };

      set((state) => appendToSession(state, agentId, agentMessage, false));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[agentChatStore] Quick action error for ${agentId}:`, err);
      const errorMessage: AgentChatMessage = {
        id: generateMessageId(),
        agentId,
        role: 'agent',
        content: `Error executing ${action.label}: ${message}`,
        timestamp: Date.now(),
      };
      set((state) => appendToSession(state, agentId, errorMessage, false));
    }
  },

  // ── Cross-Store Actions ─────────────────────────────────────────────

  clearWaitingState: (agentId) =>
    set((state) => ({
      isWaiting: { ...state.isWaiting, [agentId]: false },
    })),

  // ── UI Actions ───────────────────────────────────────────────────────

  openChat: (agentId, agentName) => {
    get().getOrCreateSession(agentId, agentName);
    set({ activeAgentId: agentId, showAgentChat: true });
  },

  closeChat: () =>
    set({ showAgentChat: false }),

  setShowAgentChat: (show) =>
    set({ showAgentChat: show }),

  setInputText: (agentId, text) =>
    set((state) => ({
      inputText: { ...state.inputText, [agentId]: text },
    })),
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get the active chat session (for the currently open agent chat). */
export function selectActiveSession(state: AgentChatState): AgentChatSession | null {
  if (!state.activeAgentId) return null;
  return state.sessions[state.activeAgentId] ?? null;
}

/** Get messages for a specific agent. */
export function selectAgentMessages(
  state: AgentChatState,
  agentId: string
): AgentChatMessage[] {
  return state.sessions[agentId]?.messages ?? [];
}

/** Check if a response is being awaited for the active agent. */
export function selectIsWaiting(state: AgentChatState): boolean {
  if (!state.activeAgentId) return false;
  return state.isWaiting[state.activeAgentId] ?? false;
}

/** Get the input text for the active agent. */
export function selectInputText(state: AgentChatState): string {
  if (!state.activeAgentId) return '';
  return state.inputText[state.activeAgentId] ?? '';
}

/** Get total message count across all agent sessions. */
export function selectTotalMessageCount(state: AgentChatState): number {
  return Object.values(state.sessions).reduce(
    (sum, session) => sum + session.messages.length,
    0
  );
}
