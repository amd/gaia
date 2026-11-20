// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** True for a failure to reach the model server; its raw text means nothing to a person. */
export function isServerDown(message: string): boolean {
    return /not reachable|not answering|model server at|ConnectionPool|NewConnectionError|Connection refused|Max retries/i.test(message);
}
