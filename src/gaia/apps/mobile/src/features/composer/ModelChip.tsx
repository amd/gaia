// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

import { useActiveModel, useLocalModelStatus, useSelectModel } from '@/data';
import { pillText } from '@/features/model/modelStatusText';
import { useT } from '@/i18n/useT';
import { Cloud, Cpu, Download, TriangleAlert } from '@/ui/icons';

import { ControlChip } from './ControlChip';
import { ModelMenu } from './ModelMenu';

/** The model the next message runs on; tap to switch, download or see what only the PC can change. */
export function ModelChip({ turnRunning = false, initialOpen = false, initialInfoRow, forceSwitching }: { turnRunning?: boolean; initialOpen?: boolean; initialInfoRow?: string; forceSwitching?: string }) {
    const t = useT();
    const [open, setOpen] = useState(initialOpen);
    // A model picked while a reply runs is used from the next message.
    const [queued, setQueued] = useState<string | null>(null);
    const { data } = useLocalModelStatus(open);
    const [switchError, setSwitchError] = useState<string | null>(null);
    const active = useActiveModel().data;
    const select = useSelectModel();
    const { mutate } = select;
    useEffect(() => {
        if (turnRunning || !queued) return;
        mutate(queued, { onSettled: () => setQueued(null) });
    }, [turnRunning, queued, mutate]);
    const pill = pillText(data, t);
    const label = pill.name ?? t.bare('modelStatus.fallbackName');
    const notDownloaded = active?.downloaded === false;
    const warning = notDownloaded || !!switchError;
    const hint = switchError
        ? switchError
        : queued
          ? t('modelMenu.usedNext', { name: queued })
          : active?.config_error
            ? active.config_error
            : notDownloaded
              ? t('modelMenu.notDownloadedTitle', { name: label })
              : pill.remote
                ? undefined
                : t('modelStatus.runsHere');
    return (
        <>
            <ControlChip
                label={queued ? `${label} → ${queued}` : label}
                icon={switchError ? TriangleAlert : notDownloaded ? Download : pill.remote ? Cloud : Cpu}
                warning={warning}
                accessibilityHint={hint}
                menu
                chip="model"
                onPress={() => setOpen(true)}
                accessibilityLabel={pill.aria}
                testID="model-chip"
            />
            <ModelMenu
                open={open}
                onClose={() => setOpen(false)}
                turnRunning={turnRunning}
                queued={queued}
                onQueue={setQueued}
                initialInfoRow={initialInfoRow}
                forceSwitching={forceSwitching}
                switchError={switchError}
                onSwitchError={setSwitchError}
            />
        </>
    );
}
