// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { GoalPriority } from '@/api/client.types';
import type { MessageKey } from '@webui/i18n/registry';
import type { BadgeTone, StatusTone } from '@/ui';

export const STATUS_KEYS: Record<string, MessageKey> = {
    pending_approval: 'mem.status.pending_approval',
    queued: 'mem.status.queued',
    in_progress: 'mem.status.in_progress',
    completed: 'mem.status.completed',
    failed: 'mem.status.failed',
    rejected: 'mem.status.rejected',
    cancelled: 'mem.status.cancelled',
    blocked: 'mem.status.blocked',
};

export const PRIORITY_KEYS: Record<GoalPriority, MessageKey> = {
    low: 'mem.priority.low',
    medium: 'mem.priority.medium',
    high: 'mem.priority.high',
};

/** The three priorities as the new-goal form words them, in the web's order. */
export const PRIORITY_CHOICES: { value: GoalPriority; label: MessageKey }[] = [
    { value: 'low', label: 'mem.goals.priorityLow' },
    { value: 'medium', label: 'mem.goals.priorityMedium' },
    { value: 'high', label: 'mem.goals.priorityHigh' },
];

export const SOURCE_KEYS: Record<string, MessageKey> = {
    agent_inferred: 'mem.goals.source.agent_inferred',
    agent_scheduled: 'mem.goals.source.agent_scheduled',
};

/** The web's header pills, in its order. */
export const STAT_STATUSES = ['queued', 'in_progress', 'completed', 'failed'] as const;

export const STAT_TONES: Record<(typeof STAT_STATUSES)[number], BadgeTone> = {
    queued: 'accent',
    in_progress: 'warning',
    completed: 'success',
    failed: 'danger',
};

export const PRIORITY_TONES: Record<GoalPriority, BadgeTone> = { low: 'neutral', medium: 'warning', high: 'danger' };

export function statusTone(status: string): StatusTone {
    switch (status) {
        case 'in_progress':
            return 'running';
        case 'completed':
            return 'success';
        case 'failed':
            return 'error';
        case 'blocked':
            return 'attention';
        default:
            return 'idle';
    }
}
