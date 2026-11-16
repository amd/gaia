// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { LogsScreen } from '@/features/logs';

export default function Logs() {
    const router = useRouter();
    return <LogsScreen onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))} />;
}
