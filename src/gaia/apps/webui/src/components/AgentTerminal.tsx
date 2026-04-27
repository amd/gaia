// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import {
  Terminal,
  Search,
  Trash2,
  Pause,
  Play,
  Download,
  X,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  Wrench,
  Shield,
  Info,
  AlertCircle,
  ArrowDown,
} from 'lucide-react';
import { useTerminalStore, selectFilteredLines } from '../stores/terminalStore';
import { useAgentStore } from '../stores/agentStore';
import type { TerminalLine, TerminalTab, TerminalLineType } from '../types/agent';
import { formatTimeHMS } from '../utils/format';
import { log } from '../utils/logger';
import './AgentTerminal.css';

// ── Constants ─────────────────────────────────────────────────────────────

const TAB_CONFIG: { id: TerminalTab; label: string; description: string }[] = [
  { id: 'activity', label: 'Activity', description: 'Tool calls, permissions, and events' },
  { id: 'logs', label: 'Logs', description: 'Agent stderr output' },
  { id: 'raw', label: 'Raw', description: 'Raw JSON-RPC stdout' },
];

const LINE_TYPE_COLORS: Record<TerminalLineType, string> = {
  info: 'var(--text-secondary)',
  warn: 'var(--accent-yellow)',
  error: 'var(--amd-red)',
  tool: 'var(--accent-blue)',
  permission: 'var(--accent-yellow)',
  rpc: 'var(--text-muted)',
  stdout: 'var(--text-secondary)',
  stderr: 'var(--text-muted)',
};

const LINE_TYPE_ICONS: Record<TerminalLineType, typeof Info> = {
  info: Info,
  warn: AlertTriangle,
  error: AlertCircle,
  tool: Wrench,
  permission: Shield,
  rpc: Terminal,
  stdout: Terminal,
  stderr: Terminal,
};

// ── Terminal Line Component ──────────────────────────────────────────────

interface TerminalLineRowProps {
  line: TerminalLine;
  expanded: boolean;
  onToggle: () => void;
}

const TerminalLineRow = memo(function TerminalLineRow({ line, expanded, onToggle }: TerminalLineRowProps) {
  const IconComponent = LINE_TYPE_ICONS[line.type] || Terminal;
  const color = LINE_TYPE_COLORS[line.type] || 'var(--text-secondary)';

  return (
    <div
      className={`terminal-line terminal-line-${line.type} ${line.expandable ? 'expandable' : ''} ${expanded ? 'expanded' : ''}`}
      role="listitem"
    >
      <div className="terminal-line-main" onClick={line.expandable ? onToggle : undefined}>
        {/* Expand chevron for expandable lines */}
        {line.expandable ? (
          <span className="terminal-line-chevron">
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
        ) : (
          <span className="terminal-line-chevron terminal-line-chevron-spacer" />
        )}

        {/* Timestamp */}
        <span className="terminal-line-time">{formatTimeHMS(line.timestamp)}</span>

        {/* Type icon */}
        <span className="terminal-line-icon" style={{ color }}>
          <IconComponent size={12} />
        </span>

        {/* Type badge */}
        <span className="terminal-line-badge" style={{ color }}>{line.type}</span>

        {/* Content */}
        <span className="terminal-line-content">{line.content}</span>

        {/* Source indicator */}
        <span className="terminal-line-source">{line.source}</span>
      </div>

      {/* Expanded detail */}
      {expanded && line.detail && (
        <div className="terminal-line-detail">
          <pre>{line.detail}</pre>
        </div>
      )}
    </div>
  );
});

// ── Main Terminal Component ──────────────────────────────────────────────

interface AgentTerminalProps {
  agentId: string;
  onClose?: () => void;
}

