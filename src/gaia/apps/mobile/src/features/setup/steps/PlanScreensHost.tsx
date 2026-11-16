// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';
import { View } from 'react-native';

import { useT } from '@/i18n/useT';

import { SetupFooterMuted } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { lowDiskRequest } from '../sheets/setupConfirm';
import { useConfirmSheet } from '../sheets/ConfirmSheet';
import { PlanOpenersContext, type PlanOpeners } from './planOpeners';
import { usePlanScreenRequest } from './planScreenRequest';
import { PLAN_SCREENS, type PlanScreenName } from './planScreens';
import { PlanStep } from './PlanStep';

/** The plan step and the screens it opens. The plan stays mounted under them, so its choices survive a visit. */
export function PlanWithScreens({ ctx }: { ctx: SetupStepContext }) {
    const t = useT();
    const [asked] = useState(() => usePlanScreenRequest.getState().pending);
    const [stack, setStack] = useState<PlanScreenName[]>(() => (asked && PLAN_SCREENS[asked.screen] ? [asked.screen] : []));
    useEffect(() => usePlanScreenRequest.getState().clear(), []);
    const { ask, sheet } = useConfirmSheet();

    const openers = useMemo<PlanOpeners>(() => {
        const opener = (name: PlanScreenName) =>
            PLAN_SCREENS[name] ? () => setStack((s) => (s[s.length - 1] === name ? s : [...s, name])) : undefined;
        return {
            openModels: opener('models'),
            openKey: opener('key'),
            openGpu: opener('gpu'),
            confirmLowDisk: (short, proceed) => {
                void ask(lowDiskRequest(short, t)).then((ok) => {
                    if (ok) proceed(true);
                });
            },
        };
    }, [ask, t]);

    const top: PlanScreenName | undefined = stack[stack.length - 1];
    const Screen = top ? PLAN_SCREENS[top] : undefined;
    // A screen another step asked for goes back to that step when it closes.
    const close = () => {
        if (asked && stack.length === 1 && stack[0] === asked.screen) ctx.goTo(asked.from);
        else setStack((s) => s.slice(0, -1));
    };

    return (
        <PlanOpenersContext.Provider value={openers}>
            <View style={Screen ? { display: 'none' } : undefined}>
                <SetupFooterMuted muted={Screen != null}>
                    <PlanStep ctx={ctx} />
                </SetupFooterMuted>
            </View>
            {Screen ? <Screen key={top} ctx={ctx} close={close} /> : null}
            {sheet}
        </PlanOpenersContext.Provider>
    );
}
