// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** First-run setup, model downloads, the GPU memory split and which Lemonade Server GAIA uses (/api/setup/*, /api/gpu-memory, /api/lemonade/*). */

/** `unchecked`: downloaded, but the run stopped before its load check; kept as is. */
export type SetupModelState = 'queued' | 'downloading' | 'verifying' | 'ready' | 'failed' | 'skipped' | 'cancelled' | 'unchecked';

/** One model's download in GET /api/setup/status. Sizes in bytes; speed in bytes/s. */
export interface SetupModelProgress {
    id: string;
    roles: SetupRole[];
    state: SetupModelState;
    bytes_done: number;
    bytes_total: number;
    speed_bps: number;
    eta_s: number | null;
    error: string | null;
    /** `verify`: the file didn't load, so Retry deletes and downloads it again. A test kind
     *  (`memory`, `stuck`, `answer`, `window`) leaves the file alone: Retry tests it again. */
    error_kind: 'network' | 'disk_full' | 'path_too_long' | 'lemonade' | 'verify' | 'memory' | 'stuck' | 'answer' | 'window' | null;
    can_retry: boolean;
    /** Unix seconds while a keep-trying wait runs; the state stays `downloading`. */
    retrying_until: number | null;
    downloaded_before_gb: number | null;
    /** This model's own download output, newest last; empty until there is some. */
    log?: string[];
}

export interface SetupOverall {
    bytes_done: number;
    bytes_total: number;
    speed_bps: number;
    eta_s: number | null;
    percent: number | null;
    failed: number;
}

export type SetupRole = 'chat' | 'vision' | 'embedding' | 'speech' | 'voice' | 'image_creation';

/** Something setup fetches besides the models (GET /api/setup/plan `components`). */
export interface SetupComponent {
    id: 'server' | 'backend' | 'agent';
    version: string | null;
    /** null when it can't be known before the download starts. */
    size_gb: number | null;
    source: string;
    /** null when the plan can't tell yet. */
    installed: boolean | null;
}

export type ModelNeedJob = 'chat' | 'vision' | 'embedding' | 'speech' | 'voice' | 'image_creation';

/** GET /api/setup/needs/{job}: the facts behind the missing-model prompt. */
export interface ModelNeed {
    job: ModelNeedJob;
    needed: boolean;
    model: { id: string; name: string; size_gb: number | null; source: string | null } | null;
    disk_free_gb: number | null;
    /** Why the job is off on this PC, so nothing can be downloaded for it: chat runs on the NPU. */
    off?: 'npu';
}

/** One row of the Test step (GET /api/setup/status `tests`). */
export interface SetupTest {
    job: SetupRole | 'server' | 'agent';
    subject: string | null;
    state: 'running' | 'passed' | 'failed' | 'skipped';
    error: string | null;
    /** Why it failed. Only `broken`, a model file that didn't load, offers a fresh download. */
    kind: 'memory' | 'stuck' | 'broken' | 'answer' | 'window' | 'missing' | 'agent' | null;
    seconds: number | null;
}

/** One model setup can pull or use (GET /api/setup/plan `catalog`). Every `*_gb` is decimal GB. */
export interface SetupCatalogEntry {
    id: string;
    name: string;
    roles: SetupRole[];
    purpose: string;
    if_skipped: string | null;
    recipe: string | null;
    /** GB left to download: 0 when downloaded, null when unknown. */
    size_gb: number | null;
    full_size_gb: number | null;
    partial: boolean;
    partial_gb: number;
    /** null when the server isn't reachable. */
    downloaded: boolean | null;
    in_lemonade: boolean;
    recommended: boolean;
    planned: boolean;
    /** GAIA's own model, not one only this Lemonade has. */
    gaia_model: boolean;
    selected: boolean;
    skipped: boolean;
    chat_choice: boolean;
    /** `verified`: GAIA's evals cover it. null for an optional model. */
    support: 'verified' | 'untested' | null;
    /** GAIA reads images with this model itself, so no second model loads. */
    reads_images: boolean;
    /** `compact` withholds image creation and other tools. */
    feature_tier: 'compact' | 'full';
    /** Where it downloads from; null when the server doesn't say. */
    source: string | null;
    /** null when this PC's memory couldn't be read: no invented numbers. */
    fits: boolean | null;
    need_gb: number | null;
    capacity_gb: number | null;
    fit_reason: string | null;
    /** Why it doesn't fit: too little memory, no NPU that can run it, or no backend for its kind. */
    fit_code?: 'memory' | 'npu' | 'backend' | null;
}

/** A 409 from a setup choice that asks to be confirmed, then sent again with `acknowledge` true. */
export interface SetupConfirm {
    code: 'no_fit' | 'low_disk';
    message: string;
    acknowledge: 'acknowledge_no_fit' | 'acknowledge_low_disk';
    model_id?: string;
    need_gb?: number | null;
    capacity_gb?: number | null;
    free_gb?: number | null;
    free_up_gb?: number | null;
    path?: string | null;
    drop?: string[];
}

