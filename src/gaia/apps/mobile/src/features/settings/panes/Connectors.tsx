// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ConnectorsBody } from '@/features/connectors';

import { SettingsAnchor } from '../targets';

export default function Connectors() {
    return (
        <SettingsAnchor name="pane">
            <ConnectorsBody />
        </SettingsAnchor>
    );
}
