// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback } from 'react';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useShallow } from 'zustand/react/shallow';
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
  permissionPromptText,
} from '../stores/notificationStore';
import type { GaiaNotification, NotificationType } from '../types/agent';
import { dateLocale, useT, type Translate } from '../i18n/useT';
import { MarkedText, markLtr } from './MarkedText';
import './NotificationCenter.css';

interface NotificationCenterProps {
  onClose: () => void;
  /** Opens the chat a notification came from, e.g. a scheduled run's chat. */
  onOpenChat?: (sessionId: string) => void;
}

// ── Notification type → icon + color mapping ─────────────────────────────

const NOTIFICATION_META: Record<
  NotificationType,
  { icon: React.ReactNode; colorClass: string }
> = {
  permission_request: {
    icon: <ShieldAlert size={16} />,
    colorClass: 'notif-type-permission',
  },
  policy_alert: {
    icon: <ShieldAlert size={16} />,
    colorClass: 'notif-type-policy',
  },
  security_alert: {
    icon: <AlertTriangle size={16} />,
    colorClass: 'notif-type-security',
  },
  status_change: {
    icon: <CheckCircle size={16} />,
    colorClass: 'notif-type-status',
  },
  info: {
    icon: <Info size={16} />,
    colorClass: 'notif-type-info',
  },
  error: {
    icon: <AlertCircle size={16} />,
    colorClass: 'notif-type-error',
  },
};

// ── Time formatting ──────────────────────────────────────────────────────

