// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * AgentPalette - Sidebar with available agents for drag-and-drop onto the canvas.
 *
 * Agents are grouped by category. Each agent card is draggable into stage zones.
 */

import { memo, useState, useEffect } from 'react';
import { GripVertical, Cpu, Loader2, ChevronDown, ChevronRight, Search, Shield, GitBranch, Repeat } from 'lucide-react';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

const CATEGORY_COLORS: Record<string, string> = {
    analysis: '#3b82f6',
    orchestration: '#8b5cf6',
    quality: '#10b981',
    development: '#f59e0b',
    planning: '#ec4899',
    unknown: '#6b7280',
};

function AgentPaletteInner() {
    const { agents, agentCategories, paletteLoading, fetchAgents } = usePipelineCanvasStore((s) => ({
        agents: s.agents,
        agentCategories: s.agentCategories,
        paletteLoading: s.paletteLoading,
        fetchAgents: s.fetchAgents,
    }));

    const [search, setSearch] = useState('');
    const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});

    // Load agents on mount
    useEffect(() => {
        fetchAgents();
    }, [fetchAgents]);

    const toggleCategory = (cat: string) => {
        setExpandedCategories((prev) => ({ ...prev, [cat]: !prev[cat] }));
    };

    const handleDragStart = (e: React.DragEvent, agent: typeof agents[number]) => {
        const dragData = {
            agentId: agent.id,
            name: agent.name,
            category: agent.category,
            modelId: agent.model_id || undefined,
            capabilities: agent.capabilities,
            description: agent.description,
            keywords: agent.keywords,
            phases: agent.phases,
        };
        e.dataTransfer.setData('application/x-agent', JSON.stringify(dragData));
        e.dataTransfer.effectAllowed = 'copy';
    };

    const handleBlockDragStart = (e: React.DragEvent, blockType: 'supervisor' | 'gate' | 'loop', condition?: string) => {
        const dragData = {
            blockType,
            name: blockType === 'supervisor' ? 'Quality Supervisor' : blockType === 'gate' ? 'Decision Gate' : 'Loop Block',
            category: 'orchestration',
            condition: condition || 'quality_below_threshold',
        };
        e.dataTransfer.setData('application/x-agent', JSON.stringify(dragData));
        e.dataTransfer.effectAllowed = 'copy';
    };

    const filteredAgents = search
        ? agents.filter(
              (a) =>
                  a.id.toLowerCase().includes(search.toLowerCase()) ||
                  a.name.toLowerCase().includes(search.toLowerCase()) ||
                  a.category.toLowerCase().includes(search.toLowerCase()),
          )
        : agents;

    const categories = Object.keys(agentCategories).sort();

    if (paletteLoading) {
        return (
            <div className="pc-palette pc-palette-loading">
                <Loader2 size={20} className="spin" />
                <span>Loading agents...</span>
            </div>
        );
    }

    if (agents.length === 0) {
        return (
            <div className="pc-palette pc-palette-empty">
                <Cpu size={32} strokeWidth={1} />
                <span>No agents registered</span>
            </div>
        );
    }

    return (
        <div className="pc-palette">
            {/* Search */}
            <div className="pc-palette-search">
                <Search size={14} className="pc-palette-search-icon" />
                <input
                    type="text"
                    placeholder="Search agents..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                />
            </div>

            {/* Agent count */}
            <div className="pc-palette-header">
                <span>{filteredAgents.length} agent{filteredAgents.length !== 1 ? 's' : ''}</span>
            </div>

            {/* Pipeline Blocks - LEGO blocks for orchestration */}
            <div className="pc-palette-blocks">
                <div className="pc-palette-blocks-label">Pipeline Blocks</div>
                <div
                    className="pc-palette-block pc-palette-block-supervisor"
                    draggable
                    onDragStart={(e) => handleBlockDragStart(e, 'supervisor')}
                    title="Drag to add a supervisor agent between stages"
                >
                    <Shield size={14} className="pc-palette-block-icon" />
                    <span>Supervisor</span>
                </div>
                <div
                    className="pc-palette-block pc-palette-block-gate"
                    draggable
                    onDragStart={(e) => handleBlockDragStart(e, 'gate')}
                    title="Drag to add a decision gate (pass/fail gate)"
                >
                    <GitBranch size={14} className="pc-palette-block-icon" />
                    <span>Decision Gate</span>
                </div>
                <div
                    className="pc-palette-block pc-palette-block-loop"
                    draggable
                    onDragStart={(e) => handleBlockDragStart(e, 'loop')}
                    title="Drag to add a loop block (iteration control)"
                >
                    <Repeat size={14} className="pc-palette-block-icon" />
                    <span>Loop Block</span>
                </div>
            </div>

            {/* Categories */}
            {categories.map((cat) => {
                const catAgents = filteredAgents.filter((a) => a.category === cat);
                if (catAgents.length === 0 && search) return null;

                const isExpanded = expandedCategories[cat] ?? true;
                const color = CATEGORY_COLORS[cat] || CATEGORY_COLORS.unknown;

                return (
                    <div key={cat} className="pc-palette-category">
                        <div
                            className="pc-palette-category-header"
                            onClick={() => toggleCategory(cat)}
                            role="button"
                            tabIndex={0}
                        >
                            <div className="pc-palette-cat-indicator" style={{ backgroundColor: color }} />
                            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            <span className="pc-palette-cat-label">{cat}</span>
                            <span className="pc-palette-cat-count">{catAgents.length}</span>
                        </div>

                        {isExpanded && (
                            <div className="pc-palette-agents">
                                {catAgents.map((agent) => (
                                    <div
                                        key={agent.id}
                                        className="pc-palette-agent"
                                        draggable
                                        onDragStart={(e) => handleDragStart(e, agent)}
                                        title={`${agent.name} (${agent.id})`}
                                    >
                                        <GripVertical size={12} className="pc-palette-agent-grip" />
                                        <span className="pc-palette-agent-name">{agent.name}</span>
                                        {agent.model_id && (
                                            <span className="pc-palette-agent-model">{agent.model_id.split('-')[0]}</span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}

export const AgentPalette = memo(AgentPaletteInner);
