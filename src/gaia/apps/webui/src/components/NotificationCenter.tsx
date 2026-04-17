// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback, useRef } from 'react';
import {
  Bell,
  X,
  ShieldAlert,
  AlertTriangle,
  CheckCircle,
  Info,
  AlertCircle,
  Check,
  Trash2,
} from 'lucide-react';
import {
  useNotificationStore,
  selectUnreadCount,
  selectVisibleNotifications,
} from '../stores/notificationStore';
import type { GaiaNotification, NotificationType } from '../types/agent';
import './NotificationCenter.css';

interface NotificationCenterProps {
  onClose: () => void;
}

// ── Notification type → icon + color mapping ─────────────────────────────

const NOTIFICATION_META: Record<
  NotificationType,
  { icon: React.ReactNode; colorClass: string; label: string }
> = {
  permission_request: {
    icon: <ShieldAlert size={16} />,
    colorClass: 'notif-type-permission',
    label: 'Permission',
  },
  security_alert: {
    icon: <AlertTriangle size={16} />,
    colorClass: 'notif-type-security',
    label: 'Security',
  },
  status_change: {
    icon: <CheckCircle size={16} />,
    colorClass: 'notif-type-status',
    label: 'Status',
  },
  info: {
    icon: <Info size={16} />,
    colorClass: 'notif-type-info',
    label: 'Info',
  },
  error: {
    icon: <AlertCircle size={16} />,
    colorClass: 'notif-type-error',
    label: 'Error',
  },
};

// ── Time formatting ──────────────────────────────────────────────────────

function formatNotifTime(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp;
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(timestamp).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
}

// ── Component ────────────────────────────────────────────────────────────

export function NotificationCenter({ onClose }: NotificationCenterProps) {
  const notifications = useNotificationStore(selectVisibleNotifications);
  const unreadCount = useNotificationStore(selectUnreadCount);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const clearAll = useNotificationStore((s) => s.clearAll);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const addNotification = useNotificationStore((s) => s.addNotification);
  const respondToPermission = useNotificationStore((s) => s.respondToPermission);
  const typeFilter = useNotificationStore((s) => s.typeFilter);
  const setTypeFilter = useNotificationStore((s) => s.setTypeFilter);

  // Listen for IPC notifications from Electron.
  // Use a ref guard to ensure registration happens only once — Electron IPC
  // listeners don't return an unsubscribe handle, so re-running would leak.
  const ipcRegisteredRef = useRef(false);
  useEffect(() => {
    const api = window.gaiaAPI;
    if (!api?.notification?.onNotification || ipcRegisteredRef.current) return;
    ipcRegisteredRef.current = true;

    api.notification.onNotification((data: GaiaNotification) => {
      addNotification(data);
    });
  }, [addNotification]);

  // Handle clicking a notification item (mark as read)
  const handleClick = useCallback(
    (id: string) => {
      markRead(id);
    },
    [markRead]
  );

  // Handle approve for permission_request
  const handleApprove = useCallback(
    (notif: GaiaNotification) => {
      respondToPermission(notif.id, 'allow', false);
    },
    [respondToPermission]
  );

  // Handle deny for permission_request
  const handleDeny = useCallback(
    (notif: GaiaNotification) => {
      respondToPermission(notif.id, 'deny', false);
    },
    [respondToPermission]
  );

  // Close on Escape — skip if a higher-priority modal already handled it
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Filter type tabs
  const filterTypes: Array<{ key: NotificationType | null; label: string }> = [
    { key: null, label: 'All' },
    { key: 'permission_request', label: 'Permissions' },
    { key: 'error', label: 'Errors' },
    { key: 'security_alert', label: 'Security' },
    { key: 'status_change', label: 'Status' },
    { key: 'info', label: 'Info' },
  ];

  return (
    <div className="notification-center" role="dialog" aria-label="Notification Center">
      {/* Header */}
      <div className="notification-center-header">
        <div className="notification-center-title-row">
          <h2 className="notification-center-title">Notifications</h2>
          {unreadCount > 0 && (
            <span className="notification-center-badge">{unreadCount}</span>
          )}
        </div>
        <button
          className="btn-icon notification-center-close"
          onClick={onClose}
          aria-label="Close notification center"
        >
          <X size={18} />
        </button>
      </div>

      {/* Filter tabs */}
      <div className="notification-filter-bar" role="tablist" aria-label="Filter notifications">
        {filterTypes.map((ft) => (
          <button
            key={ft.key ?? 'all'}
            role="tab"
            aria-selected={typeFilter === ft.key}
            className={`notification-filter-tab ${typeFilter === ft.key ? 'active' : ''}`}
            onClick={() => setTypeFilter(ft.key)}
          >
            {ft.label}
          </button>
        ))}
      </div>

      {/* Notification list */}
      <div className="notification-list">
        {notifications.length === 0 ? (
          <div className="notification-empty">
            <Bell size={40} strokeWidth={1.2} />
            <span>No notifications</span>
          </div>
        ) : (
          notifications.map((n) => {
            const meta = NOTIFICATION_META[n.type];
            return (
              <div
                className={`notification-item ${!n.read ? 'unread' : ''} ${meta.colorClass}`}
                key={n.id}
                onClick={() => handleClick(n.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') handleClick(n.id);
                }}
              >
                {/* Unread dot */}
                {!n.read && <span className="notification-unread-dot" />}

                {/* Type icon */}
                <span className="notification-icon">{meta.icon}</span>

                {/* Content */}
                <div className="notification-content">
                  <div className="notification-content-header">
                    <span className="notification-agent">{n.agentName}</span>
                    <span className="notification-time">
                      {formatNotifTime(n.timestamp)}
                    </span>
                  </div>
                  {n.title && (
                    <span className="notification-title">{n.title}</span>
                  )}
                  <span className="notification-message">{n.message}</span>

                  {/* Tool info for permission requests */}
                  {n.type === 'permission_request' && n.tool && (
                    <span className="notification-tool">
                      Tool: <code>{n.tool}</code>
                    </span>
                  )}

                  {/* Permission actions */}
                  {n.type === 'permission_request' && !n.response && (
                    <div className="notification-actions">
                      <button
                        className="notification-action-btn notification-action-allow"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleApprove(n);
                        }}
                      >
                        <Check size={14} />
                        Approve
                      </button>
                      <button
                        className="notification-action-btn notification-action-deny"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeny(n);
                        }}
                      >
                        <X size={14} />
                        Deny
                      </button>
                    </div>
                  )}

                  {/* Permission response badge */}
                  {n.type === 'permission_request' && n.response && (
                    <span
                      className={`notification-response-badge ${
                        n.response === 'allow' ? 'response-allow' : 'response-deny'
                      }`}
                    >
                      {n.response === 'allow' ? 'Approved' : 'Denied'}
                    </span>
                  )}
                </div>

                {/* Dismiss button */}
                <button
                  className="notification-dismiss"
                  onClick={(e) => {
                    e.stopPropagation();
                    dismiss(n.id);
                  }}
                  aria-label="Dismiss notification"
                >
                  <X size={14} />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Footer */}
      {notifications.length > 0 && (
        <div className="notification-center-footer">
          <button
            className="notification-footer-btn"
            onClick={markAllRead}
            disabled={unreadCount === 0}
          >
            <Check size={14} />
            Mark All Read
          </button>
          <button className="notification-footer-btn notification-footer-clear" onClick={clearAll}>
            <Trash2 size={14} />
            Clear
          </button>
        </div>
      )}
    </div>
  );
}
