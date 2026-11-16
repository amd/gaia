// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { SetupStage } from '../flow';
import type { SetupStepContext } from '../SetupScreen';
import { CheckStep } from './CheckStep';
import { DownloadStep } from './DownloadStep';
import { PlanWithScreens } from './PlanScreensHost';
import { ReadyStep } from './ReadyStep';
import { TestStep } from './TestStep';

/** A setup step's body. Rendered as a component, so each step keeps its own hooks. */
export type SetupStep = ComponentType<{ ctx: SetupStepContext }>;

/** Each stage's body, one line per step screen; a stage with none shows only its title. */
export const SETUP_STEPS: Partial<Record<SetupStage, SetupStep>> = {
    check: CheckStep,
    plan: PlanWithScreens,
    download: DownloadStep,
    test: TestStep,
    ready: ReadyStep,
};
