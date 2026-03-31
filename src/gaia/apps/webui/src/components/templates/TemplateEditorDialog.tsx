// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * TemplateEditorDialog - YAML editor for pipeline templates.
 */

import { useEffect, useState, useCallback } from 'react';
import { X, Save, RotateCcw, AlertCircle, CheckCircle } from 'lucide-react';
import Editor from '@monaco-editor/react';
import type { PipelineTemplate, TemplateValidateResponse } from '../../types';
import { useTemplateStore } from '../../stores/templateStore';
import './TemplateEditorDialog.css';

interface TemplateEditorDialogProps {
  templateName?: string; // If provided, edit existing; otherwise create new
  onClose: () => void;
  onSuccess?: (template: PipelineTemplate) => void;
}

interface EditorFormState {
  name: string;
  description: string;
  quality_threshold: number;
  max_iterations: number;
  agent_categories: Record<string, string[]>;
  routing_rules: Array<{
    condition: string;
    route_to: string;
    priority: number;
    loop_back: boolean;
    guidance?: string;
  }>;
  quality_weights: Record<string, number>;
}

const DEFAULT_TEMPLATE: EditorFormState = {
  name: '',
  description: '',
  quality_threshold: 0.90,
  max_iterations: 10,
  agent_categories: {},
  routing_rules: [],
  quality_weights: {},
};

