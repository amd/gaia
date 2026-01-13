// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA SDK Documentation Proxy Server
 *
 * Auth proxy for Mintlify-hosted documentation with access code protection.
 * Proxies authenticated requests to the Mintlify site.
 *
 * Environment Variables:
 *   DOCS_AUTH_ENABLED - Set to 'true' to require access code
 *   DOCS_ACCESS_CODE  - The access code users must enter
 *   MINTLIFY_URL      - The Mintlify site URL (default: https://amd-gaia.ai)
 *   PORT              - Server port (default: 3000)
 */

require('dotenv').config();
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const cookieParser = require('cookie-parser');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3000;

// Configuration
const AUTH_ENABLED = process.env.DOCS_AUTH_ENABLED === 'true';
const ACCESS_CODE = process.env.DOCS_ACCESS_CODE || '';
const MINTLIFY_URL = process.env.MINTLIFY_URL || 'https://amd-gaia.ai';
const COOKIE_SECRET = process.env.COOKIE_SECRET || crypto.randomBytes(32).toString('hex');
const COOKIE_NAME = 'gaia_docs_auth';
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60 * 1000; // 7 days

// Middleware
app.use(cookieParser(COOKIE_SECRET));
app.use(express.urlencoded({ extended: true }));

// Generate auth token from access code
function generateToken(code) {
  return crypto.createHmac('sha256', COOKIE_SECRET).update(code).digest('hex');
}

// Verify auth token
function verifyToken(token) {
  if (!ACCESS_CODE) return false;
  const expected = generateToken(ACCESS_CODE);
  return token === expected;
}

// Sanitize redirect URL to prevent open redirect attacks
function sanitizeRedirect(url) {
  // Must start with / but not // (protocol-relative URLs)
  if (url && url.startsWith('/') && !url.startsWith('//')) {
    return url;
  }
  return '/';
}

// HTML-escape a string to prevent XSS
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Login page HTML ({{REDIRECT}} placeholder for original URL)
function getLoginPage(redirectUrl) {
  const safeRedirect = escapeHtml(sanitizeRedirect(redirectUrl));

  return `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GAIA SDK Documentation - Access Required</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
    }
    .container {
      background: rgba(255, 255, 255, 0.05);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 16px;
      padding: 48px;
      max-width: 420px;
      width: 90%;
      text-align: center;
    }
    .logo {
      width: 80px;
      height: 80px;
      margin-bottom: 24px;
    }
    h1 {
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .subtitle {
      color: rgba(255, 255, 255, 0.6);
      font-size: 14px;
      margin-bottom: 32px;
    }
    .form-group {
      margin-bottom: 24px;
    }
    input[type="password"] {
      width: 100%;
      padding: 14px 16px;
      font-size: 16px;
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 8px;
      background: rgba(0, 0, 0, 0.3);
      color: #fff;
      outline: none;
      transition: border-color 0.2s;
    }
    input[type="password"]:focus {
      border-color: #ED1C24;
    }
    input[type="password"]::placeholder {
      color: rgba(255, 255, 255, 0.4);
    }
    button {
      width: 100%;
      padding: 14px 24px;
      font-size: 16px;
      font-weight: 600;
      border: none;
      border-radius: 8px;
      background: #ED1C24;
      color: #fff;
      cursor: pointer;
      transition: background 0.2s, transform 0.1s;
    }
    button:hover {
      background: #c8171e;
    }
    button:active {
      transform: scale(0.98);
    }
    .error {
      background: rgba(237, 28, 36, 0.2);
      border: 1px solid rgba(237, 28, 36, 0.3);
      color: #ff6b6b;
      padding: 12px;
      border-radius: 8px;
      margin-bottom: 24px;
      font-size: 14px;
    }
    .footer {
      margin-top: 32px;
      font-size: 12px;
      color: rgba(255, 255, 255, 0.4);
    }
    .footer a {
      color: rgba(255, 255, 255, 0.6);
      text-decoration: none;
    }
    .footer a:hover {
      color: #ED1C24;
    }
  </style>
</head>
<body>
  <div class="container">
    <svg class="logo" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="45" stroke="#ED1C24" stroke-width="4" fill="none"/>
      <text x="50" y="58" text-anchor="middle" fill="#ED1C24" font-size="24" font-weight="bold" font-family="sans-serif">GAIA</text>
    </svg>
    <h1>GAIA SDK Documentation</h1>
    <p class="subtitle">This documentation is access-restricted. Please enter the access code to continue.</p>
    {{ERROR}}
    <form method="POST" action="/auth/login">
      <input type="hidden" name="redirect" value="${safeRedirect}">
      <div class="form-group">
        <input type="password" name="code" placeholder="Enter access code" required autofocus>
      </div>
      <button type="submit">Access Documentation</button>
    </form>
    <p class="footer">
      Need access? Contact <a href="https://github.com/amd/gaia">GAIA team</a>
    </p>
  </div>
</body>
</html>
`;
}

// Health check endpoint (must be before auth middleware)
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok', auth: AUTH_ENABLED, target: MINTLIFY_URL });
});

