// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * TemplateViewerDialog - Read-only view of a pipeline template.
 */

import { useEffect, useState } from 'react';
import { X, FileText, Copy, Check } from 'lucide-react';
import type { PipelineTemplate } from '../../types';
import { useTemplateStore } from '../../stores/templateStore';
import './TemplateViewerDialog.css';

interface TemplateViewerDialogProps {
  templateName: string;
  onClose: () => void;
}

export function TemplateViewerDialog({ templateName, onClose }: TemplateViewerDialogProps) {
  const { selectedTemplate, selectedTemplateRaw, fetchTemplate, fetchTemplateRaw, isLoading, lastError } = useTemplateStore();
  const [activeTab, setActiveTab] = useState<'preview' | 'yaml'>('preview');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetchTemplate(templateName);
    fetchTemplateRaw(templateName);
  }, [templateName]);

  const handleCopyYaml = async () => {
    if (selectedTemplateRaw) {
      await navigator.clipboard.writeText(selectedTemplateRaw);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const template = selectedTemplate;
  if (!template && !isLoading) return null;

  return (
    <div className="template-viewer-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="template-viewer-title">
      <div className="template-viewer-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="template-viewer-header">
          <div className="template-viewer-title">
            <FileText size={20} />
            <h2 id="template-viewer-title">{template?.name || templateName}</h2>
          </div>
          <button
            className="template-viewer-close"
            onClick={onClose}
            aria-label="Close template viewer"
          >
            <X size={20} />
          </button>
        </div>

        {lastError && (
          <div className="template-viewer-error" role="alert">
            {lastError}
          </div>
        )}

        {isLoading && !template && (
          <div className="template-viewer-loading">
            <div className="loading-spinner" />
            <span>Loading template...</span>
          </div>
        )}

        {template && (
          <>
            <div className="template-viewer-tabs">
              <button
                className={`template-viewer-tab ${activeTab === 'preview' ? 'active' : ''}`}
                onClick={() => setActiveTab('preview')}
              >
                Preview
              </button>
              <button
                className={`template-viewer-tab ${activeTab === 'yaml' ? 'active' : ''}`}
                onClick={() => setActiveTab('yaml')}
              >
                YAML
              </button>
            </div>

            <div className="template-viewer-content">
              {activeTab === 'preview' && (
                <div className="template-viewer-preview">
                  <div className="template-preview-section">
                    <h3>Description</h3>
                    <p>{template.description || 'No description provided.'}</p>
                  </div>

                  <div className="template-preview-section">
                    <h3>Configuration</h3>
                    <div className="template-preview-grid">
                      <div className="template-preview-item">
                        <span className="template-preview-label">Quality Threshold</span>
                        <span className="template-preview-value">{(template.quality_threshold * 100).toFixed(0)}%</span>
                      </div>
                      <div className="template-preview-item">
                        <span className="template-preview-label">Max Iterations</span>
                        <span className="template-preview-value">{template.max_iterations}</span>
                      </div>
                    </div>
                  </div>

                  {template.agent_categories && Object.keys(template.agent_categories).length > 0 && (
                    <div className="template-preview-section">
                      <h3>Agent Categories</h3>
                      <div className="template-categories-list">
                        {Object.entries(template.agent_categories).map(([category, agents]) => (
                          <div key={category} className="template-category-item">
                            <span className="template-category-name">{category}</span>
                            <span className="template-category-agents">
                              {agents.length} agent{agents.length !== 1 ? 's' : ''}: {agents.join(', ')}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {template.routing_rules && template.routing_rules.length > 0 && (
                    <div className="template-preview-section">
                      <h3>Routing Rules</h3>
                      <div className="template-rules-list">
                        {template.routing_rules.map((rule, index) => (
                          <div key={index} className="template-rule-item">
                            <div className="template-rule-header">
                              <span className="template-rule-priority">#{rule.priority}</span>
                              <span className="template-rule-condition">{rule.condition}</span>
                            </div>
                            <div className="template-rule-details">
                              <span>Route to: <strong>{rule.route_to}</strong></span>
                              {rule.loop_back && <span className="template-rule-loopback">Loop back</span>}
                              {rule.guidance && <span className="template-rule-guidance">{rule.guidance}</span>}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {template.quality_weights && Object.keys(template.quality_weights).length > 0 && (
                    <div className="template-preview-section">
                      <h3>Quality Weights</h3>
                      <div className="template-weights-list">
                        {Object.entries(template.quality_weights).map(([key, value]) => (
                          <div key={key} className="template-weight-item">
                            <span>{key}</span>
                            <span className="template-weight-value">{(value * 100).toFixed(0)}%</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'yaml' && (
                <div className="template-viewer-yaml">
                  <button
                    className="template-viewer-copy"
                    onClick={handleCopyYaml}
                    disabled={!selectedTemplateRaw}
                  >
                    {copied ? <Check size={16} /> : <Copy size={16} />}
                    {copied ? 'Copied!' : 'Copy YAML'}
                  </button>
                  <pre className="template-yaml-content">
                    {selectedTemplateRaw || 'Loading YAML...'}
                  </pre>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