export function TemplateEditorDialog({ templateName, onClose, onSuccess }: TemplateEditorDialogProps) {
  const {
    selectedTemplate,
    selectedTemplateRaw,
    isLoading,
    isSaving,
    lastError,
    lastValidation,
    fetchTemplate,
    fetchTemplateRaw,
    createTemplate,
    updateTemplate,
    validateTemplate,
    setLastError,
  } = useTemplateStore();

  const [formState, setFormState] = useState<EditorFormState>(DEFAULT_TEMPLATE);
  const [yamlValue, setYamlValue] = useState('');
  const [useYamlEditor, setUseYamlEditor] = useState(false);
  const [validationResult, setValidationResult] = useState<TemplateValidateResponse | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  // Load existing template on mount
  useEffect(() => {
    if (templateName) {
      fetchTemplate(templateName);
      fetchTemplateRaw(templateName);
    }
  }, [templateName]);

  // Populate form when template is loaded
  useEffect(() => {
    if (selectedTemplate && templateName) {
      setFormState({
        name: selectedTemplate.name,
        description: selectedTemplate.description || '',
        quality_threshold: selectedTemplate.quality_threshold,
        max_iterations: selectedTemplate.max_iterations,
        agent_categories: selectedTemplate.agent_categories || {},
        routing_rules: selectedTemplate.routing_rules || [],
        quality_weights: selectedTemplate.quality_weights || {},
      });
    }
  }, [selectedTemplate, templateName]);

  // Populate YAML editor when raw content is loaded
  useEffect(() => {
    if (selectedTemplateRaw) {
      setYamlValue(selectedTemplateRaw);
    } else if (!templateName) {
      // New template - start with a template skeleton
      setYamlValue(`# Pipeline Template Configuration
name: ${formState.name || 'my-template'}
description: ${formState.description || 'My pipeline template'}

# Quality threshold (0.0 - 1.0)
quality_threshold: ${formState.quality_threshold}

# Maximum iterations before forcing termination
max_iterations: ${formState.max_iterations}

# Agent categories and their agents
agent_categories:
  ${Object.keys(formState.agent_categories).length > 0 ? Object.entries(formState.agent_categories).map(([cat, agents]) => `
  ${cat}:
    - ${agents.join('\n    - ')}`).join('\n  ') : '# Add your agent categories here'}

# Routing rules for conditional flow control
routing_rules:
  ${formState.routing_rules.length > 0 ? formState.routing_rules.map((rule) => `- condition: "${rule.condition}"
    route_to: "${rule.route_to}"
    priority: ${rule.priority}
    loop_back: ${rule.loop_back}${rule.guidance ? `
    guidance: "${rule.guidance}"` : ''}`).join('\n  ') : '# Add routing rules here'}

# Quality scoring weights (must sum to 1.0)
quality_weights:
  ${Object.keys(formState.quality_weights).length > 0 ? Object.entries(formState.quality_weights).map(([k, v]) => `${k}: ${v}`).join('\n  ') : '# correctness: 0.4\n  # completeness: 0.3\n  # efficiency: 0.3'}
`);
    }
  }, [selectedTemplateRaw, templateName]);

  // Parse YAML to form state (simplified parser for demo)
  const parseYamlToForm = useCallback((yaml: string): Partial<EditorFormState> => {
    // Simple YAML parsing - in production, use a proper YAML library like js-yaml
    const result: Partial<EditorFormState> = {};
    const lines = yaml.split('\n');

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('#') || !trimmed.includes(':')) continue;

      const [key, ...valueParts] = trimmed.split(':');
      const value = valueParts.join(':').trim();

      switch (key) {
        case 'name':
          result.name = value.replace(/['"]/g, '');
          break;
        case 'description':
          result.description = value.replace(/['"]/g, '');
          break;
        case 'quality_threshold':
          result.quality_threshold = parseFloat(value) || 0.9;
          break;
        case 'max_iterations':
          result.max_iterations = parseInt(value, 10) || 10;
          break;
      }
    }

    return result;
  }, []);

  const handleFormChange = (field: keyof EditorFormState, value: unknown) => {
    setFormState((prev) => ({ ...prev, [field]: value }));
    setIsDirty(true);
  };

  const handleYamlChange = (value?: string) => {
    if (value !== undefined) {
      setYamlValue(value);
      setIsDirty(true);
      // Parse YAML to update form state
      const parsed = parseYamlToForm(value);
      setFormState((prev) => ({ ...prev, ...parsed }));
    }
  };

  const handleValidate = async () => {
    if (templateName) {
      const result = await validateTemplate(templateName);
      setValidationResult(result);
    }
  };

  const handleSave = async () => {
    try {
      let template: PipelineTemplate;
      if (templateName && selectedTemplate) {
        // Update existing
        template = await updateTemplate(templateName, {
          description: formState.description,
          quality_threshold: formState.quality_threshold,
          max_iterations: formState.max_iterations,
          agent_categories: formState.agent_categories,
          routing_rules: formState.routing_rules,
          quality_weights: formState.quality_weights,
        });
      } else {
        // Create new
        template = await createTemplate({
          name: formState.name,
          description: formState.description,
          quality_threshold: formState.quality_threshold,
          max_iterations: formState.max_iterations,
          agent_categories: formState.agent_categories,
          routing_rules: formState.routing_rules,
          quality_weights: formState.quality_weights,
        });
      }
      onSuccess?.(template);
      onClose();
    } catch (err) {
      // Error already set in store
    }
  };

  const handleReset = () => {
    if (selectedTemplate) {
      setFormState({
        name: selectedTemplate.name,
        description: selectedTemplate.description || '',
        quality_threshold: selectedTemplate.quality_threshold,
        max_iterations: selectedTemplate.max_iterations,
        agent_categories: selectedTemplate.agent_categories || {},
        routing_rules: selectedTemplate.routing_rules || [],
        quality_weights: selectedTemplate.quality_weights || {},
      });
      setIsDirty(false);
      setLastError(null);
    } else {
      setFormState(DEFAULT_TEMPLATE);
      setIsDirty(false);
      setLastError(null);
    }
  };

  return (
    <div className="template-editor-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="template-editor-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="template-editor-header">
          <div className="template-editor-title">
            <h2>{templateName ? `Edit: ${templateName}` : 'Create Template'}</h2>
            {isDirty && <span className="template-editor-dirty">Unsaved changes</span>}
          </div>
          <div className="template-editor-actions">
            <button
              className={`template-editor-toggle ${useYamlEditor ? 'active' : ''}`}
              onClick={() => setUseYamlEditor(!useYamlEditor)}
              title="Toggle YAML editor"
            >
              {useYamlEditor ? 'Form' : 'YAML'}
            </button>
            <button
              className="template-editor-close"
              onClick={onClose}
              aria-label="Close editor"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {(lastError || validationResult?.errors.length) && (
          <div className="template-editor-error" role="alert">
            <AlertCircle size={16} />
            <span>{lastError || validationResult?.errors.join(', ')}</span>
          </div>
        )}

        {validationResult?.valid && (
          <div className="template-editor-success" role="status">
            <CheckCircle size={16} />
            <span>Template validation passed!</span>
          </div>
        )}

        {isLoading && !selectedTemplate && !templateName ? (
          <div className="template-editor-loading">
            <div className="loading-spinner" />
            <span>Loading...</span>
          </div>
        ) : useYamlEditor ? (
          <div className="template-editor-yaml">
            <Editor
              height="500px"
              language="yaml"
              theme="vs-dark"
              value={yamlValue}
              onChange={handleYamlChange}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 2,
              }}
            />
          </div>
        ) : (
          <div className="template-editor-form">
            <div className="template-form-section">
              <h3>Basic Information</h3>
              <div className="template-form-group">
                <label htmlFor="template-name">Name</label>
                <input
                  id="template-name"
                  type="text"
                  value={formState.name}
                  onChange={(e) => handleFormChange('name', e.target.value)}
                  disabled={!!templateName}
                  placeholder="my-pipeline-template"
                />
              </div>
              <div className="template-form-group">
                <label htmlFor="template-description">Description</label>
                <textarea
                  id="template-description"
                  value={formState.description}
                  onChange={(e) => handleFormChange('description', e.target.value)}
                  placeholder="Describe what this template does..."
                  rows={3}
                />
              </div>
            </div>

            <div className="template-form-section">
              <h3>Quality Settings</h3>
              <div className="template-form-row">
                <div className="template-form-group">
                  <label htmlFor="quality-threshold">Quality Threshold</label>
                  <input
                    id="quality-threshold"
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={formState.quality_threshold}
                    onChange={(e) => handleFormChange('quality_threshold', parseFloat(e.target.value) || 0.9)}
                  />
                  <span className="template-form-help">Minimum quality score (0.0 - 1.0)</span>
                </div>
                <div className="template-form-group">
                  <label htmlFor="max-iterations">Max Iterations</label>
                  <input
                    id="max-iterations"
                    type="number"
                    min={1}
                    max={100}
                    value={formState.max_iterations}
                    onChange={(e) => handleFormChange('max_iterations', parseInt(e.target.value, 10) || 10)}
                  />
                  <span className="template-form-help">Maximum recursive iterations</span>
                </div>
              </div>
            </div>

            <div className="template-form-section">
              <h3>Agent Categories</h3>
              <div className="template-form-help">
                Define categories of agents and which agents belong to each category.
              </div>
              <div className="template-form-categories">
                {Object.entries(formState.agent_categories).map(([category, agents]) => (
                  <div key={category} className="template-category-editor">
                    <input
                      type="text"
                      value={category}
                      disabled
                      className="template-category-name-input"
                    />
                    <input
                      type="text"
                      value={agents.join(', ')}
                      onChange={(e) => {
                        const newAgents = e.target.value.split(',').map((a) => a.trim()).filter(Boolean);
                        handleFormChange('agent_categories', { ...formState.agent_categories, [category]: newAgents });
                      }}
                      placeholder="agent-1, agent-2, ..."
                    />
                    <button
                      className="template-category-remove"
                      onClick={() => {
                        const { [category]: _, ...rest } = formState.agent_categories;
                        handleFormChange('agent_categories', rest);
                      }}
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  className="template-add-category-btn"
                  onClick={() => {
                    const categoryName = prompt('Enter category name:');
                    if (categoryName) {
                      handleFormChange('agent_categories', { ...formState.agent_categories, [categoryName]: [] });
                    }
                  }}
                >
                  + Add Category
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="template-editor-footer">
          <div className="template-editor-footer-left">
            {templateName && (
              <button
                className="template-editor-btn template-editor-btn-validate"
                onClick={handleValidate}
                disabled={isLoading}
              >
                Validate
              </button>
            )}
          </div>
          <div className="template-editor-footer-right">
            <button
              className="template-editor-btn template-editor-btn-reset"
              onClick={handleReset}
              disabled={!isDirty || isSaving}
            >
              <RotateCcw size={16} />
              Reset
            </button>
            <button
              className="template-editor-btn template-editor-btn-cancel"
              onClick={onClose}
              disabled={isSaving}
            >
              Cancel
            </button>
            <button
              className="template-editor-btn template-editor-btn-save"
              onClick={handleSave}
              disabled={!isDirty || isSaving || (templateName && isLoading)}
            >
              <Save size={16} />
              {isSaving ? 'Saving...' : (templateName ? 'Update' : 'Create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
