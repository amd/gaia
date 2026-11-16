// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, type ReactNode } from 'react';
import { StatusBar } from 'react-native';
import { useFonts } from 'expo-font';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { SheetHost } from './overlays/SheetHost';
import { fontFiles } from './theme/fonts';
import { ThemeProvider, useTheme } from './theme/ThemeProvider';

const Mounted = createContext(false);

function Frame({ children }: { children: ReactNode }) {
    const { scheme, colors } = useTheme();
    const [loaded, error] = useFonts(fontFiles);
    if (error) {
        throw new Error(
            `The UI fonts failed to load: ${error.message}. Check that @expo-google-fonts/inter, /jetbrains-mono and /fraunces are installed (npm ci in src/gaia/apps/mobile).`,
        );
    }
    if (!loaded) return null;
    return (
        <GestureHandlerRootView style={{ flex: 1, backgroundColor: colors.bgPrimary }}>
            <SafeAreaProvider>
                <KeyboardProvider>
                    <StatusBar barStyle={scheme === 'dark' ? 'light-content' : 'dark-content'} />
                    <SheetHost>{children}</SheetHost>
                </KeyboardProvider>
            </SafeAreaProvider>
        </GestureHandlerRootView>
    );
}

export function UIProvider({ children }: { children: ReactNode }) {
    const nested = useContext(Mounted);
    if (nested) return <>{children}</>;
    return (
        <Mounted.Provider value>
            <ThemeProvider>
                <Frame>{children}</Frame>
            </ThemeProvider>
        </Mounted.Provider>
    );
}
