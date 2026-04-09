// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Unit tests for templateStore - Pipeline template management.
 *
 * Tests cover:
 * - Template CRUD operations (create, read, update, delete)
 * - Loading and error states
 * - Validation workflow
 * - Raw YAML fetching
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useTemplateStore, selectTemplateByName, selectTemplateCount, selectHasTemplates } from '../templateStore';
import * as api from '../../services/api';

// Mock the API module
vi.mock('../../services/api', () => ({
  listTemplates: vi.fn(),
  getTemplate: vi.fn(),
  getTemplateRaw: vi.fn(),
  createTemplate: vi.fn(),
  updateTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  validateTemplate: vi.fn(),
}));

// Mock logger
vi.mock('../../utils/logger', () => ({
  log: {
    ui: {
      info: vi.fn(),
      error: vi.fn(),
      warn: vi.fn(),
      timed: vi.fn(),
      time: vi.fn(() => 0),
    },
    api: {
      info: vi.fn(),
      error: vi.fn(),
      warn: vi.fn(),
      timed: vi.fn(),
      time: vi.fn(() => 0),
    },
  },
}));

// Sample template data for tests
const sampleTemplate = {
  name: 'test-template',
  description: 'Test template for unit tests',
  quality_threshold: 0.90,
  max_iterations: 10,
  agent_categories: {
    planning: ['planner'],
    development: ['developer'],
    quality: ['reviewer'],
  },
  routing_rules: [
    {
      condition: "defect_type == 'security'",
      route_to: 'security-auditor',
      priority: 1,
      loop_back: true,
      guidance: 'Fix security issues first',
    },
  ],
  quality_weights: {
    code_quality: 0.25,
    requirements_coverage: 0.25,
    testing: 0.25,
    documentation: 0.15,
    best_practices: 0.10,
  },
};

const templateListResponse = {
  templates: [sampleTemplate],
  total: 1,
};

const validationResponse = {
  valid: true,
  errors: [],
  warnings: ['Consider adding more routing rules for better coverage'],
};

