// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { AgentRecovery } from './AgentRecovery';
import type { AgentProblem } from '../types';

interface Props {
    /** Send the failed turn's question again, once the user has picked a fix. */
    onTryAgain: () => void;
}

/**
 * Under a failed turn: when the chosen agent can't start, the same two fixes a
 * new chat offers. Shows nothing when the agent is not the reason.
 */
export function ChatAgentRecovery({ onTryAgain }: Props) {
    const [problem, setProblem] = useState<AgentProblem | null>(null);

    useEffect(() => {
        let live = true;
        api.getStartScreen()
            .then((s) => { if (live) setProblem(s.agent_problem ?? null); })
            .catch((err: unknown) => log.api.warn('Could not check whether the chosen agent can start', err));
        return () => { live = false; };
    }, []);

    if (!problem) return null;
    return (
        <div className="chat-agent-recovery">
            <AgentRecovery problem={problem} onRetry={onTryAgain} />
        </div>
    );
}
