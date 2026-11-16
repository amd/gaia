// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Written by hand to the /api/hybrid contract; move to api.gen.ts once the
// backend's pydantic models land and `npm run gen:api-types` can emit them.

/** What kind of work the classifier says a request is. */
export type HybridKind =
    | 'write_prose'
    | 'answer_question'
    | 'analyze_data'
    | 'operate_tools'
    | 'change_code'
    | 'find_defects'
    | 'plan_or_design'
    | 'other';

export type HybridPolicy = 'quality' | 'balanced' | 'custom';

/** When a request of one kind may run on the cheap model. */
export interface HybridRule {
    /** Highest difficulty (1-5) the cheap model takes; 0 = never. */
    cheap_max_difficulty: number;
    /** Whether a high-stakes request may still run on the cheap model. */
    allow_high_stakes: boolean;
}

export type HybridRules = Record<HybridKind, HybridRule>;

/** A gateway model the hybrid router can use, with the gateway's price per million tokens. */
export interface HybridModelOption {
    id: string;
    display_name: string;
    input_per_million: number | null;
    output_per_million: number | null;
    cache_read_per_million: number | null;
    available: boolean;
}

/** GET /api/hybrid/settings, also returned by PUT. */
export interface HybridSettings {
    cheap_model: string;
    strong_model: string;
    /** Null when the cheap model classifies too. */
    classifier_model: string | null;
    classifier_timeout: number;
    policy: HybridPolicy;
    /** The table in effect: the preset expanded, or the custom rules. */
    rules: HybridRules;
    presets: { quality: HybridRules; balanced: HybridRules };
    kinds: HybridKind[];
    models: HybridModelOption[];
    /** The default model is `hybrid`. */
    is_active: boolean;
}

/** PUT /api/hybrid/settings: only the fields to change. */
export type HybridSettingsUpdate = Partial<
    Pick<HybridSettings, 'cheap_model' | 'strong_model' | 'classifier_model' | 'classifier_timeout' | 'policy' | 'rules'>
>;

/** POST /api/hybrid/preview: the decision a request would get, without running a turn. */
export interface HybridPreview {
    model: string;
    kind: HybridKind | null;
    difficulty: number | null;
    stakes: 'low' | 'high' | null;
    reason: string;
    /** False when the classifier failed and the strong model was picked. */
    classified: boolean;
    seconds: number;
    policy: HybridPolicy;
}

/** GET /api/hybrid/stats: routed turns and what they cost against all-strong. */
export interface HybridStats {
    since: string | null;
    turns: number;
    cheap_turns: number;
    strong_turns: number;
    /** Turns the classifier couldn't label, so the strong model ran. */
    failed_closed: number;
    cost: number;
    cost_if_all_strong: number;
    /** Null when there is nothing to compare yet. */
    saving_pct: number | null;
}
