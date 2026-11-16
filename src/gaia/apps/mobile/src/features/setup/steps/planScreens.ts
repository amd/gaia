// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { SetupStepContext } from '../SetupScreen';
import { GpuScreen } from './GpuScreen';
import { ModelsScreen } from './ModelsScreen';
import { KeyScreen } from './KeyScreen';

export type PlanScreenName = 'models' | 'key' | 'gpu';

/** A screen opened from the plan step, shown in its place; `close` goes back one screen. */
export type PlanScreen = ComponentType<{ ctx: SetupStepContext; close: () => void }>;

/** One line per screen; the plan step offers an opener only for a registered screen. */
export const PLAN_SCREENS: Partial<Record<PlanScreenName, PlanScreen>> = {
    gpu: GpuScreen,
    models: ModelsScreen,
    key: KeyScreen,
};
