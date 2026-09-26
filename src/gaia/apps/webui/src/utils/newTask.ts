// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What a "New Task" click should do.
 *
 * Lives outside App.tsx so the decision can be tested without mounting the
 * shell (polling, SSE and all). The rule it protects — a new chat always runs
 * the flagship — is one line away from regressing back into "whatever agent
 * the last session used", which is how it behaved before.
 */

import type { AgentInfo, Session } from '../types';

/**
 * The agent every new chat runs.
 *
 * Deliberately not `DEFAULT_AGENT_ID` from sessionGrouping.ts, which is
 * `chat`. That one answers "which agent owns a session that has no
 * `agent_type`?" and has to keep matching the backend's legacy backfill.
 * This one answers "which agent does a *new* chat get?". Collapsing the two
 * would either reroute existing conversations or stop new ones reaching the
 * flagship.
 */
export const FLAGSHIP_AGENT_ID = 'gaia';

export type NewTaskPlan =
    | { action: 'reuse-draft' }
    | { action: 'create'; agentType: string; model?: string };

export interface NewTaskInputs {
    /** Agent chosen deliberately (Agent Hub, starter card). Absent = flagship. */
    explicitAgentId?: string;
    /** The session being navigated away from, when there is one. */
    outgoingSession?: Session;
    /** Whether that outgoing session is an untouched draft (#2119). */
    outgoingIsDraft: boolean;
    agents: AgentInfo[];
    /** Selected model-size tier, e.g. "full" | "lite" (#1162). */
    modelTier: string;
}

/**
 * Pure planner: resolves the agent, decides whether the outgoing draft can be
 * reused instead of minting another empty row, and pins a model when the
 * selected tier names one.
 */
export function planNewTask(inputs: NewTaskInputs): NewTaskPlan {
    // `||` not `??` — an empty string is a missing choice, not a valid agent id.
    const agentType = inputs.explicitAgentId || FLAGSHIP_AGENT_ID;

    // Only reuse a draft that already runs the agent we're about to ask for;
    // otherwise the reused row would silently keep the wrong agent.
    if (inputs.outgoingIsDraft && inputs.outgoingSession?.agent_type === agentType) {
        return { action: 'reuse-draft' };
    }

    // Only the "lite" tier pins a model; "full" defers to the agent's default.
    const agent = inputs.agents.find((a) => a.id === agentType);
    const tier = agent?.model_tiers?.find((t) => t.name === inputs.modelTier);
    const model = tier?.models?.[0];

    return model ? { action: 'create', agentType, model } : { action: 'create', agentType };
}
