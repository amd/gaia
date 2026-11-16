// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { HomeScreen } from '@/features/home/HomeScreen';
import { useConnectionStore } from '@/state/connection';

import NewChat from './new';

// A connected phone opens on the start screen, as the web does; Home only says there is no PC yet.
export default function Index() {
    const connected = useConnectionStore((s) => s.connection !== null);
    return connected ? <NewChat /> : <HomeScreen />;
}
