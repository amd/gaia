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

/** Where a hybrid member model runs. `claude` is Anthropic's API, used directly. */
export type HybridProvider = 'local' | 'amd' | 'fireworks' | 'claude';

/** A model a set-up provider offers (or a configured one none offers), grouped by `provider`. */
export interface HybridModelOption {
    id: string;
    display_name: string;
    provider: HybridProvider;
    /** Per million tokens; null when unknown, never guessed. */
    input_per_million: number | null;
    output_per_million: number | null;
    cache_read_per_million: number | null;
    price_source: string | null;
    available: boolean;
    /** Why it can't be used; null when `available`. */
    reason: string | null;
}

/** The notes the apps show for a pairing (settings and each template). */
export interface HybridNotes {
    /** True only for the pairing the shipped policies were tuned on. */
    measured: boolean;
    /** Null when `measured`. */
    measured_note: string | null;
    /** Set when the classifier runs on this PC. */
    classifier_note: string | null;
}

/** GET /api/hybrid/entry (and `entry` in the settings): hybrid's own model-picker choice. */
export interface HybridEntry {
    id: 'hybrid';
    /** "Hybrid (<cheap> + <strong>)", or "Hybrid" when the settings can't be read. */
    display_name: string;
    /** True when any member is a cloud model; null when the settings can't be read. */
    remote: boolean | null;
    /** The configured pair can run now. */
    available: boolean;
    /** Why it can't; null when `available`. */
    reason: string | null;
    /** Offer it: the pair or any template can run here. */
    shown: boolean;
}

/** GET /api/hybrid/settings, also returned by PUT and by applying a template. */
export interface HybridSettings extends HybridNotes {
    cheap_model: string;
    strong_model: string;
    classifier_model: string;
    classifier_timeout: number;
    policy: HybridPolicy;
    /** The table in effect: the preset expanded, or the custom rules (null when an invalid custom config has none). */
    rules: HybridRules | null;
    presets: { quality: HybridRules; balanced: HybridRules };
    kinds: HybridKind[];
    models: HybridModelOption[];
    /** Why the model list couldn't be read; `models` is then empty. */
    models_error: string | null;
    prices_fetched_at: string | null;
    /** Why the gateway price list couldn't be refreshed. */
    prices_error: string | null;
    /** Null when the model list couldn't be read. */
    entry: HybridEntry | null;
    /** The default model is `hybrid`. */
    is_active: boolean;
    /** Why the saved settings can't be used; the values are shown as saved so they can be fixed. */
    config_error: string | null;
}

/** PUT /api/hybrid/settings: only the fields to change. */
export interface HybridSettingsUpdate {
    cheap_model?: string;
    strong_model?: string;
    classifier_model?: string;
    classifier_timeout?: number;
    policy?: HybridPolicy;
    rules?: HybridRules | null;
}

/** GET /api/hybrid/templates: a ready-made pairing, resolved on this PC. */
export interface HybridTemplate extends HybridNotes {
    id: string;
    name: string;
    description: string;
    cheap_model: string;
    strong_model: string;
    classifier_model: string;
    policy: HybridPolicy;
    providers_needed: HybridProvider[];
    available: boolean;
    /** What to set up first; null when `available`. */
    reason: string | null;
}

/** POST /api/hybrid/preview: the decision a request would get, without running a turn. */
export interface HybridPreview {
    model: string;
    tier: 'cheap' | 'strong';
    kind: HybridKind | null;
    difficulty: number | null;
    stakes: 'low' | 'high' | null;
    reason: string;
    /** How to fix a failed classification; empty otherwise. */
    fix: string;
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
    /** Null when a price is unknown. */
    cost: number | null;
    cost_if_all_strong: number | null;
    /** Null when there is nothing to compare. */
    saving_pct: number | null;
    /** Turns on a model with an unknown price, not in `cost`. */
    unpriced_turns: number;
    prices_fetched_at: string | null;
    prices_error: string | null;
    /** What the numbers cover, in the backend's words (e.g. which surfaces' turns are recorded). */
    scope?: string | null;
}
