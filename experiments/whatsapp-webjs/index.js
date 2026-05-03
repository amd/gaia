// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

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
  writeLog('IN', msg.from, msg.body);
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
