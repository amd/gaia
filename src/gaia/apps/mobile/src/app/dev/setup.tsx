// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Redirect, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';

import { GaiaClientProvider, useSetupPlan, useSetupStatus } from '@/data';
import { SETUP_SCENES, setupSceneClient } from '@/features/setup/devScenes';
import { devGalleryEnabled } from '@/features/dev/devGallery';
import { SetupFooterBar, SetupFooterProvider } from '@/features/setup/SetupFooter';
import type { SetupStage } from '@/features/setup/flow';
import type { SetupStepContext } from '@/features/setup/SetupScreen';
import { usePlanScreenRequest } from '@/features/setup/steps/planScreenRequest';
import { SETUP_STEPS } from '@/features/setup/steps';
import { Screen, ScreenBannerProvider, Stack, Text, UIProvider } from '@/ui';

const noop = () => undefined;

function SceneStep({ stage }: { stage: SetupStage }) {
    const run = useSetupStatus().data ?? null;
    const plan = useSetupPlan().data;
    const Step = SETUP_STEPS[stage];
    if (!Step) throw new Error(`Setup has no body registered for the ${stage} step.`);
    const ctx: SetupStepContext = { stage, run, plan, locked: false, again: false, goTo: noop, startedRun: noop, finish: noop };
    return <Step ctx={ctx} />;
}

/** One setup step on a fake PC; `?step=check|plan|gpu|models|key|download|test|ready&state=<name>` (see SETUP_SCENES), `default` when no state is given. */
export default function SetupGalleryRoute() {
    const { step = 'check', state = 'default' } = useLocalSearchParams<{ step?: string; state?: string }>();
    const found = SETUP_SCENES[`${step}/${state}`];
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    const [client] = useState(() => {
        if (!found) return null;
        // The plan step opens this screen on arrival, as it does when another step asks for it.
        if (found.screen) usePlanScreenRequest.getState().request({ screen: found.screen, from: 'check' });
        return setupSceneClient(found);
    });
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    if (!found || !client) {
        return (
            <UIProvider>
                <Screen scroll>
                    <Stack gap="sm" padding="lg">
                        <Text role="title">{`No setup scene "${step}/${state}"`}</Text>
                        {Object.keys(SETUP_SCENES).map((key) => <Text key={key}>{key}</Text>)}
                    </Stack>
                </Screen>
            </UIProvider>
        );
    }
    return (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <UIProvider>
                    <ScreenBannerProvider banner={null}>
                        <SetupFooterProvider>
                            <Screen scroll footer={<SetupFooterBar />}>
                                <Stack gap="lg" padding="lg">
                                    <SceneStep stage={found.stage} />
                                </Stack>
                            </Screen>
                        </SetupFooterProvider>
                    </ScreenBannerProvider>
                </UIProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
}