export function AgentTerminal({ agentId, onClose }: AgentTerminalProps) {
  const {
    buffers,
    filters,
    paused,
    activeTabs,
    setFilter,
    togglePause,
    setActiveTab,
    clearBuffer,
  } = useTerminalStore();

  const agents = useAgentStore((s) => s.agents);
  const statuses = useAgentStore((s) => s.statuses);

  const agent = agents[agentId];
  const status = statuses[agentId];
  const activeTab = activeTabs[agentId] || 'activity';
  const isPaused = paused[agentId] || false;
  const filterText = filters[agentId] || '';

  // Get filtered lines using the store selector (type-safe via Zustand selector)
  const filteredLines = useTerminalStore(
    useCallback((state) => selectFilteredLines(state, agentId), [agentId]),
  );

  // Expanded state for individual lines (useState so toggling triggers re-render)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const toggleExpanded = useCallback((lineId: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) {
        next.delete(lineId);
      } else {
        next.add(lineId);
      }
      return next;
    });
  }, []);

  // Auto-scroll
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const shouldAutoScroll = !isPaused && !isScrolledUp;

  useEffect(() => {
    if (!shouldAutoScroll || !scrollContainerRef.current) return;
    const el = scrollContainerRef.current;
    el.scrollTop = el.scrollHeight;
  }, [filteredLines.length, shouldAutoScroll]);

  // Detect user scroll (with change-detection guard to avoid no-op re-renders)
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    const next = !isAtBottom;
    setIsScrolledUp((prev) => (prev === next ? prev : next));
  }, []);

  const scrollToBottom = useCallback(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
      setIsScrolledUp(false);
    }
  }, []);

  // ── IPC listeners for terminal data ───────────────────────────────────
  // Register once on mount. The onStdout/onStderr IPC bindings are
  // additive (addEventListener-style) and do not return cleanup functions
  // per the GaiaElectronAPI type. We filter by agentId inside the callback.
  useEffect(() => {
    const api = window.gaiaAPI;
    if (!api) return;

    api.agent.onStdout(({ agentId: id, message }) => {
      if (id === agentId) {
        useTerminalStore.getState().appendStdoutMessage(agentId, message);
      }
    });

    api.agent.onStderr(({ agentId: id, line }) => {
      if (id === agentId) {
        useTerminalStore.getState().appendStderrLine(agentId, line);
      }
    });
  }, [agentId]);

  // ── Export logs (reads from store directly to avoid stale closure) ────
  const handleExport = useCallback(() => {
    const lines = useTerminalStore.getState().buffers[agentId] || [];
    if (lines.length === 0) return;

    const agentName = useAgentStore.getState().agents[agentId]?.name || agentId;
    const content = lines
      .map((l) => `[${new Date(l.timestamp).toISOString()}] [${l.type}] [${l.source}] ${l.content}`)
      .join('\n');

    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${agentName}-terminal-${Date.now()}.log`;
    a.click();
    URL.revokeObjectURL(url);
    log.system.info(`[AgentTerminal] Exported ${lines.length} lines for ${agentId}`);
  }, [agentId]);

  // ── Clear logs (also resets expanded IDs) ────────────────────────────
  const handleClear = useCallback(() => {
    log.system.info(`[AgentTerminal] Clearing buffer for ${agentId}`);
    clearBuffer(agentId);
    setExpandedIds(new Set());
  }, [agentId, clearBuffer]);

  // ── Count helpers (memoized to avoid recomputing on every render) ────
  const agentBuffer = buffers[agentId] || [];
  const totalLines = agentBuffer.length;
  const errorCount = useMemo(
    () => agentBuffer.filter((l) => l.type === 'error').length,
    [agentBuffer],
  );

  return (
    <div className="agent-terminal" role="region" aria-label={`Terminal for ${agent?.name || agentId}`}>
      {/* Terminal header */}
      <div className="terminal-header">
        <div className="terminal-header-left">
          <Terminal size={16} className="terminal-icon" />
          <span className="terminal-agent-name">{agent?.name || agentId}</span>
          <span className={`terminal-status-badge ${status?.running ? 'running' : 'stopped'}`}>
            {status?.running ? 'Running' : 'Stopped'}
          </span>
        </div>
        <div className="terminal-header-right">
          {/* Line count */}
          <span className="terminal-line-count" title="Total lines">
            {totalLines} lines
          </span>
          {errorCount > 0 && (
            <span className="terminal-error-count" title="Errors">
              <AlertCircle size={12} />
              {errorCount}
            </span>
          )}
          {onClose && (
            <button
              className="btn-icon-sm"
              onClick={onClose}
              title="Close terminal"
              aria-label="Close terminal"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="terminal-tabs" role="tablist" aria-label="Terminal tabs">
        {TAB_CONFIG.map((tab) => (
          <button
            key={tab.id}
            className={`terminal-tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(agentId, tab.id)}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`terminal-panel-${tab.id}`}
            title={tab.description}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Toolbar */}
      <div className="terminal-toolbar">
        <div className="terminal-search">
          <Search size={13} className="terminal-search-icon" />
          <input
            type="text"
            placeholder="Filter output..."
            value={filterText}
            onChange={(e) => setFilter(agentId, e.target.value)}
            aria-label="Filter terminal output"
          />
          {filterText && (
            <button
              className="terminal-search-clear"
              onClick={() => setFilter(agentId, '')}
              aria-label="Clear filter"
            >
              <X size={12} />
            </button>
          )}
        </div>
        <div className="terminal-toolbar-actions">
          <button
            className={`btn-terminal-action ${isPaused ? 'active' : ''}`}
            onClick={() => togglePause(agentId)}
            title={isPaused ? 'Resume auto-scroll' : 'Pause auto-scroll'}
            aria-label={isPaused ? 'Resume auto-scroll' : 'Pause auto-scroll'}
          >
            {isPaused ? <Play size={13} /> : <Pause size={13} />}
          </button>
          <button
            className="btn-terminal-action"
            onClick={handleExport}
            title="Export logs"
            aria-label="Export logs"
            disabled={totalLines === 0}
          >
            <Download size={13} />
          </button>
          <button
            className="btn-terminal-action"
            onClick={handleClear}
            title="Clear terminal"
            aria-label="Clear terminal"
            disabled={totalLines === 0}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {/* Terminal content */}
      <div
        ref={scrollContainerRef}
        className="terminal-content"
        onScroll={handleScroll}
        role="list"
        aria-label="Terminal output"
        id={`terminal-panel-${activeTab}`}
      >
        {filteredLines.length === 0 ? (
          <div className="terminal-empty">
            {filterText ? (
              <>
                <Search size={24} strokeWidth={1} />
                <span>No lines matching "{filterText}"</span>
              </>
            ) : totalLines === 0 ? (
              <>
                <Terminal size={24} strokeWidth={1} />
                <span>No output yet</span>
                <span className="terminal-empty-hint">
                  {status?.running
                    ? 'Waiting for agent output...'
                    : 'Start the agent to see output here'}
                </span>
              </>
            ) : (
              <>
                <Info size={24} strokeWidth={1} />
                <span>No lines match the current tab filter</span>
              </>
            )}
          </div>
        ) : (
          filteredLines.map((line) => (
            <TerminalLineRow
              key={line.id}
              line={line}
              expanded={expandedIds.has(line.id)}
              onToggle={() => toggleExpanded(line.id)}
            />
          ))
        )}
      </div>

      {/* Scroll-to-bottom button (shown when scrolled up) */}
      {isScrolledUp && filteredLines.length > 0 && (
        <button
          className="terminal-scroll-btn"
          onClick={scrollToBottom}
          aria-label="Scroll to bottom"
        >
          <ArrowDown size={14} />
          <span>Scroll to bottom</span>
        </button>
      )}

      {/* Paused indicator */}
      {isPaused && (
        <div className="terminal-paused-banner">
          <Pause size={12} />
          <span>Auto-scroll paused</span>
          <button onClick={() => togglePause(agentId)}>Resume</button>
        </div>
      )}
    </div>
  );
}
