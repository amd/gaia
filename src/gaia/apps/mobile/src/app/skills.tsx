// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { SkillsScreen } from '@/features/skills';

export default function Skills() {
    const router = useRouter();
    return (
        <SkillsScreen
            onTry={(name, prompt) => router.replace({ pathname: '/new', params: { prompt, promptId: `skill:${name}:${Date.now()}` } })}
        />
    );
}
