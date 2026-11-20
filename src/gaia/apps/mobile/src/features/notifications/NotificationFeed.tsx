// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';

import { startNotificationFeed } from '@/state/notifications';

/** Files each chat's approvals and policy blocks in the center for as long as the app runs. */
export function NotificationFeed() {
    useEffect(startNotificationFeed, []);
    return null;
}
