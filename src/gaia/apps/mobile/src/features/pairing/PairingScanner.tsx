// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { CameraView, useCameraPermissions, type BarcodeScanningResult } from 'expo-camera';
import { Linking } from 'react-native';
import { useCallback, useRef } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Notice, Spinner, Stack, Text, useTheme } from '@/ui';

export interface PairingScannerProps {
    /** The QR code's text, once per code seen; pass it to `pairingApi.parseOffer`. */
    onCode(text: string): void;
    testID?: string;
}

const QR_ONLY = { barcodeTypes: ['qr' as const] };

/** The camera, scanning for the PC's pairing code; it asks for the camera only once this is shown. */
export function PairingScanner({ onCode, testID }: PairingScannerProps) {
    const t = useT();
    const theme = useTheme();
    const [permission, requestPermission] = useCameraPermissions();
    const last = useRef<string | null>(null);

    const scanned = useCallback(
        ({ data }: BarcodeScanningResult) => {
            if (data === last.current) return;
            last.current = data;
            onCode(data);
        },
        [onCode],
    );

    if (!permission) return <Spinner />;

    if (!permission.granted) {
        if (!permission.canAskAgain) {
            return (
                <Notice
                    tone="warning"
                    message={t('mobileApp.pairing.cameraOff')}
                    action={{ label: t('settings.title'), onPress: () => void Linking.openSettings() }}
                    testID={testID}
                />
            );
        }
        return (
            <Stack gap="md" testID={testID}>
                <Text>{t('mobileApp.pairing.cameraAsk')}</Text>
                <Button label={t('mobileApp.pairing.cameraAllow')} variant="primary" onPress={() => void requestPermission()} />
            </Stack>
        );
    }

    return (
        <CameraView
            testID={testID}
            facing="back"
            barcodeScannerSettings={QR_ONLY}
            onBarcodeScanned={scanned}
            style={{ width: '100%', aspectRatio: 1, borderRadius: theme.radius.lg, overflow: 'hidden' }}
        />
    );
}
