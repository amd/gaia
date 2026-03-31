// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for pipeline template management.
 *
 * Handles template CRUD operations, validation, and error handling.
 */

import { create } from 'zustand';
import type { PipelineTemplate, TemplateListResponse, TemplateValidateResponse } from '../types';
import * as api from '../services/api';
import { log } from '../utils/logger';

// ── State Interface ──────────────────────────────────────────────────────

interface TemplateState {
  // State
  /** List of all templates */
  templates: PipelineTemplate[];
  /** Currently selected template for viewing/editing */
  selectedTemplate: PipelineTemplate | null;
  /** Raw YAML content of selected template */
  selectedTemplateRaw: string | null;
  /** Last validation result */
  lastValidation: TemplateValidateResponse | null;
  /** Whether templates are being loaded */
  isLoading: boolean;
  /** Whether saving (create/update) is in progress */
  isSaving: boolean;
  /** Error message from last failed operation */
  lastError: string | null;

  // Actions - State setters
  /** Set the list of templates */
  setTemplates: (templates: PipelineTemplate[]) => void;
  /** Set selected template */
  setSelectedTemplate: (template: PipelineTemplate | null) => void;
  /** Set raw YAML content */
  setSelectedTemplateRaw: (yaml: string | null) => void;
  /** Set last validation result */
  setLastValidation: (result: TemplateValidateResponse | null) => void;
  /** Set loading state */
  setIsLoading: (loading: boolean) => void;
  /** Set saving state */
  setIsSaving: (saving: boolean) => void;
  /** Set last error */
  setLastError: (error: string | null) => void;

  // Actions - CRUD operations
  /** Fetch all templates from server */
  fetchTemplates: () => Promise<void>;
  /** Fetch a single template by name */
  fetchTemplate: (name: string) => Promise<void>;
  /** Fetch raw YAML for a template */
  fetchTemplateRaw: (name: string) => Promise<void>;
  /** Create a new template */
  createTemplate: (data: { name: string; description?: string; quality_threshold?: number; max_iterations?: number; agent_categories?: Record<string, string[]>; routing_rules?: Array<{ condition: string; route_to: string; priority: number; loop_back: boolean; guidance?: string }>; quality_weights?: Record<string, number> }) => Promise<PipelineTemplate>;
  /** Update an existing template */
  updateTemplate: (name: string, data: { description?: string; quality_threshold?: number; max_iterations?: number; agent_categories?: Record<string, string[]>; routing_rules?: Array<{ condition: string; route_to: string; priority: number; loop_back: boolean; guidance?: string }>; quality_weights?: Record<string, number> }) => Promise<PipelineTemplate>;
  /** Delete a template */
  deleteTemplate: (name: string) => Promise<void>;
  /** Validate a template */
  validateTemplate: (name: string) => Promise<TemplateValidateResponse>;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useTemplateStore = create<TemplateState>((set, get) => ({
  // Initial state
  templates: [],
  selectedTemplate: null,
  selectedTemplateRaw: null,
  lastValidation: null,
  isLoading: false,
  isSaving: false,
  lastError: null,

  // State setters
  setTemplates: (templates) => set({ templates }),
  setSelectedTemplate: (template) => set({ selectedTemplate: template }),
  setSelectedTemplateRaw: (yaml) => set({ selectedTemplateRaw: yaml }),
  setLastValidation: (result) => set({ lastValidation: result }),
  setIsLoading: (loading) => set({ isLoading: loading }),
  setIsSaving: (saving) => set({ isSaving: saving }),
  setLastError: (error) => set({ lastError: error }),

  // CRUD operations
  fetchTemplates: async () => {
    set({ isLoading: true, lastError: null });
    try {
      const data = await api.listTemplates();
      set({ templates: data.templates, isLoading: false });
      log.ui.info(`[templateStore] Fetched ${data.total} templates`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to fetch templates: ${message}`, isLoading: false });
      log.ui.error('[templateStore] Failed to fetch templates:', err);
    }
  },

  fetchTemplate: async (name) => {
    set({ isLoading: true, lastError: null });
    try {
      const template = await api.getTemplate(name);
      set({ selectedTemplate: template, isLoading: false });
      log.ui.info(`[templateStore] Fetched template: ${name}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to fetch template ${name}: ${message}`, isLoading: false });
      log.ui.error('[templateStore] Failed to fetch template:', err);
    }
  },

  fetchTemplateRaw: async (name) => {
    set({ isLoading: true, lastError: null });
    try {
      const yaml = await api.getTemplateRaw(name);
      set({ selectedTemplateRaw: yaml, isLoading: false });
      log.ui.info(`[templateStore] Fetched raw YAML for template: ${name}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to fetch raw template ${name}: ${message}`, isLoading: false });
      log.ui.error('[templateStore] Failed to fetch raw template:', err);
    }
  },

  createTemplate: async (data) => {
    set({ isSaving: true, lastError: null });
    try {
      const template = await api.createTemplate(data);
      // Add to local list
      set((state) => ({
        templates: [...state.templates, template],
        selectedTemplate: template,
        isSaving: false,
      }));
      log.ui.info(`[templateStore] Created template: ${data.name}`);
      return template;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to create template: ${message}`, isSaving: false });
      log.ui.error('[templateStore] Failed to create template:', err);
      throw err;
    }
  },

  updateTemplate: async (name, data) => {
    set({ isSaving: true, lastError: null });
    try {
      const template = await api.updateTemplate(name, data);
      // Update in local list
      set((state) => ({
        templates: state.templates.map((t) => (t.name === name ? template : t)),
        selectedTemplate: template,
        isSaving: false,
      }));
      log.ui.info(`[templateStore] Updated template: ${name}`);
      return template;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to update template ${name}: ${message}`, isSaving: false });
      log.ui.error('[templateStore] Failed to update template:', err);
      throw err;
    }
  },

  deleteTemplate: async (name) => {
    set({ isSaving: true, lastError: null });
    try {
      await api.deleteTemplate(name);
      // Remove from local list
      set((state) => ({
        templates: state.templates.filter((t) => t.name !== name),
        selectedTemplate: state.selectedTemplate?.name === name ? null : state.selectedTemplate,
        isSaving: false,
      }));
      log.ui.info(`[templateStore] Deleted template: ${name}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to delete template ${name}: ${message}`, isSaving: false });
      log.ui.error('[templateStore] Failed to delete template:', err);
      throw err;
    }
  },

  validateTemplate: async (name) => {
    set({ isLoading: true, lastError: null });
    try {
      const result = await api.validateTemplate(name);
      set({ lastValidation: result, isLoading: false });
      log.ui.info(`[templateStore] Validated template: ${name} - valid: ${result.valid}`);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to validate template ${name}: ${message}`, isLoading: false });
      log.ui.error('[templateStore] Failed to validate template:', err);
      throw err;
    }
  },
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get template by name from the current list. */
export const selectTemplateByName = (name: string): ((state: TemplateState) => PipelineTemplate | undefined) =>
  (state: TemplateState) => state.templates.find((t) => t.name === name);

/** Get count of templates. */
export const selectTemplateCount = (state: TemplateState): number => state.templates.length;

/** Check if any templates exist. */
export const selectHasTemplates = (state: TemplateState): boolean => state.templates.length > 0;
