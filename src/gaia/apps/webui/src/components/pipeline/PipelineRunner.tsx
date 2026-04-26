// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PipelineRunner - Run pipelines and view real-time SSE streaming progress.
 *
 * Provides a form to configure and execute pipeline runs, with a live event
 * log showing each SSE event (status, step, thinking, tool calls, done, error).
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Play,
  Loader2,
  Square,
  Trash2,
  Terminal,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Users,
  RotateCcw,
  Gauge,
  Zap,
  Search,
  Layers,
  List,
  History,
  LayoutGrid,
  Shield,
  GitBranch,
  ListTree,
} from 'lucide-react';
import { usePipelineStore } from '../../stores/pipelineStore';
import { useChatStore } from '../../stores/chatStore';
import { useTemplateStore } from '../../stores/templateStore';
import { usePipelineCanvasStore, PIPELINE_STAGES } from '../../stores/pipelineCanvasStore';
import { PipelineCanvas } from './PipelineCanvas';
import { ExecutionHistory } from './ExecutionHistory';
import { TemplateMarketplace } from './TemplateMarketplace';
import { VersionHistory } from './VersionHistory';
import { VersionDiff } from './VersionDiff';
import type { TemplateVersion } from '../../types';
import './PipelineRunner.css';

/** Map 5-stage pipeline phases to actual agent categories in config/agents/. */
const STAGE_CATEGORY_MAP: Record<string, string> = {
  domain_analysis: 'analysis',
  workflow_modeling: 'analysis',
  loom_building: 'orchestration',
  gap_detection: 'orchestration',
  pipeline_execution: 'orchestration',
};

const STAGE_ICONS: Record<string, string> = {
  planning: '📋',
  development: '⚙️',
  quality: '🔍',
  decision: '📊',
  analysis: '🔬',
  orchestration: '🕸️',
};

const EVENT_ICONS: Record<string, React.ReactNode> = {
  status: <Terminal size={14} />,
  step: <ChevronRight size={14} />,
  thinking: <AlertTriangle size={14} />,
  tool_start: <ChevronRight size={14} />,
  tool_end: <CheckCircle2 size={14} />,
  tool_result: <Terminal size={14} />,
  done: <CheckCircle2 size={14} />,
  error: <XCircle size={14} />,
  // Recursive pipeline events
  loop_back: <RotateCcw size={14} />,
  quality_score: <Gauge size={14} />,
  phase_jump: <Zap size={14} />,
  iteration_start: <Play size={14} />,
  iteration_end: <CheckCircle2 size={14} />,
  defect_found: <Search size={14} />,
};

const EVENT_COLORS: Record<string, string> = {
  status: 'event-status',
  step: 'event-step',
  thinking: 'event-thinking',
  tool_start: 'event-tool',
  tool_end: 'event-tool-end',
  tool_result: 'event-result',
  done: 'event-done',
  error: 'event-error',
  // Recursive pipeline events
  loop_back: 'event-loop',
  quality_score: 'event-quality',
  phase_jump: 'event-phase',
  iteration_start: 'event-iteration',
  iteration_end: 'event-iteration-end',
  defect_found: 'event-defect',
};

const STATUS_COLORS: Record<string, string> = {
  starting: '#8b5cf6',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  cancelled: '#f59e0b',
};

