// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState } from 'react';
import {
  X,
  Send,
  Zap,
  Trash2,
  Bot,
  Wrench,
  CheckCircle,
  XCircle,
  Loader,
} from 'lucide-react';
import {
  useAgentChatStore,
  selectActiveSession,
  selectIsWaiting,
  selectInputText,
} from '../stores/agentChatStore';
import type { AgentChatMessage, AgentToolCall } from '../types/agent';
import './AgentChat.css';

/**
 * AgentChat — Interactive chat panel for communicating with a running agent.
 *
 * This component provides a chat interface for agent-specific conversations
 * that happen over JSON-RPC (via Electron IPC), separate from the main
 * ChatView which uses HTTP SSE.
 *
 * Features:
 * - Per-agent chat sessions with message history
 * - Quick action buttons for common agent operations
 * - Tool call visualization within agent messages
 * - Streaming indicator while waiting for agent response
 * - Auto-scroll to latest message
 */
export function AgentChat() {
  const session = useAgentChatStore(selectActiveSession);
  const isWaiting = useAgentChatStore(selectIsWaiting);
  const inputText = useAgentChatStore(selectInputText);
  const showAgentChat = useAgentChatStore((s) => s.showAgentChat);
  const activeAgentId = useAgentChatStore((s) => s.activeAgentId);
  const closeChat = useAgentChatStore((s) => s.closeChat);
  const sendMessage = useAgentChatStore((s) => s.sendMessage);
  const executeQuickAction = useAgentChatStore((s) => s.executeQuickAction);
  const setInputText = useAgentChatStore((s) => s.setInputText);
  const clearSession = useAgentChatStore((s) => s.clearSession);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const focusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  // Clean up timers on unmount (matches MessageBubble.tsx pattern)
  useEffect(() => {
    return () => {
      if (focusTimerRef.current) clearTimeout(focusTimerRef.current);
      if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    };
  }, []);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [session?.messages.length]);

  // Focus input when chat opens
  useEffect(() => {
    if (showAgentChat) {
      if (focusTimerRef.current) clearTimeout(focusTimerRef.current);
      focusTimerRef.current = setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [showAgentChat, activeAgentId]);

  // Close on Escape — skip if a higher-priority modal (PermissionPrompt) handled it.
  // Also preventDefault so lower-priority panels (NotificationCenter) don't also close.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && showAgentChat && !e.defaultPrevented) {
        e.preventDefault();
        closeChat();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showAgentChat, closeChat]);

  // Handle sending a message
  const handleSend = useCallback(() => {
    if (!activeAgentId || !inputText.trim() || isWaiting) return;
    sendMessage(activeAgentId, inputText.trim());
  }, [activeAgentId, inputText, isWaiting, sendMessage]);

  // Handle input keydown (Enter to send, Shift+Enter for newline)
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // Handle input changes
  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      if (!activeAgentId) return;
      setInputText(activeAgentId, e.target.value);
    },
    [activeAgentId, setInputText]
  );

  // Handle clear session (double-click confirm, matching MessageBubble pattern)
  const handleClear = useCallback(() => {
    if (!activeAgentId) return;
    if (!showClearConfirm) {
      setShowClearConfirm(true);
      if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
      clearTimerRef.current = setTimeout(() => setShowClearConfirm(false), 3000);
      return;
    }
    setShowClearConfirm(false);
    if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    clearSession(activeAgentId);
  }, [activeAgentId, showClearConfirm, clearSession]);

  if (!showAgentChat || !session) return null;

  return (
    <div className="agent-chat" role="dialog" aria-label={`Chat with ${session.agentName}`}>
      {/* Header */}
      <div className="agent-chat-header">
        <div className="agent-chat-header-left">
          <Bot size={18} className="agent-chat-header-icon" />
          <span className="agent-chat-agent-name">{session.agentName}</span>
        </div>
        <div className="agent-chat-header-actions">
          <button
            className={`btn-icon agent-chat-clear ${showClearConfirm ? 'confirm' : ''}`}
            onClick={handleClear}
            title={showClearConfirm ? 'Click again to confirm' : 'Clear chat'}
            aria-label={showClearConfirm ? 'Confirm clear chat' : 'Clear chat'}
          >
            <Trash2 size={16} />
          </button>
          <button
            className="btn-icon agent-chat-close"
            onClick={closeChat}
            aria-label="Close agent chat"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Quick Actions */}
      {session.quickActions && session.quickActions.length > 0 && (
        <div className="agent-chat-quick-actions">
          {session.quickActions.map((action) => (
            <button
              key={action.method}
              className="agent-chat-quick-btn"
              onClick={() => activeAgentId && executeQuickAction(activeAgentId, action)}
              disabled={isWaiting}
              title={action.label}
            >
              <Zap size={12} />
              {action.label}
            </button>
          ))}
        </div>
      )}

      {/* Messages */}
      <div className="agent-chat-messages">
        {session.messages.length === 0 ? (
          <div className="agent-chat-empty">
            <Bot size={32} strokeWidth={1.2} />
            <span>Send a message to start chatting with {session.agentName}</span>
          </div>
        ) : (
          <>
            {session.messages.map((msg) => (
              <AgentChatBubble key={msg.id} message={msg} />
            ))}
            {isWaiting && (
              <div className="agent-chat-waiting" aria-live="polite">
                <Loader size={14} className="agent-chat-spinner" />
                <span>Waiting for response...</span>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="agent-chat-input-area">
        <textarea
          ref={inputRef}
          className="agent-chat-input"
          value={inputText}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder={`Message ${session.agentName}...`}
          rows={1}
          disabled={isWaiting}
        />
        <button
          className="agent-chat-send"
          onClick={handleSend}
          disabled={!inputText.trim() || isWaiting}
          aria-label="Send message"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}

// ── Message Bubble ───────────────────────────────────────────────────────

interface AgentChatBubbleProps {
  message: AgentChatMessage;
}

/** Detect if agent message content looks like an error (matches MessageBubble pattern). */
function isErrorContent(content: string): boolean {
  if (!content) return false;
  const lower = content.toLowerCase();
  return (
    lower.startsWith('error:') ||
    lower.startsWith('error -') ||
    lower.includes('traceback (most recent') ||
    lower.includes('connection refused') ||
    lower.includes('failed to')
  );
}

function AgentChatBubble({ message }: AgentChatBubbleProps) {
  const isUser = message.role === 'user';
  const isError = !isUser && isErrorContent(message.content);

  return (
    <div className={`agent-msg agent-msg-${message.role} ${isError ? 'agent-msg-error' : ''}`}>
      <div className="agent-msg-avatar">
        {isUser ? 'Y' : <Bot size={14} />}
      </div>
      <div className="agent-msg-body">
        <div className="agent-msg-header">
          <span className="agent-msg-role">{isUser ? 'You' : message.agentId}</span>
          <span className="agent-msg-time">{formatTime(message.timestamp)}</span>
        </div>
        <div className="agent-msg-content">
          {message.content || (message.streaming && <span className="agent-msg-cursor" />)}
        </div>

        {/* Tool calls */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="agent-msg-tools">
            {message.toolCalls.map((tc, i) => (
              <ToolCallBadge key={i} toolCall={tc} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tool Call Badge ──────────────────────────────────────────────────────

function ToolCallBadge({ toolCall }: { toolCall: AgentToolCall }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`agent-tool-call ${expanded ? 'expanded' : ''}`}>
      <button
        className="agent-tool-call-header"
        onClick={() => setExpanded(!expanded)}
      >
        <Wrench size={12} />
        <span className="agent-tool-call-name">{toolCall.tool}</span>
        {toolCall.success === true && <CheckCircle size={12} className="tool-success" />}
        {toolCall.success === false && <XCircle size={12} className="tool-failure" />}
      </button>
      {expanded && (
        <div className="agent-tool-call-detail">
          {Object.keys(toolCall.args).length > 0 && (
            <div className="agent-tool-call-section">
              <span className="agent-tool-call-label">Arguments</span>
              <pre>{JSON.stringify(toolCall.args, null, 2)}</pre>
            </div>
          )}
          {toolCall.resultSummary && (
            <div className="agent-tool-call-section">
              <span className="agent-tool-call-label">Result</span>
              <pre>{toolCall.resultSummary}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatTime(timestamp: number): string {
  const d = new Date(timestamp);
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}
