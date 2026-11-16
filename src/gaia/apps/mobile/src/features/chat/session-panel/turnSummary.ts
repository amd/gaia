// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message } from '@/api/client.types';
import type { MessageKey, Params } from '@webui/i18n/registry';
import type { TurnSummary } from '@webui/types';
import { shortModelName } from '@webui/utils/modelList.core';

/** What "This answer" shows: the latest answer's summary, a turn in progress, or nothing. */
export type AnswerState = { kind: 'turn'; summary: TurnSummary | null; stopped: boolean } | { kind: 'busy' } | null;

export interface Say {
    key: MessageKey;
    params?: Params;
}

export interface TurnFacts {
    model: Say;
    modelName: string | null;
    documents: Say | null;
    websites: string[];
    cost: Say;
}

/** The server's `turn_summary` rides on a saved message; a finished stream may already carry it as `turnSummary`. */
export function turnSummaryOf(message: Message): TurnSummary | undefined {
    return message.turnSummary ?? (message as { turn_summary?: TurnSummary | null }).turn_summary ?? undefined;
}

export function answerState(messages: readonly Message[], busy: boolean): AnswerState {
    // A running turn never shows the previous answer's data as its own.
    if (busy) return messages.some((m) => turnSummaryOf(m)) ? { kind: 'busy' } : null;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant') return null;
    const summary = turnSummaryOf(last);
    if (!summary) return null;
    const stopped = !!(last.stopState ?? (last as { stopped?: boolean }).stopped);
    return { kind: 'turn', summary, stopped };
}

/** Every internet tool this turn failed, so the answer has no live data. */
export function liveDataFailed(summary: TurnSummary): boolean {
    const attempted = summary.live_data?.attempted ?? 0;
    return attempted > 0 && (summary.live_data?.failed ?? 0) >= attempted;
}

export function hostOf(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, '');
    } catch {
        return url;
    }
}

function costSay(summary: TurnSummary, stopped: boolean): Say {
    const { cost } = summary;
    if (cost.kind === 'local') return { key: 'rail.costLocal' };
    if (cost.kind === 'unreported') return { key: stopped ? 'rail.costUnreportedStopped' : 'rail.costUnreported' };
    if (cost.kind === 'estimate' && typeof cost.usd === 'number') {
        // The "$" rides inside the value so the bidi isolate keeps it left of the digits in Arabic.
        return { key: 'rail.costAbout', params: { usd: `$${cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2)}` } };
    }
    return { key: summary.model ? 'rail.costUnknownModel' : 'rail.costUnknown' };
}

/** Where the turn's data went, in the phone's words: your PC, a cloud provider, the web. */
export function turnFacts(summary: TurnSummary, stopped: boolean): TurnFacts {
    const { model } = summary;
    const usedDocs = (summary.documents ?? []).length > 0;
    return {
        model: !model
            ? { key: 'rail.notReported' }
            : model.remote
              ? { key: 'rail.cloudModel', params: { name: model.provider_name } }
              : { key: 'rail.thisPc' },
        modelName: model?.name ? shortModelName(model.name) : null,
        documents: !usedDocs
            ? null
            : model?.remote
              ? { key: 'rail.docsSent', params: { provider: model.provider_name } }
              : { key: model ? 'rail.docsLocal' : 'rail.docsSearched' },
        websites: summary.sites ?? [],
        cost: costSay(summary, stopped),
    };
}