export function PipelineRunner({ onViewChange }: { onViewChange?: (view: 'chat' | 'templates' | 'runner') => void }) {
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);

  const {
    executions,
    activeExecution,
    isRunning,
    lastError,
    runPipeline,
    cancelPipeline,
    clearExecution,
    clearAllExecutions,
    setLastError,
  } = usePipelineStore();

  const { templates, fetchTemplates } = useTemplateStore((s) => ({
    templates: s.templates,
    fetchTemplates: s.fetchTemplates,
  }));

  // Canvas workspace state (for workspace visibility panel)
  const canvasState = usePipelineCanvasStore((s) => ({
    nodes: s.nodes,
    qualityThreshold: s.qualityThreshold,
    maxIterations: s.maxIterations,
  }));

  const canvasAgentCount = canvasState.nodes.filter((n) => n.type === 'agent').length;
  const canvasSupervisorCount = canvasState.nodes.filter((n) => n.type === 'supervisor').length;
  const canvasGateCount = canvasState.nodes.filter((n) => n.type === 'gate').length;
  const canvasLoopCount = canvasState.nodes.filter((n) => n.type === 'loop').length;
  const hasCanvasContent = canvasAgentCount > 0 || canvasSupervisorCount > 0 || canvasGateCount > 0;

  const [taskDescription, setTaskDescription] = useState('');
  const [autoSpawn, setAutoSpawn] = useState(true);
  const [templateName, setTemplateName] = useState('');
  const [sessionId, setSessionId] = useState(currentSessionId || '');
  const [activeTab, setActiveTab] = useState<'canvas' | 'runner' | 'history' | 'marketplace'>('canvas');
  // Version history state
  const [versionTemplateName, setVersionTemplateName] = useState<string | null>(null);
  const [diffVersions, setDiffVersions] = useState<{ a: TemplateVersion; b: TemplateVersion } | null>(null);
  // Use array for collapsed event keys (immutable, React-detectable)
  const [collapsedEvents, setCollapsedEvents] = useState<string[]>([]);

  const eventLogRef = useRef<HTMLDivElement>(null);

  // Fetch templates on mount
  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  // Sync session ID when current session changes
  useEffect(() => {
    if (currentSessionId) {
      setSessionId(currentSessionId);
    }
  }, [currentSessionId]);

  // Get selected template's agent categories for display
  const selectedTemplate = templates.find((t) => t.name === templateName) || null;
  const agentCategories = selectedTemplate?.agent_categories || null;

  // Build phase-to-agent mapping for display
  const phaseAgents: Array<{ stage: string; agents: string[]; category: string }> = selectedTemplate
    ? [
        { stage: 'Domain Analysis', agents: agentCategories?.analysis || [], category: 'analysis' },
        { stage: 'Workflow Modeling', agents: agentCategories?.analysis || [], category: 'analysis' },
        { stage: 'Loom Building', agents: agentCategories?.orchestration || [], category: 'orchestration' },
        { stage: 'Gap Detection', agents: agentCategories?.orchestration || [], category: 'orchestration' },
        { stage: 'Pipeline Execution', agents: agentCategories?.orchestration || [], category: 'orchestration' },
      ]
    : [];

  // Auto-scroll event log
  useEffect(() => {
    if (eventLogRef.current && activeExecution) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight;
    }
  }, [activeExecution?.events.length]);

  const handleRun = useCallback(() => {
    if (!taskDescription.trim() || !sessionId || isRunning) return;

    // Collect canvas loop and supervisor config from the canvas store
    const canvasState = usePipelineCanvasStore.getState();
    const loopNodes = canvasState.nodes.filter((n) => n.type === 'loop' && n.loopConfig);
    const canvasLoops = loopNodes.map((n) => ({
      loop_id: n.loopConfig!.loop_id,
      label: n.loopConfig!.label,
      agent_ids: n.loopConfig!.agent_ids,
      max_iterations: n.loopConfig!.max_iterations,
      quality_threshold: n.loopConfig!.quality_threshold,
      source_stage: n.loopConfig!.source_stage,
      target_stage: n.loopConfig!.target_stage,
      condition: n.loopConfig!.condition || 'quality_below_threshold',
    }));

    const supervisorNodes = canvasState.nodes.filter((n) => n.type === 'supervisor' && n.supervisorConfig);
    const canvasSupervisors = supervisorNodes.map((n) => ({
      supervisor_id: n.supervisorConfig!.supervisor_id,
      label: n.supervisorConfig!.label,
      agent_id: n.supervisorConfig!.agent_id,
      decision_condition: n.supervisorConfig!.decision_condition,
      decision_type: n.supervisorConfig!.decision_type,
      monitoring_targets: n.supervisorConfig!.monitoring_targets || [],
    }));

    const result = runPipeline({
      session_id: sessionId,
      task_description: taskDescription,
      auto_spawn: autoSpawn,
      template_name: templateName || undefined,
      stream: true,
      canvas_loops: canvasLoops.length > 0 ? canvasLoops : undefined,
      canvas_supervisors: canvasSupervisors.length > 0 ? canvasSupervisors : undefined,
    });

    if (result) {
      setTaskDescription('');
    }
  }, [taskDescription, sessionId, isRunning, autoSpawn, templateName, runPipeline]);

  const handleCancel = useCallback(() => {
    cancelPipeline();
  }, [cancelPipeline]);

  const toggleEventCollapse = useCallback((eventId: string) => {
    setCollapsedEvents((prev) =>
      prev.includes(eventId)
        ? prev.filter((id) => id !== eventId)
        : [...prev, eventId]
    );
  }, []);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString();
  };

  const stageProgress = () => {
    if (!activeExecution) return { current: 0, total: 5 };
    const stageOrder = [
      'domain_analysis',
      'workflow_modeling',
      'loom_building',
      'gap_detection',
      'pipeline_execution',
    ];
    const idx = stageOrder.indexOf(activeExecution.currentStage || 'pipeline_execution');
    return { current: Math.max(0, idx) + 1, total: 5 };
  };

  const progress = stageProgress();

  return (
    <div className="pipeline-runner">
      {/* Header with tabs */}
      <div className="pr-header">
        <div className="pr-header-left">
          <h1>Pipeline Execution</h1>
          <p>Run the 5-stage autonomous agent spawning pipeline</p>
        </div>
        <div className="pr-tabs">
          <button
            className={`pr-tab ${activeTab === 'canvas' ? 'active' : ''}`}
            onClick={() => setActiveTab('canvas')}
          >
            <Layers size={14} />
            Canvas
          </button>
          <button
            className={`pr-tab ${activeTab === 'runner' ? 'active' : ''}`}
            onClick={() => setActiveTab('runner')}
          >
            <List size={14} />
            Log View
          </button>
          <button
            className={`pr-tab ${activeTab === 'history' ? 'active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            <History size={14} />
            History
          </button>
          <button
            className={`pr-tab ${activeTab === 'marketplace' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('marketplace');
              fetchTemplates();
            }}
          >
            <LayoutGrid size={14} />
            Marketplace
          </button>
        </div>
      </div>

      {/* Tab content */}
      {activeTab === 'marketplace' ? (
        <div className="pr-marketplace-content">
          <TemplateMarketplace
            onUseTemplate={(name: string) => {
              setTemplateName(name);
              setActiveTab('runner');
            }}
            onViewVersions={(name) => {
              setVersionTemplateName(name);
              // Auto-switch to runner tab with version history visible
              setActiveTab('runner');
            }}
          />
          {/* Version history overlay for selected template */}
          {versionTemplateName && (
            <div className="pr-version-panel">
              <VersionHistory
                templateName={versionTemplateName}
                onClose={() => setVersionTemplateName(null)}
                onCompare={(versionA, versionB) => {
                  setDiffVersions({ a: versionA, b: versionB });
                }}
              />
              {/* Version diff panel */}
              {diffVersions && (
                <VersionDiff
                  versionA={diffVersions.a}
                  versionB={diffVersions.b}
                  onClose={() => setDiffVersions(null)}
                />
              )}
            </div>
          )}
        </div>
      ) : activeTab === 'canvas' ? (
        <PipelineCanvas />
      ) : activeTab === 'history' ? (
        <div className="pr-history-content">
          <ExecutionHistory
            onReplay={(taskDescription, agentsUsed) => {
              setTaskDescription(taskDescription);
              setActiveTab('runner');
            }}
          />
        </div>
      ) : (
      <div className="pr-runner-content">
      {/* Configuration Form */}
      <div className="pr-form-card">
        <h2>Configure Pipeline</h2>

        <div className="pr-field">
          <label htmlFor="pr-task">Task Description</label>
          <textarea
            id="pr-task"
            placeholder="Describe what you want the pipeline to analyze or build..."
            value={taskDescription}
            onChange={(e) => setTaskDescription(e.target.value)}
            disabled={isRunning}
            rows={3}
          />
        </div>

        <div className="pr-field">
          <label htmlFor="pr-session">Session</label>
          <select
            id="pr-session"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            disabled={isRunning}
          >
            <option value="">-- Select session --</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title || s.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </div>

        <div className="pr-field pr-field-row">
          <label className="pr-checkbox-label">
            <input
              type="checkbox"
              checked={autoSpawn}
              onChange={(e) => setAutoSpawn(e.target.checked)}
              disabled={isRunning}
            />
            Auto-spawn agents for gaps
          </label>

          <div>
            <label htmlFor="pr-template" style={{ marginRight: 8 }}>
              Template (optional)
            </label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                id="pr-template"
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                disabled={isRunning}
                style={{ maxWidth: 200 }}
              >
                <option value="">Default</option>
                {templates.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button
                className="pr-btn pr-btn-secondary"
                onClick={() => onViewChange?.('templates')}
                style={{ padding: '4px 8px', fontSize: 12, height: 32 }}
                title="Manage Templates"
              >
                Manage Templates
              </button>
            </div>
          </div>
        </div>

        {/* Agent Ecosystem Preview */}
        {agentCategories && Object.keys(agentCategories).length > 0 && (
          <div className="pr-agent-ecosystem">
            <div className="pr-agent-ecosystem-header">
              <Users size={16} />
              <h3>Agent Ecosystem</h3>
              <span className="pr-agent-ecosystem-count">
                {Object.values(agentCategories).flat().length} agents across {Object.keys(agentCategories).length} categories
              </span>
            </div>
            <div className="pr-agent-categories">
              {Object.entries(agentCategories).map(([category, agents]) => (
                <div key={category} className="pr-agent-category">
                  <div className="pr-agent-category-header">
                    <span className="pr-agent-category-icon">{STAGE_ICONS[category] || '🤖'}</span>
                    <span className="pr-agent-category-label">{category.charAt(0).toUpperCase() + category.slice(1)}</span>
                    <span className="pr-agent-category-count">{agents.length} agent{agents.length !== 1 ? 's' : ''}</span>
                  </div>
                  <div className="pr-agent-category-agents">
                    {agents.map((agent) => (
                      <span key={agent} className="pr-agent-chip">
                        {agent}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            {/* Phase-to-agent mapping */}
            <div className="pr-phase-mapping">
              <h4>Phase → Agent Mapping</h4>
              <div className="pr-phase-agents-list">
                {phaseAgents.map((pa) => (
                  <div key={pa.stage} className="pr-phase-item">
                    <span className="pr-phase-name">{pa.stage}</span>
                    <ChevronRight size={12} className="pr-phase-arrow" />
                    {pa.agents.length > 0 ? (
                      <span className="pr-phase-agent-names">
                        {pa.agents.join(', ')}
                      </span>
                    ) : (
                      <span className="pr-phase-no-agent">No agent assigned</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
            {/* Routing rules summary */}
            {selectedTemplate?.routing_rules && selectedTemplate.routing_rules.length > 0 && (
              <div className="pr-routing-rules">
                <h4>Routing Rules ({selectedTemplate.routing_rules.length})</h4>
                {selectedTemplate.routing_rules.map((rule, i) => (
                  <div key={i} className="pr-rule">
                    <span className="pr-rule-condition">{rule.condition}</span>
                    <ChevronRight size={12} className="pr-phase-arrow" />
                    <span className="pr-rule-target">{rule.route_to}</span>
                    {rule.loop_back && <span className="pr-rule-loop">↻ loop</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="pr-actions">
          <button
            className="pr-btn pr-btn-primary"
            onClick={handleRun}
            disabled={!taskDescription.trim() || !sessionId || isRunning}
          >
            {isRunning ? (
              <>
                <Loader2 size={16} className="spin" />
                Running...
              </>
            ) : (
              <>
                <Play size={16} />
                Run Pipeline
              </>
            )}
          </button>

          {isRunning && (
            <button className="pr-btn pr-btn-danger" onClick={handleCancel}>
              <Square size={14} />
              Cancel
            </button>
          )}

          {executions.length > 0 && !isRunning && (
            <button
              className="pr-btn pr-btn-secondary"
              onClick={clearAllExecutions}
            >
              <Trash2 size={14} />
              Clear History
            </button>
          )}
        </div>

        {lastError && (
          <div className="pr-error" role="alert">
            <AlertTriangle size={16} />
            <span>{lastError}</span>
            <button onClick={() => setLastError(null)}>Dismiss</button>
          </div>
        )}
      </div>

      {/* Workspace Visibility Panel */}
      {hasCanvasContent && (
        <div className="pr-workspace">
          <div className="pr-workspace-header">
            <ListTree size={16} />
            <h3>Canvas Workspace</h3>
            <span className="pr-workspace-count">
              {canvasAgentCount} agent{canvasAgentCount !== 1 ? 's' : ''}
              {canvasSupervisorCount > 0 && ` · ${canvasSupervisorCount} supervisor${canvasSupervisorCount !== 1 ? 's' : ''}`}
              {canvasGateCount > 0 && ` · ${canvasGateCount} gate${canvasGateCount !== 1 ? 's' : ''}`}
              {canvasLoopCount > 0 && ` · ${canvasLoopCount} loop${canvasLoopCount !== 1 ? 's' : ''}`}
            </span>
          </div>
          {/* Stage-by-stage breakdown */}
          <div className="pr-workspace-stages">
            {PIPELINE_STAGES.map((stage) => {
              const stageAgents = canvasState.nodes.filter(
                (n) => n.type === 'agent' && n.assignedStage === stage.key
              );
              const stageSupervisors = canvasState.nodes.filter(
                (n) => n.type === 'supervisor' && n.assignedStage === stage.key
              );
              const stageGates = canvasState.nodes.filter(
                (n) => n.type === 'gate' && n.assignedStage === stage.key
              );
              const stageLoops = canvasState.nodes.filter(
                (n) => n.type === 'loop' && n.assignedStage === stage.key
              );
              return (
                <div key={stage.key} className="pr-workspace-stage">
                  <div className="pr-workspace-stage-header">
                    <span className="pr-workspace-stage-num">{stage.order}</span>
                    <span className="pr-workspace-stage-label">{stage.label}</span>
                  </div>
                  {stageAgents.length > 0 && (
                    <div className="pr-workspace-stage-agents">
                      {stageAgents.map((a: any) => (
                        <span key={a.id} className="pr-workspace-agent-chip">
                          {a.label || a.agentId}
                        </span>
                      ))}
                    </div>
                  )}
                  {stageSupervisors.length > 0 && (
                    <div className="pr-workspace-stage-supervisors">
                      {stageSupervisors.map((s: any) => (
                        <span key={s.id} className="pr-workspace-supervisor-chip">
                          <Shield size={10} />
                          {s.label}
                        </span>
                      ))}
                    </div>
                  )}
                  {stageGates.length > 0 && (
                    <div className="pr-workspace-stage-gates">
                      {stageGates.map((g: any) => (
                        <span key={g.id} className="pr-workspace-gate-chip">
                          <GitBranch size={10} />
                          {g.label} → {g.gateCondition}
                        </span>
                      ))}
                    </div>
                  )}
                  {stageLoops.length > 0 && (
                    <div className="pr-workspace-stage-loops">
                      {stageLoops.map((l: any) => (
                        <span key={l.id} className="pr-workspace-loop-chip">
                          <RotateCcw size={10} />
                          {l.label} → {l.loopConfig?.target_stage || 'unknown'}
                        </span>
                      ))}
                    </div>
                  )}
                  {stageAgents.length === 0 && stageSupervisors.length === 0 && stageGates.length === 0 && stageLoops.length === 0 && (
                    <span className="pr-workspace-stage-empty">Empty stage</span>
                  )}
                </div>
              );
            })}
          </div>
          {/* Canvas config summary */}
          <div className="pr-workspace-config">
            <span>Quality: {(canvasState.qualityThreshold * 100).toFixed(0)}%</span>
            <span>Max iterations: {canvasState.maxIterations}</span>
          </div>
        </div>
      )}

      {/* Stage Progress Indicator */}
      {activeExecution && (
        <div className="pr-stages">
          {['Domain Analysis', 'Workflow Modeling', 'Loom Building', 'Gap Detection', 'Execution'].map(
            (stage, i) => {
              const stageKeys = [
                'domain_analysis',
                'workflow_modeling',
                'loom_building',
                'gap_detection',
                'pipeline_execution',
              ];
              const isActive = activeExecution.currentStage === stageKeys[i];
              const isComplete =
                activeExecution.status === 'completed' ||
                progress.current > i + 1;

              return (
                <div
                  key={stage}
                  className={`pr-stage ${isActive ? 'active' : ''} ${isComplete ? 'complete' : ''}`}
                >
                  <div className="pr-stage-dot">
                    {isComplete ? <CheckCircle2 size={14} /> : i + 1}
                  </div>
                  <span className="pr-stage-label">{stage}</span>
                </div>
              );
            }
          )}
        </div>
      )}

      {/* Execution History */}
      {executions.length === 0 && (
        <div className="pr-empty">
          <Terminal size={48} strokeWidth={1} />
          <h3>No pipeline runs</h3>
          <p>Configure and run a pipeline to see results here.</p>
        </div>
      )}

      {executions.map((exec) => {
        const isActive = exec.id === activeExecution?.id;
        const statusColor = STATUS_COLORS[exec.status] || '#6b7280';

        return (
          <div key={exec.id} className={`pr-execution ${isActive ? 'active' : ''}`}>
            <div className="pr-execution-header">
              <div className="pr-execution-title">
                <span
                  className="pr-status-dot"
                  style={{ backgroundColor: statusColor }}
                />
                <span className="pr-execution-id">
                  Run {exec.id.slice(0, 8)}
                </span>
                <span className="pr-execution-status">
                  {exec.status}
                </span>
                {isActive && exec.currentIteration !== undefined && exec.currentIteration > 1 && (
                  <span className="pr-iteration-badge" title="Current loop iteration">
                    Iteration {exec.currentIteration}
                  </span>
                )}
                {exec.loopCount !== undefined && exec.loopCount > 0 && (
                  <span className="pr-loop-count-badge" title="Total loops executed">
                    {exec.loopCount} loop{exec.loopCount !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <div className="pr-execution-meta">
                <span>{formatTime(exec.startTime)}</span>
                {exec.endTime && <span>{formatTime(exec.endTime)}</span>}
                {!isActive && (
                  <button
                    className="pr-btn-icon"
                    onClick={() => clearExecution(exec.id)}
                    title="Clear"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            </div>

            <div className="pr-execution-task">{exec.taskDescription}</div>

            {/* Event Log */}
            {exec.events.length > 0 && (
              <div className="pr-event-log" ref={isActive ? eventLogRef : undefined}>
                <div className="pr-event-log-header">
                  <span>Event Log ({exec.events.length} events)</span>
                </div>
                <div className="pr-event-list">
                  {exec.events.map((event, i) => {
                    const eventType =
                      'type' in event ? (event as any).type : 'status';
                    const eventKey = `${exec.id}-${i}`;
                    const isCollapsed = collapsedEvents.includes(eventKey);
                    const icon = EVENT_ICONS[eventType] || <Terminal size={14} />;
                    const colorClass = EVENT_COLORS[eventType] || 'event-status';
                    const content =
                      'content' in event ? (event as any).content : '';
                    const message =
                      'message' in event ? (event as any).message : '';
                    const toolName =
                      'tool' in event ? (event as any).tool : '';
                    const targetPhase =
                      'target_phase' in event ? (event as any).target_phase : undefined;
                    const qualityScore =
                      'quality_score' in event ? (event as any).quality_score : undefined;
                    const iteration =
                      'iteration' in event ? (event as any).iteration : undefined;

                    // Special rendering for recursive pipeline events
                    const isSpecialEvent = ['loop_back', 'quality_score', 'phase_jump', 'iteration_start', 'iteration_end', 'defect_found'].includes(eventType);

                    return (
                      <div
                        key={eventKey}
                        className={`pr-event ${colorClass}${isSpecialEvent ? ' pr-event-recursive' : ''}`}
                      >
                        <div
                          className="pr-event-summary"
                          onClick={() => toggleEventCollapse(eventKey)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              toggleEventCollapse(eventKey);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                          aria-expanded={!isCollapsed}
                        >
                          <button className="pr-event-toggle">
                            {isCollapsed ? (
                              <ChevronRight size={12} />
                            ) : (
                              <ChevronDown size={12} />
                            )}
                          </button>
                          {icon}
                          <span className="pr-event-type">{eventType}</span>
                          {toolName && (
                            <span className="pr-event-tool">{toolName}</span>
                          )}
                          {/* Recursive event metadata */}
                          {iteration !== undefined && (
                            <span className="pr-event-iteration">#{iteration}</span>
                          )}
                          {targetPhase && (
                            <span className="pr-event-phase">→ {targetPhase}</span>
                          )}
                          {qualityScore !== undefined && (
                            <span className={`pr-event-quality ${qualityScore >= 0.9 ? 'pass' : 'fail'}`}>
                              {(qualityScore * 100).toFixed(0)}%
                            </span>
                          )}
                          <span className="pr-event-text">
                            {message || content}
                          </span>
                        </div>
                        {!isCollapsed && content && content !== message && (
                          <pre className="pr-event-detail">
                            {typeof content === 'string'
                              ? content
                              : JSON.stringify(content, null, 2)}
                          </pre>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })}
      </div>
      )}
    </div>
  );
}
