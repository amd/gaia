// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useMemo } from 'react';
import { Plus, Search } from 'lucide-react';
import type { AgentInfo } from '../types';
import { AgentHubCard } from './AgentHubCard';
import { AgentDetailModal } from './AgentDetailModal';
import './AgentHub.css';

interface AgentHubGridProps {
    agents: AgentInfo[];
    activeAgentId: string;
    onSelect: (id: string) => void;
    onStartChat: (id: string, prompt?: string) => void;
    onCreateAgent?: () => void;
}

// Detect Electron context once
const isElectron = typeof window !== 'undefined' && !!(window as any).electronAPI;

/** Sort: builtin first, then custom, then native. Alphabetical within groups. */
function sortAgents(agents: AgentInfo[]): AgentInfo[] {
    const order: Record<string, number> = { builtin: 0, custom_python: 1, native: 2 };
    return [...agents].sort((a, b) => {
        const oa = order[a.source] ?? 1;
        const ob = order[b.source] ?? 1;
        if (oa !== ob) return oa - ob;
        return a.name.localeCompare(b.name);
    });
}

export function AgentHubGrid({ agents, activeAgentId, onSelect, onStartChat, onCreateAgent }: AgentHubGridProps) {
    const [search, setSearch] = useState('');
    const [categoryFilter, setCategoryFilter] = useState('all');
    const [detailAgent, setDetailAgent] = useState<AgentInfo | null>(null);

    const showControls = agents.length > 6;

    // Unique categories for the filter dropdown
    const categories = useMemo(() => {
        const cats = new Set<string>();
        agents.forEach((a) => { if (a.category && a.category !== 'general') cats.add(a.category); });
        return Array.from(cats).sort();
    }, [agents]);

    // Filter + sort
    const filtered = useMemo(() => {
        let list = agents;
        if (search) {
            const q = search.toLowerCase();
            list = list.filter((a) =>
                a.name.toLowerCase().includes(q) ||
                a.description.toLowerCase().includes(q) ||
                (a.tags ?? []).some((t) => t.toLowerCase().includes(q)) ||
                (a.category ?? '').toLowerCase().includes(q)
            );
        }
        if (categoryFilter !== 'all') {
            list = list.filter((a) => a.category === categoryFilter);
        }
        return sortAgents(list);
    }, [agents, search, categoryFilter]);

    return (
        <div className="agent-hub">
            <div className="agent-hub-header">
                <span className="agent-hub-title">Choose Your Agent</span>
                {showControls && (
                    <div className="agent-hub-controls">
                        <div className="agent-hub-search">
                            <Search size={14} />
                            <input
                                type="text"
                                placeholder="Search agents..."
                                aria-label="Search agents"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                            />
                        </div>
                        {categories.length > 0 && (
                            <select
                                className="agent-hub-filter"
                                aria-label="Filter by category"
                                value={categoryFilter}
                                onChange={(e) => setCategoryFilter(e.target.value)}
                            >
                                <option value="all">All categories</option>
                                {categories.map((c) => (
                                    <option key={c} value={c}>{c}</option>
                                ))}
                            </select>
                        )}
                    </div>
                )}
            </div>

            <div className="agent-hub-grid">
                {filtered.length === 0 && (
                    <div className="agent-hub-empty">No agents match your search.</div>
                )}
                {filtered.map((agent) => (
                    <AgentHubCard
                        key={agent.id}
                        agent={agent}
                        isActive={agent.id === activeAgentId}
                        isElectron={isElectron}
                        onSelect={onSelect}
                        onStartChat={(id) => onStartChat(id)}
                        onViewDetails={setDetailAgent}
                    />
                ))}
                {onCreateAgent && (
                    <div
                        className="agent-hub-card create-new"
                        role="button"
                        tabIndex={0}
                        onClick={onCreateAgent}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onCreateAgent(); } }}
                    >
                        <div className="create-icon"><Plus size={22} /></div>
                        <div className="create-title">Build a Custom Agent</div>
                        <div className="create-desc">Create a new agent through conversation</div>
                    </div>
                )}
            </div>

            {detailAgent && (
                <AgentDetailModal
                    agent={detailAgent}
                    onClose={() => setDetailAgent(null)}
                    onStartChat={onStartChat}
                />
            )}
        </div>
    );
}
