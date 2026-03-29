// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Minimum context window (tokens) required for reliable agent operation.
 *  Must match backend `_MIN_CONTEXT_SIZE` in `gaia.ui.routers.system`. */
export const MIN_CONTEXT_SIZE = 32768;

/** Default model name used by GAIA Chat when no custom override is set.
 *  Must match backend `_DEFAULT_MODEL_NAME` in `gaia.ui.routers.system`. */
export const DEFAULT_MODEL_NAME = 'Qwen3.5-35B-A3B-GGUF';

/** Max spinner duration (ms) for model load operations (5 min safety reset). */
export const LOAD_SPINNER_TIMEOUT_MS = 300_000;

/** Max spinner duration (ms) for model download operations (30 min safety reset). */
export const DOWNLOAD_SPINNER_TIMEOUT_MS = 1_800_000;

/** Polling interval (ms) for checking model operation completion. */
export const MODEL_POLL_INTERVAL_MS = 10_000;
