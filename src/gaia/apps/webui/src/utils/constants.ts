// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Minimum context window (tokens) required for reliable agent operation.
 *  Must match backend `_MIN_CONTEXT_SIZE` in `gaia.ui.routers.system`. */
export const MIN_CONTEXT_SIZE = 32768;

/** Fallback only: the backend's `default_model_name` in `/api/system/status` is the
 *  model this PC runs (it can be larger than this floor). Must match
 *  `DEFAULT_MODEL_NAME` in `gaia.llm.lemonade_client`. */
export const DEFAULT_MODEL_NAME = 'Gemma-4-E4B-it-GGUF';

/** Max spinner duration (ms) for model load operations (5 min safety reset). */
export const LOAD_SPINNER_TIMEOUT_MS = 300_000;

/** Max spinner duration (ms) for model download operations (30 min safety reset). */
export const DOWNLOAD_SPINNER_TIMEOUT_MS = 1_800_000;

/** Polling interval (ms) for checking model operation completion. */
export const MODEL_POLL_INTERVAL_MS = 10_000;
