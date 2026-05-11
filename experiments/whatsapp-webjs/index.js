// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

// Privacy note:
// - Messages sent/received by this prototype transit Meta's WhatsApp service (WhatsApp Web).
// - Session credentials created by LocalAuth are stored locally on disk.
// - `run.log` may contain message bodies. By default this script redacts message bodies
//   from the log unless `LOG_MESSAGE_BODIES=1` is set in the environment. Treat logs as
//   sensitive and do not run this against production/personal accounts.
const LOG_PATH = path.resolve(__dirname, 'run.log');
function writeLog(...parts) {
  const line = `[${new Date().toISOString()}] ${parts.join(' ')}\n`;
  process.stdout.write(line);
  try {
    fs.appendFileSync(LOG_PATH, line);
  } catch (e) {
    // ignore logging errors
  }
}

// Minimal prototype: prints QR, logs incoming messages, echoes them back.
const client = new Client({ authStrategy: new LocalAuth() });

client.on('qr', (qr) => {
  qrcode.generate(qr, { small: true });
  writeLog('EVENT qr');
});

client.on('authenticated', (session) => {
  writeLog('EVENT authenticated');
});

client.on('auth_failure', (msg) => {
  writeLog('EVENT auth_failure', msg);
});

client.on('ready', () => {
  writeLog('EVENT ready');
});

client.on('disconnected', (reason) => {
  writeLog('EVENT disconnected', reason);
});

client.on('message', async (msg) => {
  // redact body unless explicitly allowed via env var
  const showBodies = process.env.LOG_MESSAGE_BODIES === '1';
  const bodyForLog = showBodies ? msg.body : '[REDACTED_BODY]';
  writeLog('IN', msg.from, bodyForLog);
  try {
    await msg.reply('Echo: ' + msg.body);
    writeLog('OUT reply', msg.from);
  } catch (e) {
    writeLog('ERROR reply', e && e.message);
  }
});

process.on('SIGINT', async () => {
  writeLog('SIGINT received — shutting down');
  try {
    await client.destroy();
  } catch (e) {
    // ignore
  }
  process.exit(0);
});

client.initialize();
