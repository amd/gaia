// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Types for agent management, terminal, notifications, and permissions. */

// ── Agent Types ──────────────────────────────────────────────────────────

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  binaries: Record<string, string>;  // platform → binary name
  language?: string;
  toolsCount: number;
  categories: string[];
  requiresAdmin: boolean;
  capabilities: {
    standaloneMode: boolean;
    notifications: boolean;
    interactiveChat: boolean;
  };
  downloadUrls?: Record<string, string>;
  sha256?: Record<string, string>;
  sizeBytes?: number;
}

export interface AgentStatus {
  installed: boolean;
  running: boolean;
  pid?: number;
  uptime?: number;         // seconds
  memoryMB?: number;
  lastHealthCheck?: number; // timestamp
  healthy?: boolean;
  error?: string;
}

export type AgentInstallState = 'not_installed' | 'downloading' | 'verifying' | 'installing' | 'installed' | 'failed';

export interface AgentInstallProgress {
  agentId: string;
  state: AgentInstallState;
  progress: number;  // 0-100
  error?: string;
}

// ── Terminal Types ───────────────────────────────────────────────────────

export type TerminalLineType = 'info' | 'warn' | 'error' | 'tool' | 'permission' | 'rpc' | 'stdout' | 'stderr';

export interface TerminalLine {
  id: number;
  timestamp: number;
  type: TerminalLineType;
  source: 'stdout' | 'stderr';
  content: string;
  /** Parsed JSON-RPC message (for stdout lines). */
  rpcMessage?: JsonRpcMessage;
  /** Whether this line can be expanded for details. */
  expandable?: boolean;
  /** Expanded detail content. */
  detail?: string;
}

export type TerminalTab = 'activity' | 'logs' | 'raw';

// ── JSON-RPC Types ───────────────────────────────────────────────────────

export interface JsonRpcRequest {
  jsonrpc: '2.0';
  method: string;
  id?: string | number;
  params?: Record<string, unknown>;
}

export interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number;
  result?: unknown;
  error?: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export interface JsonRpcNotification {
  jsonrpc: '2.0';
  method: string;
  params?: Record<string, unknown>;
}

export type JsonRpcMessage = JsonRpcRequest | JsonRpcResponse | JsonRpcNotification;

// ── Notification Types ───────────────────────────────────────────────────

export type NotificationType = 'permission_request' | 'security_alert' | 'status_change' | 'info' | 'error';
export type NotificationPriority = 'low' | 'medium' | 'high' | 'critical';

export interface GaiaNotification {
  id: string;
  type: NotificationType;
  agentId: string;
  agentName: string;
  title: string;
  message: string;
  timestamp: number;
  read: boolean;
  dismissed: boolean;
  priority: NotificationPriority;
  /** For permission_request type. */
  tool?: string;
  toolArgs?: Record<string, unknown>;
  actions?: string[];
  timeoutSeconds?: number;
  /** Response (after user action). */
  response?: 'allow' | 'deny';
  respondedAt?: number;
}

// ── Permission Types ─────────────────────────────────────────────────────

export type PermissionTier = 'auto' | 'confirm' | 'escalate';

export interface ToolPermission {
  tool: string;
  defaultTier: PermissionTier;
  overrideTier?: PermissionTier;
}

export interface AgentPermissions {
  agentId: string;
  tools: ToolPermission[];
}

// ── Tray Config Types ────────────────────────────────────────────────────

export interface AgentConfig {
  autoStart: boolean;
  restartOnCrash: boolean;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
}

export interface TrayConfig {
  agents: Record<string, AgentConfig>;
  tray: {
    minimizeToTray: boolean;
    startMinimized: boolean;
    startOnLogin: boolean;
    showNotificationBadge: boolean;
  };
}

// ── Agent Chat Types ─────────────────────────────────────────────────────

export interface AgentChatMessage {
  id: string;
  agentId: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: number;
  /** Tool calls made during this response. */
  toolCalls?: AgentToolCall[];
  /** Whether this message is still streaming. */
  streaming?: boolean;
}

export interface AgentToolCall {
  tool: string;
  args: Record<string, unknown>;
  resultSummary?: string;
  success?: boolean;
}

export interface AgentChatSession {
  agentId: string;
  agentName: string;
  messages: AgentChatMessage[];
  /** Quick action buttons for this agent. */
  quickActions?: QuickAction[];
}

export interface QuickAction {
  label: string;
  method: string;
  params?: Record<string, unknown>;
  icon?: string;
}

// ── Audit Log Types ──────────────────────────────────────────────────────

export interface AuditEntry {
  id: string;
  timestamp: number;
  agentId: string;
  agentName: string;
  tool: string;
  tier: PermissionTier;
  args: Record<string, unknown>;
  success: boolean;
  resultSummary?: string;
  reversible: boolean;
  rolledBack?: boolean;
}

// ── System Metrics Types ─────────────────────────────────────────────────

export interface ProcessInfo {
  pid: number;
  name: string;
  cpuPercent: number;
  memoryMB: number;
  uptime: number;  // seconds
}

export interface SystemMetrics {
  cpuPercent: number;
  memoryUsedGB: number;
  memoryTotalGB: number;
  diskUsedGB: number;
  diskTotalGB: number;
  gpuPercent?: number;
  gpuMemoryUsedMB?: number;
  gpuMemoryTotalMB?: number;
  gpuTempC?: number;
  npuPercent?: number;
  networkUp: boolean;
  processes: ProcessInfo[];
  timestamp: number;
}

// ── Electron Preload API ─────────────────────────────────────────────────

/**
 * Type-safe interface for window.gaiaAPI exposed by preload.cjs.
 * Available only when running inside Electron.
 */
export interface GaiaElectronAPI {
  agent: {
    start: (id: string) => Promise<void>;
    stop: (id: string) => Promise<void>;
    restart: (id: string) => Promise<void>;
    status: (id: string) => Promise<AgentStatus>;
    statusAll: () => Promise<Record<string, AgentStatus>>;
    sendRpc: (id: string, method: string, params?: Record<string, unknown>) => Promise<unknown>;
    onStdout: (cb: (data: { agentId: string; message: JsonRpcMessage }) => void) => void;
    onStderr: (cb: (data: { agentId: string; line: string }) => void) => void;
    onCrashed: (cb: (data: { agentId: string; exitCode: number; signal?: string }) => void) => void;
  };
  tray: {
    getConfig: () => Promise<TrayConfig>;
    setConfig: (config: Partial<TrayConfig>) => Promise<void>;
  };
  notification: {
    onPermissionRequest: (cb: (data: GaiaNotification) => void) => void;
    respondPermission: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;
    onNotification: (cb: (data: GaiaNotification) => void) => void;
  };
}

// Augment Window to include gaiaAPI when running in Electron
declare global {
  interface Window {
    gaiaAPI?: GaiaElectronAPI;
  }
}
