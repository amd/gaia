// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getT } from '../i18n/useT';
import type { StreamEvent, AgentStep } from '../types';
import { agentEventToStep as buildStep, statusText as buildStatusText } from './streamSteps.core';

export { isProgressTick } from './streamSteps.core';

/** A status event's line for the user; the tool-list re-read is said in their language. */
export function statusText(event: StreamEvent): string {
    return buildStatusText(event, getT());
}

/** Map an SSE agent event to an AgentStep for the UI. */
export function agentEventToStep(event: StreamEvent, stepIdRef: { current: number }): AgentStep | null {
    return buildStep(event, stepIdRef, getT());
}
