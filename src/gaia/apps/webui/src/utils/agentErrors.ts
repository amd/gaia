// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { enMessages } from '../i18n/locales/en';
import { ref, type Param } from '../i18n/registry';

// What `RelayProfile._with_log_folder` appends in gaia/ui/sidecar/profiles.py.
const LOG_FOLDER = ' The details are in this log folder: ';

/** An agent error the backend writes in English, worded in each language; text GAIA has no wording for stays as sent. */
export function agentErrorParam(text: string): Param {
    const at = text.indexOf(LOG_FOLDER);
    const message = at < 0 ? text : text.slice(0, at);
    if (message !== enMessages['agentError.startFailed']) return text;
    return at < 0
        ? ref('agentError.startFailed')
        : ref('agentError.withLogFolder', { message: ref('agentError.startFailed'), path: text.slice(at + LOG_FOLDER.length) });
}