// ── GPU memory split (/api/gpu-memory) ─────────────────────────────────

export interface GpuMemoryPending {
    setting_gb: number;
    previous_setting_gb: number | null;
    source: 'apply' | 'revert' | 'guided' | null;
    recorded_at: string | null;
    state: 'restart_required' | 'took_effect' | 'not_applied';
}

export interface GpuMemoryPreset {
    setting_gb: number;
    gpu_gb: number;
    unlocks: string[];
}

/** GET /api/gpu-memory. Without `applies` only the first six fields are sent. */
export interface GpuMemoryStatus {
    applies: boolean;
    show_in_setup: boolean;
    hidden_reason: string | null;
    os: 'linux' | 'windows' | 'darwin' | string;
    method: 'apply' | 'guided' | 'unsupported';
    method_reason: string;
    total_gb?: number;
    gpu_gb?: number;
    cpu_gb?: number;
    setting?: { name: string; current_gb: number; min_gb: number; max_gb: number };
    reserve_gb?: number;
    needs_admin?: boolean;
    needs_restart?: boolean;
    presets?: GpuMemoryPreset[];
    recommended_setting_gb?: number | null;
    models?: Array<{ id: string; size_gb: number; need_gb: number; default: boolean; fits_now: boolean; fits_at_setting_gb: number | null }>;
    pending?: GpuMemoryPending | null;
    revert?: { available: boolean; previous_setting_gb: number | null };
    guided_steps?: string[];
    /** The one value the steps ask the user to enter (AMD Software's GB on Windows,
     *  pages on Linux); null when this PC has nothing to give. */
    guided_value?: string | null;
}

// ── First-run setup (GET /api/setup/*) ────────────────────────────────────

export type SetupStepStatus = 'pending' | 'active' | 'done' | 'failed' | 'cancelled';

export interface SetupStep {
    key: 'server' | 'models' | 'finish';
    label: string;
    detail: string;
    status?: SetupStepStatus;
}

/** `gaia init --check --json`: ready, or the stage and reasons it is not. */
export interface SetupCheck {
    ready: boolean;
    stage?: 'setup' | 'upgrade' | 'server' | 'load' | string;
    reasons?: string[];
    /** Setup was seen ready on this PC before, so a `server` stage is an outage, not a first run. */
    initialized?: boolean;
    models?: Array<{ id: string; role: string; size_gb: number | null; loaded: boolean; error: string | null }>;
    /** True but no setup step, e.g. no GAIA agent build for this PC. */
    warnings?: string[];
    /** The hub has no GAIA agent build for this PC, so chat can't run here. */
    no_agent_build?: boolean;
    steps: SetupStep[];
    /** The steps for a cloud provider, which downloads only the embedding model. */
    cloud_steps?: SetupStep[];
}

/** A failed setup with a known fix: the user's Lemonade Server isn't answering. */
export interface SetupProblem {
    kind: 'server_unreachable' | 'server_too_old';
    /** One plain sentence: what is wrong, in English, with the address and versions. */
    message: string;
    /** How to start that server, or null when this PC has none to start. */
    start: string | null;
    /** Setup can switch to GAIA's own server instead. */
    use_gaia: boolean;
    /** The address set outside the app (LEMONADE_BASE_URL) that picked this server; switching ignores it. */
    env_url?: string | null;
}

export interface SetupDevice {
    kind: string;
    name: string;
    family: string | null;
    available: boolean;
    integrated?: boolean | null;
    cores?: number | null;
    threads?: number | null;
}

export interface SetupGaiaBuild {
    version: string;
    /** A published release or release candidate, a wheel built from a checkout, or a checkout itself. */
    kind: 'release' | 'release_candidate' | 'local' | 'source';
    /** The candidate's number, e.g. "2" for 0.25.5rc2. */
    rc: string | null;
    /** The checkout's commit, for a source build. */
    commit: string | null;
}

export interface SetupSystem {
    gaia_version: string;
    gaia_build?: SetupGaiaBuild;
    /** Settings, chats, logs and GAIA's own Lemonade. */
    gaia_home: string | null;
    agent_version: string | null;
    lemonade_version: string | null;
    os: string | null;
    processor: string | null;
    physical_memory: string | null;
    devices: SetupDevice[];
}

export type SetupLogStage = 'check' | 'download' | 'test';

