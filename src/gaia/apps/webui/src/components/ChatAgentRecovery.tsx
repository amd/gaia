// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { AgentRecovery } from './AgentRecovery';
import type { AgentProblem } from '../types';

/**
 * Why the chosen agent can't start, asked once per failed turn (`turn`) while
 * `enabled`: undefined while checking, null when it can start.
 */
export function useAgentProblem(enabled: boolean, turn: number): AgentProblem | null | undefined {
    const [problem, setProblem] = useState<AgentProblem | null | undefined>(undefined);

    useEffect(() => {
        if (!enabled) {
            setProblem(undefined);
            return;
        }
        let live = true;
        api.getStartScreen()
            .then((s) => { if (live) setProblem(s.agent_problem ?? null); })
            .catch((err: unknown) => {
                log.api.warn('Could not check whether the chosen agent can start', err);
                if (live) setProblem(null);
            });
        return () => { live = false; };
    }, [enabled, turn]);

    return enabled ? problem : undefined;
}

interface Props {
    problem: AgentProblem;
    /** Send the failed turn's question again, once the user has picked a fix. */
    onTryAgain: () => void;
}

/** In a failed turn whose chosen agent can't start: the same two fixes a new chat offers. */
export function ChatAgentRecovery({ problem, onTryAgain }: Props) {
    return (
        <div className="chat-agent-recovery">
            <AgentRecovery problem={problem} onRetry={onTryAgain} />
        </div>
    );
}