describe('useTemplateStore', () => {
  beforeEach(() => {
    // Reset store to initial state
    useTemplateStore.setState({
      templates: [],
      selectedTemplate: null,
      selectedTemplateRaw: null,
      lastValidation: null,
      isLoading: false,
      isSaving: false,
      lastError: null,
    });

    // Reset all mocks
    vi.clearAllMocks();
  });

  describe('State Initialization', () => {
    it('should have correct initial state', () => {
      const state = useTemplateStore.getState();
      expect(state.templates).toEqual([]);
      expect(state.selectedTemplate).toBeNull();
      expect(state.selectedTemplateRaw).toBeNull();
      expect(state.lastValidation).toBeNull();
      expect(state.isLoading).toBe(false);
      expect(state.isSaving).toBe(false);
      expect(state.lastError).toBeNull();
    });
  });

  describe('fetchTemplates', () => {
    it('should fetch templates successfully', async () => {
      vi.mocked(api.listTemplates).mockResolvedValue(templateListResponse);

      await act(async () => {
        await useTemplateStore.getState().fetchTemplates();
      });

      const state = useTemplateStore.getState();
      expect(api.listTemplates).toHaveBeenCalledTimes(1);
      expect(state.templates).toEqual([sampleTemplate]);
      expect(state.isLoading).toBe(false);
      expect(state.lastError).toBeNull();
    });

    it('should handle fetch error', async () => {
      const errorMessage = 'Network error';
      vi.mocked(api.listTemplates).mockRejectedValue(new Error(errorMessage));

      await act(async () => {
        await useTemplateStore.getState().fetchTemplates();
      });

      const state = useTemplateStore.getState();
      expect(state.templates).toEqual([]);
      expect(state.isLoading).toBe(false);
      expect(state.lastError).toContain('Failed to fetch templates');
      expect(state.lastError).toContain(errorMessage);
    });

    it('should set loading state during fetch', async () => {
      let loadingDuringFetch = false;

      vi.mocked(api.listTemplates).mockImplementation(async () => {
        loadingDuringFetch = useTemplateStore.getState().isLoading;
        return templateListResponse;
      });

      await act(async () => {
        await useTemplateStore.getState().fetchTemplates();
      });

      expect(loadingDuringFetch).toBe(true);
    });
  });

  describe('fetchTemplate', () => {
    it('should fetch a single template successfully', async () => {
      vi.mocked(api.getTemplate).mockResolvedValue(sampleTemplate);

      await act(async () => {
        await useTemplateStore.getState().fetchTemplate('test-template');
      });

      const state = useTemplateStore.getState();
      expect(api.getTemplate).toHaveBeenCalledWith('test-template');
      expect(state.selectedTemplate).toEqual(sampleTemplate);
      expect(state.isLoading).toBe(false);
    });

    it('should handle template not found', async () => {
      vi.mocked(api.getTemplate).mockRejectedValue(new Error('Template not found'));

      await act(async () => {
        await useTemplateStore.getState().fetchTemplate('nonexistent');
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplate).toBeNull();
      expect(state.lastError).toContain('Failed to fetch template nonexistent');
    });

    it('should handle validation errors', async () => {
      const invalidTemplate = {
        ...sampleTemplate,
        quality_threshold: 1.5, // Invalid: must be <= 1.0
      };
      vi.mocked(api.getTemplate).mockRejectedValue(
        new Error('quality_threshold must be between 0 and 1')
      );

      await act(async () => {
        await useTemplateStore.getState().fetchTemplate('invalid-template');
      });

      const state = useTemplateStore.getState();
      expect(state.lastError).toContain('quality_threshold');
    });
  });

  describe('fetchTemplateRaw', () => {
    it('should fetch raw YAML successfully', async () => {
      const rawYaml = `name: test-template
description: Test template
quality_threshold: 0.9
max_iterations: 10
`;
      vi.mocked(api.getTemplateRaw).mockResolvedValue(rawYaml);

      await act(async () => {
        await useTemplateStore.getState().fetchTemplateRaw('test-template');
      });

      const state = useTemplateStore.getState();
      expect(api.getTemplateRaw).toHaveBeenCalledWith('test-template');
      expect(state.selectedTemplateRaw).toBe(rawYaml);
      expect(state.isLoading).toBe(false);
    });

    it('should handle raw YAML fetch error', async () => {
      vi.mocked(api.getTemplateRaw).mockRejectedValue(new Error('File not found'));

      await act(async () => {
        await useTemplateStore.getState().fetchTemplateRaw('missing-template');
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplateRaw).toBeNull();
      expect(state.lastError).toContain('Failed to fetch raw template');
    });
  });

  describe('createTemplate', () => {
    it('should create a new template successfully', async () => {
      vi.mocked(api.createTemplate).mockResolvedValue(sampleTemplate);

      let createdTemplate;
      await act(async () => {
        createdTemplate = await useTemplateStore.getState().createTemplate({
          name: 'test-template',
          description: 'Test template',
          quality_threshold: 0.90,
          max_iterations: 10,
        });
      });

      expect(api.createTemplate).toHaveBeenCalledWith({
        name: 'test-template',
        description: 'Test template',
        quality_threshold: 0.90,
        max_iterations: 10,
      });
      expect(createdTemplate).toEqual(sampleTemplate);

      const state = useTemplateStore.getState();
      expect(state.templates).toContainEqual(sampleTemplate);
      expect(state.selectedTemplate).toEqual(sampleTemplate);
      expect(state.isSaving).toBe(false);
    });

    it('should handle creation error', async () => {
      vi.mocked(api.createTemplate).mockRejectedValue(
        new Error('Template name already exists')
      );

      await expect(
        useTemplateStore.getState().createTemplate({ name: 'duplicate' })
      ).rejects.toThrow('Template name already exists');

      const state = useTemplateStore.getState();
      expect(state.lastError).toContain('Failed to create template');
      expect(state.isSaving).toBe(false);
    });

    it('should handle validation error during creation', async () => {
      vi.mocked(api.createTemplate).mockRejectedValue(
        new Error('Quality weights must sum to 1.0')
      );

      await expect(
        useTemplateStore.getState().createTemplate({
          name: 'bad-weights',
          quality_weights: { a: 0.3, b: 0.3 },
        })
      ).rejects.toThrow();

      const state = useTemplateStore.getState();
      expect(state.lastError).toContain('Failed to create template');
    });
  });

  describe('updateTemplate', () => {
    const updatedTemplate = {
      ...sampleTemplate,
      description: 'Updated description',
      quality_threshold: 0.95,
    };

    beforeEach(() => {
      // Pre-populate store with template
      useTemplateStore.setState({
        templates: [sampleTemplate],
        selectedTemplate: sampleTemplate,
      });
    });

    it('should update an existing template successfully', async () => {
      vi.mocked(api.updateTemplate).mockResolvedValue(updatedTemplate);

      let result;
      await act(async () => {
        result = await useTemplateStore.getState().updateTemplate('test-template', {
          description: 'Updated description',
          quality_threshold: 0.95,
        });
      });

      expect(api.updateTemplate).toHaveBeenCalledWith('test-template', {
        description: 'Updated description',
        quality_threshold: 0.95,
      });
      expect(result).toEqual(updatedTemplate);

      const state = useTemplateStore.getState();
      expect(state.templates[0].description).toBe('Updated description');
      expect(state.selectedTemplate?.description).toBe('Updated description');
    });

    it('should handle update error', async () => {
      vi.mocked(api.updateTemplate).mockRejectedValue(new Error('Template not found'));

      await expect(
        useTemplateStore.getState().updateTemplate('nonexistent', {
          description: 'Update',
        })
      ).rejects.toThrow();

      const state = useTemplateStore.getState();
      expect(state.lastError).toContain('Failed to update template');
    });

    it('should update template in local list', async () => {
      vi.mocked(api.updateTemplate).mockResolvedValue(updatedTemplate);

      await act(async () => {
        await useTemplateStore.getState().updateTemplate('test-template', {
          description: 'New description',
        });
      });

      const state = useTemplateStore.getState();
      expect(state.templates.length).toBe(1);
      expect(state.templates[0].description).toBe('New description');
    });
  });

  describe('deleteTemplate', () => {
    beforeEach(() => {
      useTemplateStore.setState({
        templates: [sampleTemplate],
        selectedTemplate: sampleTemplate,
      });
    });

    it('should delete a template successfully', async () => {
      vi.mocked(api.deleteTemplate).mockResolvedValue({
        deleted: true,
        template: 'test-template',
      });

      await act(async () => {
        await useTemplateStore.getState().deleteTemplate('test-template');
      });

      expect(api.deleteTemplate).toHaveBeenCalledWith('test-template');

      const state = useTemplateStore.getState();
      expect(state.templates).toEqual([]);
      expect(state.selectedTemplate).toBeNull();
    });

    it('should handle delete error', async () => {
      vi.mocked(api.deleteTemplate).mockRejectedValue(
        new Error('Cannot delete template')
      );

      await expect(
        useTemplateStore.getState().deleteTemplate('test-template')
      ).rejects.toThrow();

      const state = useTemplateStore.getState();
      expect(state.templates.length).toBe(1); // Template still exists
      expect(state.lastError).toContain('Failed to delete template');
    });

    it('should clear selected template if deleted template was selected', async () => {
      vi.mocked(api.deleteTemplate).mockResolvedValue({ deleted: true, template: 'test' });

      await act(async () => {
        await useTemplateStore.getState().deleteTemplate('test-template');
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplate).toBeNull();
    });

    it('should keep selected template if different template was deleted', async () => {
      const otherTemplate = {
        ...sampleTemplate,
        name: 'other-template',
      };
      useTemplateStore.setState({
        templates: [sampleTemplate, otherTemplate],
        selectedTemplate: sampleTemplate,
      });

      vi.mocked(api.deleteTemplate).mockResolvedValue({
        deleted: true,
        template: 'other-template',
      });

      await act(async () => {
        await useTemplateStore.getState().deleteTemplate('other-template');
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplate).toEqual(sampleTemplate); // Unchanged
    });
  });

  describe('validateTemplate', () => {
    it('should validate template successfully', async () => {
      vi.mocked(api.validateTemplate).mockResolvedValue(validationResponse);

      let result;
      await act(async () => {
        result = await useTemplateStore.getState().validateTemplate('test-template');
      });

      expect(api.validateTemplate).toHaveBeenCalledWith('test-template');
      expect(result).toEqual(validationResponse);

      const state = useTemplateStore.getState();
      expect(state.lastValidation).toEqual(validationResponse);
    });

    it('should handle validation error', async () => {
      vi.mocked(api.validateTemplate).mockRejectedValue(
        new Error('Invalid YAML syntax')
      );

      await expect(
        useTemplateStore.getState().validateTemplate('invalid-template')
      ).rejects.toThrow();

      const state = useTemplateStore.getState();
      expect(state.lastValidation).toBeNull();
      expect(state.lastError).toContain('Failed to validate template');
    });

    it('should set loading state during validation', async () => {
      let loadingDuringValidation = false;

      vi.mocked(api.validateTemplate).mockImplementation(async () => {
        loadingDuringValidation = useTemplateStore.getState().isLoading;
        return validationResponse;
      });

      await act(async () => {
        await useTemplateStore.getState().validateTemplate('test-template');
      });

      expect(loadingDuringValidation).toBe(true);
    });
  });

  describe('State Setters', () => {
    it('should set templates via setTemplates', () => {
      act(() => {
        useTemplateStore.getState().setTemplates([sampleTemplate]);
      });

      const state = useTemplateStore.getState();
      expect(state.templates).toEqual([sampleTemplate]);
    });

    it('should set selected template via setSelectedTemplate', () => {
      act(() => {
        useTemplateStore.getState().setSelectedTemplate(sampleTemplate);
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplate).toEqual(sampleTemplate);
    });

    it('should set raw YAML via setSelectedTemplateRaw', () => {
      const yaml = 'name: test\nquality_threshold: 0.9';
      act(() => {
        useTemplateStore.getState().setSelectedTemplateRaw(yaml);
      });

      const state = useTemplateStore.getState();
      expect(state.selectedTemplateRaw).toBe(yaml);
    });

    it('should set validation result via setLastValidation', () => {
      act(() => {
        useTemplateStore.getState().setLastValidation(validationResponse);
      });

      const state = useTemplateStore.getState();
      expect(state.lastValidation).toEqual(validationResponse);
    });

    it('should set loading state via setIsLoading', () => {
      act(() => {
        useTemplateStore.getState().setIsLoading(true);
      });

      const state = useTemplateStore.getState();
      expect(state.isLoading).toBe(true);
    });

    it('should set saving state via setIsSaving', () => {
      act(() => {
        useTemplateStore.getState().setIsSaving(true);
      });

      const state = useTemplateStore.getState();
      expect(state.isSaving).toBe(true);
    });

    it('should set error via setLastError', () => {
      act(() => {
        useTemplateStore.getState().setLastError('Test error message');
      });

      const state = useTemplateStore.getState();
      expect(state.lastError).toBe('Test error message');
    });
  });

  describe('Error Handling Edge Cases', () => {
    it('should handle string error (not Error object)', async () => {
      vi.mocked(api.listTemplates).mockRejectedValue('String error');

      await act(async () => {
        await useTemplateStore.getState().fetchTemplates();
      });

      const state = useTemplateStore.getState();
      expect(state.lastError).toContain('Failed to fetch templates');
      expect(state.lastError).toContain('String error');
    });

    it('should handle null response gracefully', async () => {
      vi.mocked(api.listTemplates).mockResolvedValue(null as unknown as typeof templateListResponse);

      await act(async () => {
        await useTemplateStore.getState().fetchTemplates();
      });

      // Should not crash
      const state = useTemplateStore.getState();
      expect(state.templates).toBeNull();
    });
  });
});

describe('Template Store Selectors', () => {
  beforeEach(() => {
    useTemplateStore.setState({
      templates: [sampleTemplate],
    });
  });

  describe('selectTemplateByName', () => {
    it('should return template when found', () => {
      const selector = selectTemplateByName('test-template');
      const result = selector(useTemplateStore.getState());
      expect(result).toEqual(sampleTemplate);
    });

    it('should return undefined when not found', () => {
      const selector = selectTemplateByName('nonexistent');
      const result = selector(useTemplateStore.getState());
      expect(result).toBeUndefined();
    });
  });

  describe('selectTemplateCount', () => {
    it('should return correct count', () => {
      const count = selectTemplateCount(useTemplateStore.getState());
      expect(count).toBe(1);
    });

    it('should return 0 for empty list', () => {
      useTemplateStore.setState({ templates: [] });
      const count = selectTemplateCount(useTemplateStore.getState());
      expect(count).toBe(0);
    });
  });

  describe('selectHasTemplates', () => {
    it('should return true when templates exist', () => {
      const hasTemplates = selectHasTemplates(useTemplateStore.getState());
      expect(hasTemplates).toBe(true);
    });

    it('should return false for empty list', () => {
      useTemplateStore.setState({ templates: [] });
      const hasTemplates = selectHasTemplates(useTemplateStore.getState());
      expect(hasTemplates).toBe(false);
    });
  });
});