export interface SetupRunStatus {
    state: 'idle' | 'running' | 'verifying' | 'ready' | 'failed' | 'cancelled';
    steps?: SetupStep[];
    text?: string;
    percent?: number | null;
    /** How much of the current download is done, e.g. "1.20 GB of 5.60 GB". */
    size?: string | null;
    error?: string | null;
    problem?: SetupProblem | null;
    command?: string;
    log_tail?: string[];
    /** Each stage's log, its last lines: the checks, the downloads, the Test step. */
    logs?: Partial<Record<SetupLogStage, string[]>>;
    skip_chat_model?: boolean;
    /** The run is switching to GAIA's own server; a retry must keep switching. */
    gaia_lemonade?: boolean;
    /** A model's download failed in this run; its row says why. */
    model_failed?: boolean;
    /** Each piece besides the models, by id. */
    components?: Record<string, 'active' | 'done' | 'failed'>;
    /** Why a failed piece failed, when setup knows: `transient` may pass on Retry. */
    component_kinds?: Record<string, 'transient' | 'other'>;
    tests?: SetupTest[];
    /** The run only starts the model server; models are chosen and pulled after. */
    server_only?: boolean;
    /** The run replaces an older embedded Lemonade artifact without model downloads. */
    upgrade_embedded?: boolean;
    /** The chat model setup chose, once it has said. */
    chat_model?: string | null;
    /** Each model's download, the run's and any single download's. */
    models?: SetupModelProgress[];
    overall?: SetupOverall;
}

/** One thing between this PC and running a model on its NPU (Linux setup). */
export interface SetupNpuProblem {
    code: 'driver' | 'device' | 'firmware' | 'access' | 'memlock' | 'runtime';
    title: string;
    fix: string;
    params: Record<string, string>;
}

export interface SetupNpuStatus {
    present: boolean;
    ready: boolean;
    problems: SetupNpuProblem[];
}

/** GET /api/setup/plan: what setup will use and download, before it starts. */
export interface SetupPlan {
    /** `url` is null while GAIA's own server isn't running: it has no address yet. */
    lemonade: { mode: 'gaia' | 'external'; url: string | null; reachable: boolean };
    chat_model: { id: string; user_set: boolean; size_gb: number | null; downloaded: boolean | null };
    recommended_model: string | null;
    /** GAIA's own server isn't running yet, so the final pick comes once it is. */
    decided_after_server: boolean;
    memory_gb: number | null;
    memory_source: string | null;
    options: Array<{ id: string; size_gb: number | null; fits: boolean | null; reason: string | null }>;
    models: Array<{ id: string; role: 'chat' | 'embedding'; size_gb: number | null }>;
    download_gb: number | null;
    capacity_gb?: number | null;
    /** The drive Lemonade stores models on; `free_gb` null when it couldn't be read. */
    disk?: { path: string | null; free_gb: number | null; source: 'lemonade' | 'local' | null };
    disk_warning?: string | null;
    skipped_models?: string[];
    catalog?: SetupCatalogEntry[];
    /** Backends Lemonade reports for this PC. */
    backends?: Array<{ recipe: string; backend: string; state: string | null; version?: string | null; default: boolean }>;
    /** An NPU Lemonade reports, and what stops a model running on it yet. */
    npu?: SetupNpuStatus;
    /** What setup found across the stack; null where nothing could find out. */
    system?: SetupSystem;
    /** Everything else setup fetches. */
    components?: SetupComponent[];
    /** The model the user chose to read images; null is GAIA's choice. */
    vision_model?: string | null;
}

/** A task a new chat offers, from what is set up on this PC. */
export interface StartCard {
    /** The skill that sharpens this task when installed; also the card's id. */
    skill: string;
    title: string;
    note: string;
    prompt: string;
    /** The prompt ends where the user types the subject, so it fills the box instead of sending. */
    needs_input: boolean;
}

/** GET /api/onboarding/start */
export interface StartScreen {
    cards: StartCard[];
    /** choosable: a new chat can be given another folder. */
    working_folder: { path: string; name: string; gaia_folder: boolean; choosable: boolean };
    /** Why there are no cards: the agent couldn't list its skills. */
    skills_error?: string | null;
}

// ── Which Lemonade Server GAIA uses (/api/lemonade/*) ─────────────────────

export type LemonadeMode = 'gaia' | 'external';

/** GET /api/lemonade/target: the server GAIA uses now and who chose it. */
export interface LemonadeTarget {
    mode: LemonadeMode;
    /** null until the user (or `gaia init`) has chosen. */
    saved_mode: LemonadeMode | null;
    /** `env` means LEMONADE_BASE_URL chose it; switching to GAIA's own overrides it. */
    source: 'env' | 'config' | 'gaia';
    base_url: string | null;
    saved_base_url: string | null;
    api_key_stored: boolean;
    /** The LEMONADE_BASE_URL value GAIA's own was chosen over, while it still holds. */
    env_url_ignored?: string | null;
    gaia_installed: boolean;
    /** Nobody has chosen yet: offer a Lemonade found running. */
    offer: boolean;
    what_gaia_does: string;
}

export type LemonadeProbeStatus = 'ok' | 'needs_key' | 'bad_key' | 'too_old' | 'not_lemonade' | 'unreachable';

/** A Lemonade the user already runs, from GET /api/lemonade/detect. */
export interface LemonadeFound {
    url: string;
    status: LemonadeProbeStatus;
    version: string | null;
    minimum: string | null;
    message: string;
}

export interface LemonadeTargetBody {
    mode: LemonadeMode;
    base_url?: string;
    api_key?: string;
}
