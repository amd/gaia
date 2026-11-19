// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** installer/headless/hub_mirror.py's folder, served as the hub by playwright.config.ts. */

export const HUB_MIRROR = process.env.GAIA_E2E_HUB_MIRROR || '';
export const HUB_MIRROR_PORT = Number(process.env.GAIA_E2E_HUB_PORT || 18731);
/** What the backend and `gaia init` get as GAIA_HUB_URL; '' leaves the live hub. */
export const HUB_URL = HUB_MIRROR ? `http://127.0.0.1:${HUB_MIRROR_PORT}` : '';
