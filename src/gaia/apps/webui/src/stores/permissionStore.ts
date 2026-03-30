// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for per-agent tool permission overrides.
 *
 * Manages the mapping of agent IDs to their tool permission lists,
 * including user-applied tier overrides. The permission panel UI
 * uses this store to display and edit tool-level access control.
 */

import { create } from 'zustand';
import type { ToolPermission, PermissionTier } from '../types/agent';

// ── State Interface ──────────────────────────────────────────────────────

interface PermissionState {
  /** Tool permissions per agent (agentId → tool permissions). */
  permissions: Record<string, ToolPermission[]>;
  /** Whether the permission management panel is visible. */
  showPanel: boolean;
  /** Currently selected agent in the permission panel. */
  selectedAgentId: string | null;

  // ── Permission Actions ──────────────────────────────────────────────
  /** Set all permissions for an agent, replacing any existing list. */
  setPermissions: (agentId: string, permissions: ToolPermission[]) => void;
  /** Override a single tool's tier for an agent. */
  setToolOverride: (agentId: string, toolName: string, tier: PermissionTier) => void;
  /** Reset a single tool's override back to its default tier. */
  resetToolOverride: (agentId: string, toolName: string) => void;
  /** Reset all overrides for an agent (restore every tool to its default). */
  resetAllOverrides: (agentId: string) => void;

  // ── UI Actions ────────────────────────────────────────────────────────
  setShowPanel: (show: boolean) => void;
  setSelectedAgentId: (id: string | null) => void;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const usePermissionStore = create<PermissionState>((set) => ({
  // State
  permissions: {},
  showPanel: false,
  selectedAgentId: null,

  // ── Permission Actions ──────────────────────────────────────────────

  setPermissions: (agentId, permissions) =>
    set((state) => ({
      permissions: { ...state.permissions, [agentId]: permissions },
    })),

  setToolOverride: (agentId, toolName, tier) =>
    set((state) => {
      const existing = state.permissions[agentId];
      if (!existing) return state;
      return {
        permissions: {
          ...state.permissions,
          [agentId]: existing.map((tp) =>
            tp.tool === toolName ? { ...tp, overrideTier: tier } : tp
          ),
        },
      };
    }),

  resetToolOverride: (agentId, toolName) =>
    set((state) => {
      const existing = state.permissions[agentId];
      if (!existing) return state;
      return {
        permissions: {
          ...state.permissions,
          [agentId]: existing.map((tp) =>
            tp.tool === toolName ? { ...tp, overrideTier: undefined } : tp
          ),
        },
      };
    }),

  resetAllOverrides: (agentId) =>
    set((state) => {
      const existing = state.permissions[agentId];
      if (!existing) return state;
      return {
        permissions: {
          ...state.permissions,
          [agentId]: existing.map((tp) => ({ ...tp, overrideTier: undefined })),
        },
      };
    }),

  // ── UI Actions ────────────────────────────────────────────────────────

  setShowPanel: (show) => set({ showPanel: show }),
  setSelectedAgentId: (id) => set({ selectedAgentId: id }),
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get the tool permissions for a specific agent (empty array if none). */
export const selectAgentPermissions = (state: PermissionState, agentId: string): ToolPermission[] =>
  state.permissions[agentId] ?? [];

/** Get the count of tools with active overrides for a specific agent. */
export const selectOverrideCount = (state: PermissionState, agentId: string): number =>
  (state.permissions[agentId] ?? []).filter((tp) => tp.overrideTier !== undefined).length;
