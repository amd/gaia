// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * TemplateCard component - Displays a pipeline template as a card.
 */

import { FileText, Clock, Settings, ChevronRight } from 'lucide-react';
import type { PipelineTemplate } from '../../types';
import './TemplateCard.css';

interface TemplateCardProps {
  template: PipelineTemplate;
  onView: (name: string) => void;
  onEdit: (name: string) => void;
  onValidate: (name: string) => void;
}

export function TemplateCard({ template, onView, onEdit, onValidate }: TemplateCardProps) {
  const categoryCount = Object.keys(template.agent_categories || {}).length;
  const ruleCount = template.routing_rules?.length || 0;

  return (
    <div className="template-card" onClick={() => onView(template.name)} role="button" tabIndex={0}>
      <div className="template-card-header">
        <div className="template-card-icon">
          <FileText size={24} />
        </div>
        <div className="template-card-title">
          <h3 className="template-card-name">{template.name}</h3>
          {template.description && (
            <p className="template-card-description">{template.description}</p>
          )}
        </div>
      </div>

      <div className="template-card-body">
        <div className="template-card-stats">
          <div className="template-card-stat" title="Quality threshold">
            <Settings size={14} />
            <span>{(template.quality_threshold * 100).toFixed(0)}%</span>
          </div>
          <div className="template-card-stat" title="Max iterations">
            <Clock size={14} />
            <span>{template.max_iterations} iters</span>
          </div>
          <div className="template-card-stat" title="Agent categories">
            <FileText size={14} />
            <span>{categoryCount} categories</span>
          </div>
          <div className="template-card-stat" title="Routing rules">
            <ChevronRight size={14} />
            <span>{ruleCount} rules</span>
          </div>
        </div>

        {template.quality_weights && Object.keys(template.quality_weights).length > 0 && (
          <div className="template-card-weights">
            <span className="template-card-weights-label">Quality weights:</span>
            <div className="template-card-weights-list">
              {Object.entries(template.quality_weights).slice(0, 3).map(([key, value]) => (
                <span key={key} className="template-card-weight">
                  {key}: {(value * 100).toFixed(0)}%
                </span>
              ))}
              {Object.keys(template.quality_weights).length > 3 && (
                <span className="template-card-weight-more">
                  +{Object.keys(template.quality_weights).length - 3} more
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="template-card-footer">
        <button
          className="template-card-btn template-card-btn-view"
          onClick={(e) => {
            e.stopPropagation();
            onView(template.name);
          }}
          aria-label={`View template ${template.name}`}
        >
          View
        </button>
        <button
          className="template-card-btn template-card-btn-edit"
          onClick={(e) => {
            e.stopPropagation();
            onEdit(template.name);
          }}
          aria-label={`Edit template ${template.name}`}
        >
          Edit
        </button>
        <button
          className="template-card-btn template-card-btn-validate"
          onClick={(e) => {
            e.stopPropagation();
            onValidate(template.name);
          }}
          aria-label={`Validate template ${template.name}`}
        >
          Validate
        </button>
      </div>
    </div>
  );
}
