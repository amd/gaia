// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * AgentRegistry - Browse all available GAIA agents with their capabilities,
 * categories, model assignments, and pipeline template references.
 */

import { useState, useEffect, useMemo } from 'react';
import { Search, Users, Tag, Cpu, Terminal, FileText, Zap, ChevronDown, ChevronRight } from 'lucide-react';
import * as api from '../../services/api';
import type { AgentRegistryEntry } from '../../types';
import './AgentRegistry.css';

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  planning: <Tag size={16} />,
  development: <Terminal size={16} />,
  review: <Zap size={16} />,
  quality: <Zap size={16} />,
  management: <Users size={16} />,
  analysis: <Search size={16} />,
  orchestration: <Cpu size={16} />,
  pipeline_stage: <Cpu size={16} />,
};

const CATEGORY_LABELS: Record<string, string> = {
  planning: 'Planning',
  development: 'Development',
  review: 'Review',
  quality: 'Quality',
  management: 'Management',
  analysis: 'Analysis',
  orchestration: 'Orchestration',
  pipeline_stage: 'Pipeline Stages',
};

const SOURCE_LABELS: Record<string, string> = {
  yaml: 'Template Agent',
  pipeline_stage: 'Pipeline Stage',
};

export function AgentRegistry() {
  const [agents, setAgents] = useState<AgentRegistryEntry[]>([]);
  const [categories, setCategories] = useState<Record<string, string[]>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  useEffect(() => {
    api.listAgents()
      .then((data: { agents: AgentRegistryEntry[]; categories: Record<string, string[]>; total: number }) => {
        setAgents(data.agents || []);
        setCategories(data.categories || {});
        setTotal(data.total || 0);
      })
      .catch((_err: unknown) => {
        console.error('Failed to load agent registry:', _err);
      })
      .finally(() => setLoading(false));
  }, []);

  const filteredAgents = useMemo(() => {
    let result = agents;
    if (activeCategory !== 'all') {
      result = result.filter((a) => a.category === activeCategory);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          a.id.toLowerCase().includes(q) ||
          a.description.toLowerCase().includes(q) ||
          a.capabilities.some((c: string) => c.toLowerCase().includes(q)) ||
          a.keywords.some((k: string) => k.toLowerCase().includes(q))
      );
    }
    return result;
  }, [agents, activeCategory, search]);

  const categoryList = useMemo(() => {
    return Object.keys(categories).sort();
  }, [categories]);

  const toggleAgent = (id: string) => {
    setExpandedAgent((prev) => (prev === id ? null : id));
  };

  if (loading) {
    return (
      <div className="agent-registry">
        <div className="ar-header">
          <h1>Agent Registry</h1>
          <p>Loading agents...</p>
        </div>
        <div className="ar-loading">
          <div className="ar-loading-spinner" />
          <span>Discovering agents from config/agents/...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="agent-registry">
      {/* Header */}
      <div className="ar-header">
        <h1>Agent Registry</h1>
        <p>
          {total} agent{total !== 1 ? 's' : ''} across {categoryList.length} categories
        </p>
      </div>

      {/* Search and Filter Bar */}
      <div className="ar-toolbar">
        <div className="ar-search">
          <Search size={16} className="ar-search-icon" />
          <input
            type="text"
            placeholder="Search agents by name, capability, or keyword..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="ar-category-filter">
          <button
            className={`ar-cat-btn ${activeCategory === 'all' ? 'active' : ''}`}
            onClick={() => setActiveCategory('all')}
          >
            All ({total})
          </button>
          {categoryList.map((cat) => (
            <button
              key={cat}
              className={`ar-cat-btn ${activeCategory === cat ? 'active' : ''}`}
              onClick={() => setActiveCategory(cat)}
            >
              {CATEGORY_ICONS[cat] || <Users size={14} />}
              {CATEGORY_LABELS[cat] || cat} ({categories[cat]?.length || 0})
            </button>
          ))}
        </div>
      </div>

      {/* Agent List */}
      {filteredAgents.length === 0 && (
        <div className="ar-empty">
          <Users size={48} strokeWidth={1} />
          <h3>No agents found</h3>
          <p>
            {search
              ? 'Try a different search term.'
              : 'No agents are registered in this category.'}
          </p>
        </div>
      )}

      <div className="ar-agent-list">
        {filteredAgents.map((agent) => {
          const isExpanded = expandedAgent === agent.id;
          const hasTemplates = agent.templates_using.length > 0;

          return (
            <div
              key={agent.id}
              className={`ar-agent-card ${agent.enabled ? '' : 'ar-agent-disabled'}`}
            >
              {/* Agent Header (always visible) */}
              <div
                className="ar-agent-header"
                onClick={() => toggleAgent(agent.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleAgent(agent.id);
                  }
                }}
              >
                <div className="ar-agent-icon">
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </div>
                <div className="ar-agent-title">
                  <span className="ar-agent-name">{agent.name}</span>
                  <span className="ar-agent-id">{agent.id}</span>
                </div>
                <div className="ar-agent-badges">
                  <span className={`ar-badge ar-badge-source ar-badge-${agent.source}`}>
                    {SOURCE_LABELS[agent.source] || agent.source}
                  </span>
                  <span className="ar-badge ar-badge-category">
                    {CATEGORY_LABELS[agent.category] || agent.category}
                  </span>
                  {agent.model_id && (
                    <span className="ar-badge ar-badge-model">
                      {agent.model_id}
                    </span>
                  )}
                  {!agent.enabled && (
                    <span className="ar-badge ar-badge-disabled">Disabled</span>
                  )}
                </div>
              </div>

              {/* Expanded Details */}
              {isExpanded && (
                <div className="ar-agent-details">
                  <div className="ar-detail-section">
                    <h4>Description</h4>
                    <p className="ar-agent-description">{agent.description || 'No description available.'}</p>
                  </div>

                  {agent.capabilities.length > 0 && (
                    <div className="ar-detail-section">
                      <h4>Capabilities</h4>
                      <div className="ar-capability-list">
                        {agent.capabilities.map((cap: string) => (
                          <span key={cap} className="ar-capability-chip">
                            {cap}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {agent.keywords.length > 0 && (
                    <div className="ar-detail-section">
                      <h4>Trigger Keywords</h4>
                      <div className="ar-keyword-list">
                        {agent.keywords.map((kw: string) => (
                          <span key={kw} className="ar-keyword-chip">
                            {kw}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {agent.phases.length > 0 && (
                    <div className="ar-detail-section">
                      <h4>Phases</h4>
                      <div className="ar-phase-list">
                        {agent.phases.map((phase: string) => (
                          <span key={phase} className="ar-phase-chip">
                            {phase}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {agent.tools.length > 0 && (
                    <div className="ar-detail-section">
                      <h4>Tools</h4>
                      <div className="ar-tool-list">
                        {agent.tools.map((tool: string) => (
                          <span key={tool} className="ar-tool-chip">
                            <Terminal size={12} />
                            {tool}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {hasTemplates && (
                    <div className="ar-detail-section">
                      <h4>Used by Pipeline Templates</h4>
                      <div className="ar-template-list">
                        {agent.templates_using.map((tmpl: string) => (
                          <span key={tmpl} className="ar-template-chip">
                            <FileText size={12} />
                            {tmpl}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {agent.entrypoint && (
                    <div className="ar-detail-section">
                      <h4>Entrypoint</h4>
                      <code className="ar-entrypoint">{agent.entrypoint}</code>
                    </div>
                  )}

                  <div className="ar-detail-section ar-detail-meta">
                    <span>Version: {agent.version}</span>
                    <span>Complexity: {agent.complexity_range}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
