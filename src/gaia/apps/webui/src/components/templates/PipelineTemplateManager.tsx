// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PipelineTemplateManager - Main page for managing pipeline templates.
 */

import { useEffect, useState, useCallback } from 'react';
import { FileText, Plus, AlertCircle, RefreshCw, Search } from 'lucide-react';
import { useTemplateStore } from '../../stores/templateStore';
import { TemplateCard } from './TemplateCard';
import { TemplateViewerDialog } from './TemplateViewerDialog';
import { TemplateEditorDialog } from './TemplateEditorDialog';
import './PipelineTemplateManager.css';

export function PipelineTemplateManager() {
  const {
    templates,
    isLoading,
    lastError,
    fetchTemplates,
    setLastError,
  } = useTemplateStore();

  const [searchQuery, setSearchQuery] = useState('');
  const [viewingTemplate, setViewingTemplate] = useState<string | null>(null);
  const [editingTemplate, setEditingTemplate] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  useEffect(() => {
    fetchTemplates();
  }, []);

  const handleRefresh = useCallback(() => {
    fetchTemplates();
  }, []);

  const handleView = useCallback((name: string) => {
    setViewingTemplate(name);
  }, []);

  const handleEdit = useCallback((name: string) => {
    setEditingTemplate(name);
  }, []);

  const handleValidate = useCallback(async (name: string) => {
    // Validation is handled within the TemplateEditorDialog
    setEditingTemplate(name);
  }, []);

  const handleCreateSuccess = useCallback(() => {
    fetchTemplates();
    setShowCreateDialog(false);
  }, [fetchTemplates]);

  const handleEditSuccess = useCallback(() => {
    fetchTemplates();
    setEditingTemplate(null);
  }, [fetchTemplates]);

  const filteredTemplates = searchQuery
    ? templates.filter((t) =>
        t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.description?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : templates;

  return (
    <div className="pipeline-template-manager">
      <div className="ptm-header">
        <div className="ptm-title">
          <FileText size={24} className="ptm-title-icon" />
          <div>
            <h1>Pipeline Templates</h1>
            <p>Manage pipeline configurations and routing rules</p>
          </div>
        </div>
        <div className="ptm-actions">
          <div className="ptm-search">
            <Search size={16} />
            <input
              type="text"
              placeholder="Search templates..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search templates"
            />
          </div>
          <button
            className="ptm-btn ptm-btn-refresh"
            onClick={handleRefresh}
            disabled={isLoading}
            aria-label="Refresh templates"
          >
            <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
          </button>
          <button
            className="ptm-btn ptm-btn-primary"
            onClick={() => setShowCreateDialog(true)}
          >
            <Plus size={18} />
            Create Template
          </button>
        </div>
      </div>

      {lastError && (
        <div className="ptm-error-banner" role="alert">
          <AlertCircle size={18} />
          <span>{lastError}</span>
          <button
            className="ptm-error-dismiss"
            onClick={() => setLastError(null)}
            aria-label="Dismiss error"
          >
            Dismiss
          </button>
        </div>
      )}

      {isLoading && templates.length === 0 ? (
        <div className="ptm-loading">
          <div className="loading-spinner" />
          <span>Loading templates...</span>
        </div>
      ) : filteredTemplates.length === 0 ? (
        <div className="ptm-empty">
          <FileText size={48} strokeWidth={1} />
          <h3>No templates found</h3>
          <p>
            {searchQuery
              ? 'No templates match your search query.'
              : 'Create your first pipeline template to get started.'}
          </p>
          {!searchQuery && (
            <button
              className="ptm-btn ptm-btn-primary"
              onClick={() => setShowCreateDialog(true)}
            >
              <Plus size={18} />
              Create Template
            </button>
          )}
        </div>
      ) : (
        <div className="ptm-grid">
          {filteredTemplates.map((template) => (
            <TemplateCard
              key={template.name}
              template={template}
              onView={handleView}
              onEdit={handleEdit}
              onValidate={handleValidate}
            />
          ))}
        </div>
      )}

      {/* Viewer Dialog */}
      {viewingTemplate && (
        <TemplateViewerDialog
          templateName={viewingTemplate}
          onClose={() => setViewingTemplate(null)}
        />
      )}

      {/* Editor Dialog - Edit existing */}
      {editingTemplate && (
        <TemplateEditorDialog
          templateName={editingTemplate}
          onClose={() => setEditingTemplate(null)}
          onSuccess={handleEditSuccess}
        />
      )}

      {/* Editor Dialog - Create new */}
      {showCreateDialog && (
        <TemplateEditorDialog
          onClose={() => setShowCreateDialog(false)}
          onSuccess={handleCreateSuccess}
        />
      )}
    </div>
  );
}
