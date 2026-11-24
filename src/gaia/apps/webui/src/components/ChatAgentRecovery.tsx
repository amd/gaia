// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, type ReactNode } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { AgentRecovery } from './AgentRecovery';
import type { AgentProblem } from '../types';

interface Props {
    /** Send the failed turn's question again, once the user has picked a fix. */
    onTryAgain: () => void;
    /** Shown instead once the check finds the agent can start. */
    otherwise?: ReactNode;
}

/**
 * Under a failed turn: when the chosen agent can't start, the same two fixes a
 * new chat offers. Otherwise shows `otherwise`, or nothing.
 */
export function ChatAgentRecovery({ onTryAgain, otherwise = null }: Props) {
    const [problem, setProblem] = useState<AgentProblem | null | undefined>(undefined);

    useEffect(() => {
        let live = true;
        api.getStartScreen()
            .then((s) => { if (live) setProblem(s.agent_problem ?? null); })
            .catch((err: unknown) => {
                log.api.warn('Could not check whether the chosen agent can start', err);
                if (live) setProblem(null);
            });
        return () => { live = false; };
    }, []);

    if (problem === undefined) return null;
    if (problem === null) return <>{otherwise}</>;
    return (
        <div className="chat-agent-recovery">
            <AgentRecovery problem={problem} onRetry={onTryAgain} />
        </div>
    );
}
