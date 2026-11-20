// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Minimum context window (tokens) required for reliable agent operation.
 *  Must match backend `_MIN_CONTEXT_SIZE` in `gaia.ui.routers.system`. */
export const MIN_CONTEXT_SIZE = 32768;

/** Fallback only: the backend's `default_model_name` in `/api/system/status` is the
 *  model this PC runs (it can be larger than this floor). Must match
 *  `DEFAULT_MODEL_NAME` in `gaia.llm.lemonade_client`. */
export const DEFAULT_MODEL_NAME = 'Gemma-4-E4B-it-GGUF';

/** Longest chat title the backend accepts; longer renames are cut to fit.
 *  Must match `MAX_SESSION_TITLE_CHARS` in `gaia.ui.models`. */
export const MAX_SESSION_TITLE_CHARS = 200;

/** Max spinner duration (ms) for model load operations (5 min safety reset). */
export const LOAD_SPINNER_TIMEOUT_MS = 300_000;

/** Max spinner duration (ms) for model download operations (30 min safety reset). */
export const DOWNLOAD_SPINNER_TIMEOUT_MS = 1_800_000;

/** Polling interval (ms) for checking model operation completion. */
export const MODEL_POLL_INTERVAL_MS = 10_000;

/** Composer text while the backend runs its boot tasks; sending waits until they finish. */
export const STARTING_REASON = 'GAIA is starting…';

/** The product's name, which no language translates. */
export const APP_NAME = 'GAIA';

/** Decimal gigabytes, the unit the backend reports downloads in. */
export const GB_UNIT = 'GB';

/** The title a chat is created with until it is named; `isPlaceholderTitle` and the draft cleanup match it. */
export const NEW_CHAT_TITLE = 'New chat';

/** How the backend's periodic "still working" status tick starts (`gaia/ui/run_manager.py`). */
export const PROGRESS_TICK_PREFIX = 'Model is processing';

/** The notices the server stores in a turn cut off before its answer. Must match `gaia/ui/restart_recovery.py` and `gaia/ui/scheduler.py`. */
export const CUT_OFF_NOTICE_TEXT = {
    interrupted: 'This answer was interrupted when GAIA closed. Send again to retry.',
    cancelled: 'This answer was interrupted before it finished. Send again to retry.',
    noReply: 'This answer ended before GAIA could save a reply. Send again to retry.',
    scheduleDeleted: 'This run was stopped because its schedule was deleted.',
    schedulePaused: 'This run was stopped because its schedule was paused.',
} as const;

/** The live line's phase while a dead stream is being re-attached; the line's own words are the user's language. */
export const RECONNECTING_PHASE = 'reconnecting';
