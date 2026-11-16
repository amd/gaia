// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createAudioPlayer } from 'expo-audio';

const CHIME_LENGTH_MS = 1000;

/** The two-note chime the web plays with a chat alert. */
export function playAlertSound(): void {
    try {
        const player = createAudioPlayer(require('./alert-chime.wav'));
        player.play();
        setTimeout(() => player.remove(), CHIME_LENGTH_MS);
    } catch (err) {
        console.error(`Could not play the alert sound: ${err instanceof Error ? err.message : String(err)}`);
    }
}