// Auth middleware
function authMiddleware(req, res, next) {
  // Skip auth if disabled
  if (!AUTH_ENABLED) {
    return next();
  }

  // Skip auth for login/logout routes and health check
  if (req.path.startsWith('/auth/') || req.path === '/health') {
    return next();
  }

  // Check for valid auth cookie
  const token = req.signedCookies[COOKIE_NAME];
  if (token && verifyToken(token)) {
    return next();
  }

  // Show login page with original URL preserved for redirect after login
  const originalUrl = req.originalUrl || req.url || '/';
  res.status(401).send(getLoginPage(originalUrl).replace('{{ERROR}}', ''));
}

// Login handler
app.post('/auth/login', (req, res) => {
  const { code, redirect } = req.body;
  const safeRedirect = sanitizeRedirect(redirect);

  if (code === ACCESS_CODE) {
    // Set signed cookie
    const token = generateToken(code);
    res.cookie(COOKIE_NAME, token, {
      signed: true,
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      maxAge: COOKIE_MAX_AGE,
      sameSite: 'lax'
    });
    // Redirect to the original URL the user was trying to access
    res.redirect(safeRedirect);
  } else {
    // Preserve the redirect URL even on error so user can retry
    res.redirect(`/auth/login-error?redirect=${encodeURIComponent(safeRedirect)}`);
  }
});

// Login error handler (preserves redirect URL)
app.get('/auth/login-error', (req, res) => {
  const safeRedirect = sanitizeRedirect(req.query.redirect);
  const errorHtml = '<div class="error">Invalid access code. Please try again.</div>';
  res.status(401).send(getLoginPage(safeRedirect).replace('{{ERROR}}', errorHtml));
});

// Logout handler
app.get('/auth/logout', (req, res) => {
  res.clearCookie(COOKIE_NAME);
  res.redirect('/');
});

// Apply auth middleware
app.use(authMiddleware);

// Proxy to Mintlify site (after auth)
const proxyMiddleware = createProxyMiddleware({
  target: MINTLIFY_URL,
  changeOrigin: true,
  secure: true,
  // Don't modify the path
  pathRewrite: null,
  // Handle proxy errors
  on: {
    error: (err, req, res) => {
      console.error('Proxy error:', err.message);
      res.status(502).send('Documentation temporarily unavailable. Please try again later.');
    },
    proxyReq: (proxyReq, req, res) => {
      // Remove cookies from proxied request (Mintlify doesn't need our auth cookies)
      proxyReq.removeHeader('cookie');
    }
  }
});

app.use('/', proxyMiddleware);

// Start server
app.listen(PORT, () => {
  console.log(`GAIA Docs Proxy running on port ${PORT}`);
  console.log(`Proxying to: ${MINTLIFY_URL}`);
  console.log(`Auth protection: ${AUTH_ENABLED ? 'ENABLED' : 'DISABLED'}`);
  if (AUTH_ENABLED && !ACCESS_CODE) {
    console.warn('WARNING: Auth is enabled but DOCS_ACCESS_CODE is not set!');
  }
});