function formatNotifTime(timestamp: number, t: Translate): string {
  const now = Date.now();
  const diff = now - timestamp;
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return t('time.justNow');
  const mins = Math.floor(secs / 60);
  if (mins < 60) return t('time.minutesAgo', { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return t('time.hoursAgo', { n: hrs });
  const days = Math.floor(hrs / 24);
  if (days < 7) return t('time.daysAgo', { n: days });
  return new Date(timestamp).toLocaleDateString(dateLocale(t), {
    month: 'short',
    day: 'numeric',
  });
}

// ── Component ────────────────────────────────────────────────────────────

export function NotificationCenter({ onClose, onOpenChat }: NotificationCenterProps) {
  const t = useT();
  // useShallow is required — the selector filters into a fresh array, and an
  // uncached snapshot makes useSyncExternalStore re-render without end.
  const notifications = useNotificationStore(useShallow(selectVisibleNotifications));
  const unreadCount = useNotificationStore(selectUnreadCount);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const clearAll = useNotificationStore((s) => s.clearAll);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const respondToPermission = useNotificationStore((s) => s.respondToPermission);
  const typeFilter = useNotificationStore((s) => s.typeFilter);
  const setTypeFilter = useNotificationStore((s) => s.setTypeFilter);

  // Clicking a notification marks it read and, when it names a chat, opens it.
  const handleClick = useCallback(
    (notif: GaiaNotification) => {
      markRead(notif.id);
      if (notif.sessionId && notif.type !== 'permission_request') onOpenChat?.(notif.sessionId);
    },
    [markRead, onOpenChat]
  );

  // Handle approve for permission_request
  const handleApprove = useCallback(
    (notif: GaiaNotification) => {
      respondToPermission(notif.id, 'allow').catch((err) => console.error('[NotificationCenter] allow failed:', err));
    },
    [respondToPermission]
  );

  // Handle deny for permission_request
  const handleDeny = useCallback(
    (notif: GaiaNotification) => {
      respondToPermission(notif.id, 'deny').catch((err) => console.error('[NotificationCenter] deny failed:', err));
    },
    [respondToPermission]
  );

  useEscapeToClose(true, onClose);

  // Filter type tabs
  const filterTypes: Array<{ key: NotificationType | null; label: string }> = [
    { key: null, label: t('notifications.filter.all') },
    { key: 'permission_request', label: t('notifications.filter.permissions') },
    { key: 'policy_alert', label: t('notifications.filter.policy') },
    { key: 'error', label: t('notifications.filter.errors') },
    { key: 'security_alert', label: t('notifications.filter.security') },
    { key: 'status_change', label: t('notifications.filter.status') },
    { key: 'info', label: t('notifications.filter.info') },
  ];

  return (
    <div className="notification-center" role="dialog" aria-label={t('notifications.dialogAria')}>
      {/* Header */}
      <div className="notification-center-header">
        <div className="notification-center-title-row">
          <h2 className="notification-center-title">{t('notifications.title')}</h2>
          {unreadCount > 0 && (
            <span className="notification-center-badge">{unreadCount}</span>
          )}
        </div>
        <button
          className="btn-icon notification-center-close"
          onClick={onClose}
          aria-label={t('notifications.close')}
        >
          <X size={18} />
        </button>
      </div>

      {/* Filter tabs */}
      <div className="notification-filter-bar" role="tablist" aria-label={t('notifications.filterAria')}>
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
            <span>{t('notifications.empty')}</span>
          </div>
        ) : (
          notifications.map((n) => {
            const meta = NOTIFICATION_META[n.type];
            // A prompt reads as its card does, not as its stored English text.
            const prompt = n.type === 'permission_request' && n.tool ? permissionPromptText(n, t, markLtr) : null;
            return (
              <div
                className={`notification-item ${!n.read ? 'unread' : ''} ${meta.colorClass}`}
                id={n.type === 'policy_alert' && n.receiptId ? `policy-receipt-${encodeURIComponent(n.receiptId)}` : undefined}
                key={n.id}
                onClick={() => handleClick(n)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') handleClick(n);
                }}
              >
                {/* Unread dot */}
                {!n.read && <span className="notification-unread-dot" />}

                {/* Type icon */}
                <span className="notification-icon">{meta.icon}</span>

                {/* Content */}
                <div className="notification-content">
                  <div className="notification-content-header">
                    <span className="notification-agent" dir="auto">{n.agentName}</span>
                    <span className="notification-time">
                      {formatNotifTime(n.timestamp, t)}
                    </span>
                  </div>
                  {prompt ? (
                    <>
                      <span className="notification-title"><MarkedText text={prompt.heading} /></span>
                      <span className="notification-message"><MarkedText text={prompt.question} /></span>
                    </>
                  ) : (
                    <>
                      {n.title && (
                        <span className="notification-title">{n.title}</span>
                      )}
                      <span className="notification-message">{n.message}</span>
                    </>
                  )}

                  {n.type === 'policy_alert' && (
                    <div className="notification-policy-details">
                      {n.tool && (
                        <span className="notification-tool">
                          {t('activity.policy.tool')}: <code dir="ltr">{n.tool}</code>
                        </span>
                      )}
                      {n.reason && (
                        <span className="notification-policy-reason">
                          {t('notifications.reason')}: {n.reason}
                        </span>
                      )}
                      {n.ruleIds && n.ruleIds.length > 0 && (
                        <span className="notification-tool">
                          {t('activity.policy.rules')}: <code dir="ltr">{n.ruleIds.join(', ')}</code>
                        </span>
                      )}
                      {n.policyVersion && (
                        <span className="notification-tool">
                          {t('activity.policy.policy')}: <code dir="ltr">{n.policyVersion}</code>
                        </span>
                      )}
                      <span className="notification-tool">
                        {t('activity.policy.receipt')}:{' '}
                        {n.receiptId ? (
                          <code dir="ltr">{n.receiptId}</code>
                        ) : (
                          <em>{t('activity.policy.receiptUnavailable')}</em>
                        )}
                      </span>
                    </div>
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
                        {t('notifications.approve')}
                      </button>
                      <button
                        className="notification-action-btn notification-action-deny"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeny(n);
                        }}
                      >
                        <X size={14} />
                        {t('notifications.deny')}
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
                      {n.response === 'allow' ? t('notifications.approved') : t('notifications.denied')}
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
                  aria-label={t('notifications.dismiss')}
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
            {t('notifications.markAllRead')}
          </button>
          <button className="notification-footer-btn notification-footer-clear" onClick={clearAll}>
            <Trash2 size={14} />
            {t('notifications.clear')}
          </button>
        </div>
      )}
    </div>
  );
}
