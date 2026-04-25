// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * TemplateMarketplace - Browse, manage, and use pipeline templates.
 *
 * Displays templates in grid or list view with actions:
 * - View Versions (opens version history)
 * - Export (downloads template with version history)
 * - Use Template (selects template for pipeline runner)
 * - Import (opens file picker for JSON exports)
 */

import { memo, useState, useCallback, useRef, type FC } from 'react';
import {
  Grid3X3,
  List,
  Eye,
  Download,
  Upload,
  Play,
  Clock,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  FileText,
  Tag,
  Users,
  Search,
} from 'lucide-react';
import { useTemplateStore } from '../../stores/templateStore';
import type { PipelineTemplate } from '../../types';
import './TemplateMarketplace.css';

interface TemplateMarketplaceProps {
  onUseTemplate?: (name: string) => void;
  onViewVersions?: (name: string) => void;
}

function TemplateMarketplaceInner({ onUseTemplate, onViewVersions }: TemplateMarketplaceProps) {
  const {
    templates,
    isLoading,
    lastError,
    isExporting,
    isImporting,
    viewMode,
    setViewMode,
    fetchTemplates,
    exportTemplate,
    importTemplate,
    setLastError,
  } = useTemplateStore((s) => ({
    templates: s.templates,
    isLoading: s.isLoading,
    lastError: s.lastError,
    isExporting: s.isExporting,
    isImporting: s.isImporting,
    viewMode: s.viewMode,
    setViewMode: s.setViewMode,
    fetchTemplates: s.fetchTemplates,
    exportTemplate: s.exportTemplate,
    importTemplate: s.importTemplate,
    setLastError: s.setLastError,
  }));

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Extract unique categories from all templates
  const allCategories = templates.reduce<string[]>((acc, t) => {
    Object.keys(t.agent_categories).forEach((cat) => {
      if (!acc.includes(cat)) acc.push(cat);
    });
    return acc;
  }, []);

  // Filter templates by search and category
  const filteredTemplates = templates.filter((t) => {
    const matchesSearch = searchQuery === '' ||
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = selectedCategory === 'all' ||
      Object.keys(t.agent_categories).includes(selectedCategory);
    return matchesSearch && matchesCategory;
  });

  const handleExport = useCallback(async (template: PipelineTemplate) => {
    const data = await exportTemplate(template.name);
    if (data) {
      // Create a blob and trigger download
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${template.name}_export.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  }, [exportTemplate]);

  const handleImportClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const data = JSON.parse(text);

      const importRequest = {
        template: data.template,
        name_conflict_strategy: 'rename' as const,
        versions: data.versions || [],
      };

      const success = await importTemplate(importRequest);
      if (success) {
        fetchTemplates();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setLastError(`Failed to parse import file: ${message}`);
    }

    // Reset file input so the same file can be re-imported
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [importTemplate, fetchTemplates, setLastError]);

  const totalAgents = (template: PipelineTemplate) =>
    Object.values(template.agent_categories).flat().length;

  return (
    <div className="tm-marketplace">
      {/* Header */}
      <div className="tm-header">
        <div className="tm-header-left">
          <FileText size={18} />
          <h2>Template Marketplace</h2>
          <span className="tm-template-count">{templates.length} templates</span>
        </div>

        <div className="tm-header-actions">
          {/* Search */}
          <div className="tm-search">
            <Search size={14} />
            <input
              type="text"
              placeholder="Search templates..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="tm-search-input"
            />
          </div>

          {/* Category filter */}
          <select
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value)}
            className="tm-category-select"
          >
            <option value="all">All Categories</option>
            {allCategories.map((cat) => (
              <option key={cat} value={cat}>{cat}</option>
            ))}
          </select>

          {/* Import button */}
          <button
            className="tm-btn tm-btn-secondary"
            onClick={handleImportClick}
            disabled={isImporting}
            title="Import template from JSON file"
          >
            {isImporting ? <Loader2 size={14} className="spin" /> : <Upload size={14} />}
            Import
          </button>

          {/* Refresh */}
          <button
            className="tm-btn tm-btn-secondary"
            onClick={fetchTemplates}
            disabled={isLoading}
            title="Refresh templates"
          >
            <Clock size={14} />
          </button>

          {/* View toggle */}
          <div className="tm-view-toggle">
            <button
              className={`tm-view-btn ${viewMode === 'grid' ? 'active' : ''}`}
              onClick={() => setViewMode('grid')}
              title="Grid view"
            >
              <Grid3X3 size={14} />
            </button>
            <button
              className={`tm-view-btn ${viewMode === 'list' ? 'active' : ''}`}
              onClick={() => setViewMode('list')}
              title="List view"
            >
              <List size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        onChange={handleFileChange}
        style={{ display: 'none' }}
      />

      {/* Error display */}
      {lastError && (
        <div className="tm-error-bar">
          <AlertTriangle size={14} />
          <span>{lastError}</span>
          <button onClick={() => setLastError(null)} className="tm-error-dismiss">Dismiss</button>
        </div>
      )}

      {/* Loading state */}
      {isLoading && templates.length === 0 && (
        <div className="tm-loading">
          <Loader2 size={24} className="spin" />
          <span>Loading templates...</span>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && templates.length === 0 && (
        <div className="tm-empty">
          <FileText size={40} strokeWidth={1} />
          <h3>No templates found</h3>
          <p>Create templates from the Canvas tab or import one.</p>
        </div>
      )}

      {/* No results after filtering */}
      {!isLoading && templates.length > 0 && filteredTemplates.length === 0 && (
        <div className="tm-empty">
          <Search size={32} strokeWidth={1} />
          <h3>No matching templates</h3>
          <p>Try adjusting your search or category filter.</p>
        </div>
      )}

      {/* Templates Grid or List */}
      {filteredTemplates.length > 0 && (
        <div className={`tm-templates ${viewMode === 'grid' ? 'tm-templates-grid' : 'tm-templates-list'}`}>
          {filteredTemplates.map((template) => (
            viewMode === 'grid' ? (
              <TemplateCard
                key={template.name}
                template={template}
                totalAgents={totalAgents(template)}
                onUse={() => onUseTemplate?.(template.name)}
                onViewVersions={() => onViewVersions?.(template.name)}
                onExport={() => handleExport(template)}
              />
            ) : (
              <TemplateRow
                key={template.name}
                template={template}
                totalAgents={totalAgents(template)}
                onUse={() => onUseTemplate?.(template.name)}
                onViewVersions={() => onViewVersions?.(template.name)}
                onExport={() => handleExport(template)}
              />
            )
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Template Card (Grid View) ─────────────────────────────────────────── */

interface TemplateCardProps {
  template: PipelineTemplate;
  totalAgents: number;
  onUse: () => void;
  onViewVersions: () => void;
  onExport: () => void;
}

function TemplateCard({ template, totalAgents, onUse, onViewVersions, onExport }: TemplateCardProps) {
  const categories = Object.keys(template.agent_categories);

  return (
    <div className="tm-card">
      <div className="tm-card-header">
        <h3 className="tm-card-title">{template.name}</h3>
        <span className="tm-card-agents">
          <Users size={12} />
          {totalAgents} agent{totalAgents !== 1 ? 's' : ''}
        </span>
      </div>

      <p className="tm-card-description">{template.description || 'No description'}</p>

      {/* Metadata row */}
      <div className="tm-card-meta">
        <span className="tm-card-quality" title="Quality threshold">
          <span className={`tm-quality-indicator ${template.quality_threshold >= 0.9 ? 'pass' : 'warn'}`}>
            {(template.quality_threshold * 100).toFixed(0)}%
          </span>
        </span>
        <span className="tm-card-iterations">
          Max {template.max_iterations} iter{template.max_iterations !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Category tags */}
      <div className="tm-card-tags">
        {categories.slice(0, 4).map((cat) => (
          <span key={cat} className="tm-tag">
            <Tag size={10} />
            {cat}
          </span>
        ))}
        {categories.length > 4 && (
          <span className="tm-tag-more">+{categories.length - 4}</span>
        )}
      </div>

      {/* Action buttons */}
      <div className="tm-card-actions">
        <button className="tm-btn tm-btn-small tm-btn-secondary" onClick={onViewVersions} title="View version history">
          <Eye size={12} />
          Versions
        </button>
        <button className="tm-btn tm-btn-small tm-btn-secondary" onClick={onExport} title="Export template">
          <Download size={12} />
          Export
        </button>
        <button className="tm-btn tm-btn-small tm-btn-primary" onClick={onUse} title="Use this template">
          <Play size={12} />
          Use
        </button>
      </div>
    </div>
  );
}

/* ── Template Row (List View) ──────────────────────────────────────────── */

function TemplateRow({ template, totalAgents, onUse, onViewVersions, onExport }: TemplateCardProps) {
  const categories = Object.keys(template.agent_categories);

  return (
    <div className="tm-row">
      <div className="tm-row-main">
        <span className="tm-row-name">{template.name}</span>
        <span className="tm-row-description">{template.description || 'No description'}</span>
      </div>

      <div className="tm-row-meta">
        <span className="tm-row-agents">
          <Users size={12} />
          {totalAgents}
        </span>
        <span className={`tm-row-quality ${template.quality_threshold >= 0.9 ? 'pass' : 'warn'}`}>
          {(template.quality_threshold * 100).toFixed(0)}%
        </span>
        {categories.slice(0, 3).map((cat) => (
          <span key={cat} className="tm-tag tm-tag-small">
            {cat}
          </span>
        ))}
      </div>

      <div className="tm-row-actions">
        <button className="tm-btn-icon" onClick={onViewVersions} title="View versions">
          <Eye size={14} />
        </button>
        <button className="tm-btn-icon" onClick={onExport} title="Export">
          <Download size={14} />
        </button>
        <button className="tm-btn-icon tm-btn-icon-primary" onClick={onUse} title="Use template">
          <Play size={14} />
        </button>
      </div>
    </div>
  );
}

export const TemplateMarketplace: FC<TemplateMarketplaceProps> = memo(TemplateMarketplaceInner);
