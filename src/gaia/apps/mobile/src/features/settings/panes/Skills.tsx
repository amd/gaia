// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { SkillsBody } from '@/features/skills';

import { SettingsAnchor } from '../targets';

export default function Skills() {
    const router = useRouter();
    return (
        <SettingsAnchor name="pane">
            <SkillsBody
                onTry={(name, prompt) => router.replace({ pathname: '/new', params: { prompt, promptId: `skill:${name}:${Date.now()}` } })}
            />
        </SettingsAnchor>
    );
}
