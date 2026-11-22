import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'fs';
import { createRequire } from 'module';
import { resolve } from 'path';
import { serviceWorkerPlugin } from './src/pwa/serviceWorkerPlugin';

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

// The backend refuses /api without its token; the dev server sends it like the desktop app does.
const uiToken = createRequire(import.meta.url)('./services/ui-token.cjs');

export default defineConfig({
    plugins: [react({ jsxImportSource: '@gaia/jsx' }), serviceWorkerPlugin(resolve(__dirname, 'src/pwa/sw.js'), gaiaVersion)],
    resolve: { alias: { '@gaia/jsx': resolve(__dirname, 'src/i18n/jsx') } },
    base: './',
    define: {
        __APP_VERSION__: JSON.stringify(gaiaVersion),
    },
    server: {
        port: 5174,
        proxy: {
            '/api': {
                target: 'http://localhost:4200',
                changeOrigin: true,
                // Disable buffering for SSE streaming support
                configure: (proxy) => {
                    proxy.on('proxyReq', (proxyReq) => {
                        const token = uiToken.defaultReader.get();
                        if (token) proxyReq.setHeader('Authorization', `Bearer ${token}`);
                    });
                    proxy.on('proxyRes', (proxyRes) => {
                        if (proxyRes.statusCode === 401) uiToken.defaultReader.clear();
                        // Prevent response buffering for text/event-stream
                        if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
                            proxyRes.headers['cache-control'] = 'no-cache';
                            proxyRes.headers['x-accel-buffering'] = 'no';
                        }
                    });
                },
            },
        },
    },
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        // Browsers with native :dir(): older targets rewrite it into :lang() lists,
        // which ignore dir="auto" and fade English text in Arabic at the wrong end.
        cssTarget: ['chrome120', 'edge120', 'firefox120', 'safari16.4'],
    },
});
