import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// Read the GAIA version from version.py (single source of truth)
function getGaiaVersion(): string {
    try {
        const versionPy = readFileSync(
            resolve(__dirname, '..', '..', 'version.py'),
            'utf-8'
        );
        const match = versionPy.match(/__version__\s*=\s*"([^"]+)"/);
        return match ? match[1] : '0.0.0';
    } catch {
        // Fallback to package.json version
        try {
            const pkg = JSON.parse(readFileSync(resolve(__dirname, 'package.json'), 'utf-8'));
            return pkg.version || '0.0.0';
        } catch {
            return '0.0.0';
        }
    }
}

const gaiaVersion = getGaiaVersion();

export default defineConfig({
    plugins: [react()],
    base: './',
    define: {
        __APP_VERSION__: JSON.stringify(gaiaVersion),
    },
    server: {
        port: 5174,
        proxy: {
            '/api': 'http://localhost:4200',
        },
    },
    build: {
        outDir: 'dist',
        emptyOutDir: true,
    },
});
