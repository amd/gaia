// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import type { PermissionMode } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { ActionSheet } from '@/ui';
import { Check, ShieldAlert, ShieldCheck } from '@/ui/icons';

import { ControlChip } from './ControlChip';

export interface PermissionChipProps {
    mode: PermissionMode;
    onChange: (mode: PermissionMode) => void;
    disabled?: boolean;
}

/** The chat's permission mode, the TUI's ask or full access. Full access asks once more before it turns on. */
export function PermissionChip({ mode, onChange, disabled }: PermissionChipProps) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const full = mode === 'full_access';
    const modes = [
        { mode: 'ask' as const, label: t('perm.ask'), note: t('perm.askNote') },
        { mode: 'full_access' as const, label: t('perm.full'), note: t('perm.fullNote') },
    ];
    return (
        <>
            <ControlChip
                label={t.bare(full ? 'perm.full' : 'env.ask')}
                icon={full ? ShieldAlert : ShieldCheck}
                chip="permission"
                menu
                warning={full}
                disabled={disabled}
                onPress={() => setOpen(true)}
                accessibilityLabel={t('perm.chipAria', { mode: t.bare(full ? 'perm.fullLower' : 'perm.askLower') })}
                testID="permission-chip"
            />
            <ActionSheet
                open={open}
                onClose={() => setOpen(false)}
                title={t('env.permission')}
                actions={modes.map((m) => ({
                    label: `${m.mode === mode ? '✓ ' : ''}${m.label}`,
                    icon: m.mode === mode ? Check : undefined,
                    onPress: () => {
                        if (m.mode === 'full_access' && !full) setConfirming(true);
                        else onChange(m.mode);
                    },
                }))}
            />
            <ActionSheet
                open={confirming}
                onClose={() => setConfirming(false)}
                title={t('env.fullAccessWarn')}
                actions={[{ label: t('env.turnOn'), onPress: () => onChange('full_access') }]}
            />
        </>
    );
}
