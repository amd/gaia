// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  ShieldAlert,
  Check,
  X,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import { useNotificationStore, selectActivePermissionPrompt } from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import './PermissionPrompt.css';

/**
 * PermissionPrompt — Modal dialog for permission requests from agents.
 *
 * Shown as an overlay when an agent requests permission for a tool invocation.
 * Only one prompt is shown at a time; additional requests are queued in the
 * notification store and displayed sequentially.
 *
 * Features:
 * - Optional countdown timer (from notification.timeoutSeconds)
 * - "Remember this choice" checkbox
 * - Tool name and arguments display
 * - Agent identification
 */
export function PermissionPrompt() {
  const activePrompt = useNotificationStore(selectActivePermissionPrompt);
  const respondToPermission = useNotificationStore((s) => s.respondToPermission);

  if (!activePrompt) return null;

  return (
    <div className="permission-overlay" role="dialog" aria-modal="true" aria-label="Permission Request">
      <PermissionPromptInner
        key={activePrompt.id}
        notification={activePrompt}
        onRespond={respondToPermission}
      />
    </div>
  );
}

// ── Inner prompt (keyed to reset state per prompt) ───────────────────────

interface PromptInnerProps {
  notification: GaiaNotification;
  onRespond: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;
}

function PermissionPromptInner({ notification, onRespond }: PromptInnerProps) {
  const hasTimeout = notification.timeoutSeconds != null && notification.timeoutSeconds > 0;
  const [countdown, setCountdown] = useState<number | null>(
    hasTimeout ? notification.timeoutSeconds! : null
  );
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // State for UI disabled + ref guard for handler (ref avoids recreating useCallback)
  const [isResponding, setIsResponding] = useState(false);
  const [remember, setRemember] = useState(false);
  const isRespondingRef = useRef(false);

  // Stable ref for onRespond to avoid stale closures in timer
  const onRespondRef = useRef(onRespond);
  onRespondRef.current = onRespond;

  // Countdown timer — tick every second, auto-deny handled by separate effect
  useEffect(() => {
    if (!hasTimeout) return;

    timerRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev === null || prev <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [hasTimeout]);

  // Auto-deny when countdown reaches zero (side-effect outside state setter).
  // Uses the same isRespondingRef guard to prevent racing with a manual click.
  useEffect(() => {
    if (countdown === 0) {
      if (isRespondingRef.current) return;
      isRespondingRef.current = true;
      setIsResponding(true);
      onRespondRef.current(notification.id, 'deny', false);
    }
  }, [countdown, notification.id]);

  // Define handlers before they're used in the keyboard effect
  const handleAllow = useCallback(async () => {
    if (isRespondingRef.current) return;
    isRespondingRef.current = true;
    setIsResponding(true);
    if (timerRef.current) clearInterval(timerRef.current);
    try {
      await onRespond(notification.id, 'allow', remember);
    } finally {
      isRespondingRef.current = false;
      setIsResponding(false);
    }
  }, [notification.id, onRespond, remember]);

  const handleDeny = useCallback(async () => {
    if (isRespondingRef.current) return;
    isRespondingRef.current = true;
    setIsResponding(true);
    if (timerRef.current) clearInterval(timerRef.current);
    try {
      await onRespond(notification.id, 'deny', false);
    } finally {
      isRespondingRef.current = false;
      setIsResponding(false);
    }
  }, [notification.id, onRespond]);

  // Handle keyboard shortcuts — preventDefault stops lower-priority Escape handlers
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleAllow();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        handleDeny();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleAllow, handleDeny]);

  // Format tool arguments for display
  const toolArgs = notification.toolArgs;
  const hasArgs = toolArgs && Object.keys(toolArgs).length > 0;

  return (
    <div className="permission-prompt">
      {/* Header */}
      <div className="permission-header">
        <div className="permission-header-icon">
          <ShieldAlert size={24} />
        </div>
        <div className="permission-header-text">
          <h2 className="permission-title">Permission Request</h2>
          <span className="permission-agent">{notification.agentName}</span>
        </div>
        {countdown !== null && countdown > 0 && (
          <div className="permission-countdown" title="Auto-deny on timeout">
            <Clock size={14} />
            <span>{countdown}s</span>
          </div>
        )}
      </div>

      {/* Body */}
      <div className="permission-body">
        <p className="permission-message">{notification.message}</p>

        {/* Tool info */}
        {notification.tool && (
          <div className="permission-tool-info">
            <div className="permission-tool-header">
              <AlertTriangle size={14} />
              <span>Tool Invocation</span>
            </div>
            <div className="permission-tool-name">
              <code>{notification.tool}</code>
            </div>
            {hasArgs && (
              <div className="permission-tool-args">
                <pre>{JSON.stringify(toolArgs, null, 2)}</pre>
              </div>
            )}
          </div>
        )}

        {/* Priority indicator */}
        {notification.priority === 'critical' && (
          <div className="permission-critical-banner">
            <AlertTriangle size={14} />
            <span>This is a critical-tier operation</span>
          </div>
        )}

        {/* Remember choice */}
        <label className="permission-remember">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            disabled={isResponding}
          />
          <span>Always allow this tool</span>
        </label>
      </div>

      {/* Actions */}
      <div className="permission-actions">
        <button
          className="permission-btn permission-btn-deny"
          onClick={handleDeny}
          disabled={isResponding}
          title="Deny (Esc)"
        >
          <X size={16} />
          Deny
        </button>
        <button
          className="permission-btn permission-btn-allow"
          onClick={handleAllow}
          disabled={isResponding}
          title="Allow (Enter)"
        >
          <Check size={16} />
          Allow
        </button>
      </div>

      {/* Keyboard hints */}
      <div className="permission-hints">
        <span><kbd>Enter</kbd> Allow</span>
        <span><kbd>Esc</kbd> Deny</span>
      </div>
    </div>
  );
}
