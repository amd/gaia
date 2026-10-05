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
import { useNotificationStore, selectActivePermissionPrompt, requiresFreshConsent } from '../stores/notificationStore';
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
 * - Enter never approves: focus moves to the dialog, so a keystroke meant for
 *   the composer can't answer it. Esc denies; Tab reaches the buttons.
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

/** `45` -> "45s", `600` -> "10:00". */
export function formatCountdown(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function PermissionPromptInner({ notification, onRespond }: PromptInnerProps) {
  const hasTimeout = notification.timeoutSeconds != null && notification.timeoutSeconds > 0;
  // A deadline, not a tick count: background tabs throttle timers, and the
  // backend denies at its own deadline regardless of how often we ticked.
  const deadlineRef = useRef<number | null>(
    hasTimeout ? Date.now() + notification.timeoutSeconds! * 1000 : null
  );
  const [countdown, setCountdown] = useState<number | null>(
    hasTimeout ? notification.timeoutSeconds! : null
  );
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const promptRef = useRef<HTMLDivElement>(null);

  // State for UI disabled + ref guard for handler (ref avoids recreating useCallback)
  const [isResponding, setIsResponding] = useState(false);
  const [remember, setRemember] = useState(false);
  const freshConsent = requiresFreshConsent(notification.tool);
  const isRespondingRef = useRef(false);

  // Stable ref for onRespond to avoid stale closures in timer
  const onRespondRef = useRef(onRespond);
  onRespondRef.current = onRespond;

  // Countdown timer — tick every second, auto-deny handled by separate effect
  useEffect(() => {
    if (!hasTimeout) return;

    timerRef.current = setInterval(() => {
      const left = Math.max(0, Math.ceil((deadlineRef.current! - Date.now()) / 1000));
      if (left === 0 && timerRef.current) clearInterval(timerRef.current);
      setCountdown(left);
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
      await onRespond(notification.id, 'allow', freshConsent ? false : remember);
    } finally {
      isRespondingRef.current = false;
      setIsResponding(false);
    }
  }, [notification.id, onRespond, remember, freshConsent]);

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

  // Take focus from the composer so its Enter can't land on this prompt.
  useEffect(() => {
    promptRef.current?.focus({ preventScroll: true });
  }, []);

  // Esc denies. Enter is deliberately unbound — only a focused button approves.
  // preventDefault stops lower-priority Escape handlers.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        handleDeny();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleDeny]);

  // Format tool arguments for display
  const toolArgs = notification.toolArgs;
  const hasArgs = toolArgs && Object.keys(toolArgs).length > 0;

  return (
    <div className="permission-prompt" ref={promptRef} tabIndex={-1}>
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
            <span>{formatCountdown(countdown)}</span>
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
        {!freshConsent && <label className="permission-remember">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            disabled={isResponding}
          />
          <span>
            Allow this tool for the rest of this chat
            <small className="permission-remember-hint">
              Only in this chat, until you reload or restart GAIA. Revoke any time in Settings → Tools &amp; Permissions.
            </small>
          </span>
        </label>}
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
          title="Allow"
        >
          <Check size={16} />
          Allow
        </button>
      </div>

      {/* Keyboard hints */}
      <div className="permission-hints">
        <span><kbd>Tab</kbd> Choose</span>
        <span><kbd>Esc</kbd> Deny</span>
      </div>
    </div>
  );
}
