// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Constants from 'expo-constants';
import { Platform } from 'react-native';

import { buildBugReportUrl } from '@webui/utils/bugReport.core';

/** The prefilled GitHub bug report for an unexpected error, the web UI's link with this phone's details. */
export function phoneBugReportUrl(title: string): string {
    const version = Constants.expoConfig?.version ?? 'unknown';
    const environment = `**Environment:**\n- GAIA Version: ${version}\n- Platform: ${Platform.OS}\n- Interface: GAIA phone app`;
    return buildBugReportUrl(title, undefined, environment);
}
