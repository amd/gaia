# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Self-contained HTML for the email-agent playground (issue #1796).

``render_playground_html()`` returns ONE self-contained page — inline CSS + inline
vanilla JS, no external requests of any kind. It is served by the sidecar itself
(``GET /v1/email/playground``) so it shares the sidecar's localhost origin:

  * Same-origin → no CORS, the page only ever ``fetch``es the local sidecar.
  * Served from the local (frozen) binary → no remote-controlled code.
  * The route sets ``Content-Security-Policy: connect-src 'self'`` so the browser
    STRUCTURALLY forbids the page from talking to any non-local URL.

Net: code + data + compute all stay on the user's machine — a structural
guarantee, not a promise. Inference runs on local Lemonade; email content never
crosses the network.

No webfonts/CDNs (offline + no-egress): a system font stack stands in for Inter.
All dynamic values are written with ``textContent`` (never ``innerHTML``), so a
triaged email body can't inject markup.
"""

from __future__ import annotations

# Brand tokens mirror website/src/design/tokens.css (dark theme).
_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>GAIA Email Agent · Playground</title>
<style>
  :root{
    --bg:#0a0a0b; --surface:#111113; --card:#0d0d0f; --border:#1f1f22;
    --text:#f0f0ee; --muted:#8e8e92; --gold:#e2a33e;
    --soft:rgba(226,163,62,.12); --line:rgba(226,163,62,.35);
    --ok:#9ecb8a; --bad:#e87a7a;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(120% 60% at 50% -8%,rgba(226,163,62,.06),transparent 55%),var(--bg);
    color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.5}
  .wrap{max-width:880px;margin:0 auto;padding:34px 22px 64px}
  a{color:var(--gold);text-decoration:none}
  a:hover{text-decoration:underline}
  code,.mono{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:.86em}
  .top{display:flex;align-items:center;gap:13px;flex-wrap:wrap;margin-bottom:4px}
  h1{font-size:23px;font-weight:700;letter-spacing:-.01em;margin:0}
  .badge{font-size:11.5px;font-weight:600;letter-spacing:.04em;border-radius:999px;padding:4px 11px;
    background:var(--soft);border:1px solid var(--line);color:var(--gold)}
  .ver{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:11.5px;font-weight:600;color:var(--gold);
    background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
  .sub{color:var(--muted);font-size:14px;margin:4px 0 26px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:14px;margin:14px 0}
  .card h2{font-size:13px;font-weight:600;letter-spacing:.10em;text-transform:uppercase;color:var(--muted);margin:0 0 14px}
  /* Accordion: each section is a native <details> dropdown. */
  details.card{transition:border-color .15s}
  details.card[open]{border-color:var(--line)}
  details.card>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:12px;
    padding:16px 22px;user-select:none}
  details.card>summary::-webkit-details-marker{display:none}
  .sum-title{font-size:13px;font-weight:600;letter-spacing:.10em;text-transform:uppercase;color:var(--muted)}
  .sum-desc{font-size:12.5px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0}
  details.card>summary:hover .sum-title,details.card[open]>summary .sum-title{color:var(--gold)}
  .chev{margin-left:auto;color:var(--muted);font-size:17px;line-height:1;transition:transform .18s ease}
  details.card[open]>summary .chev{transform:rotate(90deg);color:var(--gold)}
  .sum-stat{font-size:11px;font-weight:600;border-radius:999px;padding:2px 9px;background:var(--soft);
    border:1px solid var(--line);color:var(--gold);text-transform:none;letter-spacing:0}
  .card-body{padding:2px 22px 20px}
  .row{display:flex;align-items:flex-start;gap:12px;padding:9px 0;border-top:1px solid var(--border)}
  .row:first-of-type{border-top:0}
  .dot{flex:0 0 auto;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;
    font-size:12px;font-weight:700;margin-top:1px}
  .dot.ok{background:rgba(158,203,138,.14);color:var(--ok);border:1px solid rgba(158,203,138,.4)}
  .dot.bad{background:rgba(232,122,122,.14);color:var(--bad);border:1px solid rgba(232,122,122,.4)}
  .dot.wait{background:var(--soft);color:var(--gold);border:1px solid var(--line)}
  .row .body{min-width:0;flex:1}
  .row .name{font-weight:600;font-size:14.5px}
  .row .detail{color:var(--muted);font-size:13px;margin-top:2px;word-break:break-word}
  .fix{margin-top:7px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .cmd{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:5px 10px;
    font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12px;color:var(--text)}
  button{font:inherit;cursor:pointer;border-radius:8px;border:1px solid var(--line);background:var(--soft);
    color:var(--gold);padding:8px 15px;font-size:13.5px;font-weight:600}
  button:hover{background:rgba(226,163,62,.18)}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.ghost{background:transparent;border-color:var(--border);color:var(--muted)}
  button.copy{padding:4px 9px;font-size:11.5px}
  button.primary{background:var(--gold);color:#0a0a0b;border-color:var(--gold)}
  button.primary:hover{background:#eab556}
  /* gaia init streaming terminal */
  .term{display:none;margin-top:12px;background:#08080a;border:1px solid var(--border);border-radius:9px;
    padding:12px 14px;max-height:240px;overflow:auto;
    font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12px;line-height:1.55;color:#d8d8d4}
  .term.show{display:block}
  .term-bar{display:flex;align-items:center;gap:8px;margin:12px 0 0;font-size:12px;color:var(--muted)}
  .term-bar .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .term-bar .dot.run{background:var(--gold);animation:pulse 1s infinite}
  .term-bar .dot.ok{background:var(--ok)} .term-bar .dot.bad{background:var(--bad)}
  @keyframes pulse{50%{opacity:.35}}
  .term-line{white-space:pre-wrap;word-break:break-word}
  .term-line.ok{color:var(--ok)} .term-line.bad{color:var(--bad)} .term-line.cmd{color:var(--gold)}
  textarea,input,select{width:100%;background:var(--card);border:1px solid var(--border);border-radius:9px;
    color:var(--text);font:inherit;font-size:13.5px;padding:9px 11px;margin-top:5px}
  textarea{min-height:96px;resize:vertical;font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12.5px}
  label{font-size:12.5px;color:var(--muted);font-weight:600}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .actions{margin-top:13px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .out{margin-top:14px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;display:none}
  .out.show{display:block}
  .pill{display:inline-block;font-size:11.5px;font-weight:600;border-radius:999px;padding:2px 10px;margin:0 6px 6px 0;
    background:var(--soft);border:1px solid var(--line);color:var(--gold)}
  .pill.bad{background:rgba(232,122,122,.14);border-color:rgba(232,122,122,.4);color:var(--bad)}
  ul.items{margin:8px 0 0;padding-left:18px;color:var(--text);font-size:13.5px}
  .muted{color:var(--muted)}
  .note{font-size:12.5px;color:var(--muted);margin-top:8px}
  .foot{margin-top:30px;color:var(--muted);font-size:12px;text-align:center}
  .kbd{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;color:var(--gold)}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>GAIA Email Agent · Playground</h1>
    <span class="ver" id="ver-badge"></span>
    <span class="badge">⬤ 100% local</span>
  </div>
  <div class="sub">Served by your local sidecar — code, data, and inference never leave this machine.</div>

  <!-- Connectors (playground mode only — /v1/email/connectors) -->
  <details class="card" id="conn-card" open>
    <summary><span class="sum-title">Connectors</span>
      <span class="sum-desc">connect Gmail / Outlook</span>
      <span class="sum-stat" id="conn-stat">checking…</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="row" id="conn-unavailable" style="border:0;padding-top:0;display:none">
        <div class="dot wait">i</div>
        <div class="body"><div class="name">Connector routes not mounted here</div>
          <div class="detail">This page is served somewhere without the connector routes (e.g. embedded
            in the Agent UI, which has its own connector settings). Connect a mailbox there or with
            <span class="kbd">gaia connectors</span> — the sidecar's send reads it from the shared
            store. Triage and draft work without a connection.</div></div>
      </div>
      <div id="conn-live" style="display:none">
        <div class="note" style="margin:0 0 4px">Connect with your own Google/Microsoft OAuth client credentials.
          Tokens are stored locally by GAIA's connector framework — nothing leaves this machine.</div>
        <div id="conn-providers"></div>
      </div>
    </div>
  </details>

  <!-- Stack health -->
  <details class="card" open>
    <summary><span class="sum-title">Stack health</span>
      <span class="sum-desc">sidecar · Lemonade · model</span>
      <span class="sum-stat" id="health-stat">checking…</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div id="health">
        <div class="row"><div class="dot wait" id="d-sidecar">…</div>
          <div class="body"><div class="name">Sidecar</div><div class="detail" id="t-sidecar">checking…</div></div></div>
        <div class="row"><div class="dot wait" id="d-lemonade">…</div>
          <div class="body"><div class="name">Lemonade + model</div><div class="detail" id="t-lemonade">checking…</div>
            <div class="fix" id="fix-lemonade" style="display:none"></div></div></div>
      </div>
      <div class="actions"><button id="recheck">Re-check</button>
        <button id="do-init" class="ghost">Run readiness check · /v1/email/init</button>
        <span class="note" id="health-note"></span></div>
      <div class="out" id="init-out"></div>
    </div>
  </details>

  <!-- Triage -->
  <details class="card">
    <summary><span class="sum-title">Triage an email</span>
      <span class="sum-desc">standalone · no mailbox</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="grid2">
        <div><label>From</label><input id="tr-from" value="Sarah Chen &lt;sarah@example.com&gt;" /></div>
        <div><label>Subject</label><input id="tr-subject" value="Prod incident follow-up" /></div>
      </div>
      <label style="display:block;margin-top:10px">Body</label>
      <textarea id="tr-body">Please review the incident report and reply by Friday. Action required.</textarea>
      <div class="actions"><button id="do-triage">Triage</button>
        <span class="note">Needs Lemonade + the model (see Stack health).</span></div>
      <div class="out" id="tr-out"></div>
    </div>
  </details>

  <!-- Batch triage (#1887) -->
  <details class="card">
    <summary><span class="sum-title">Batch triage</span>
      <span class="sum-desc">array in · array out · /v1/email/triage/batch</span><span class="chev">›</span></summary>
    <div class="card-body">
      <p class="note">Sends the two emails below as one <code>items</code> array and renders the
        parallel <code>results</code> array — each entry carries either a <code>result</code> or an
        <code>error</code>, correlated by <code>index</code>. Additive: the single Triage card above is unchanged.</p>
      <div class="grid2">
        <div><label>Item 0 — From</label><input id="tb-from0" value="Sarah Chen &lt;sarah@example.com&gt;" /></div>
        <div><label>Item 0 — Subject</label><input id="tb-subject0" value="Prod incident follow-up" /></div>
      </div>
      <label style="display:block;margin-top:10px">Item 0 — Body</label>
      <textarea id="tb-body0">Please review the incident report and reply by Friday. Action required.</textarea>
      <div class="grid2" style="margin-top:10px">
        <div><label>Item 1 — From</label><input id="tb-from1" value="Deals &lt;promo@shop.example&gt;" /></div>
        <div><label>Item 1 — Subject</label><input id="tb-subject1" value="50% off this weekend only" /></div>
      </div>
      <label style="display:block;margin-top:10px">Item 1 — Body</label>
      <textarea id="tb-body1">Limited-time offer — shop now and save big before Sunday.</textarea>
      <div class="actions"><button id="do-triage-batch">Triage batch</button>
        <span class="note">Needs Lemonade + the model. HTTP 200 with all items errored is valid — inspect each result.</span></div>
      <div class="out" id="tb-out"></div>
    </div>
  </details>

  <!-- Draft -->
  <details class="card">
    <summary><span class="sum-title">Draft a reply</span>
      <span class="sum-desc">standalone · mints a send token</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="grid2">
        <div><label>To</label><input id="df-to" value="sarah@example.com" /></div>
        <div><label>Subject</label><input id="df-subject" value="Re: Prod incident follow-up" /></div>
      </div>
      <label style="display:block;margin-top:10px">Reply body</label>
      <textarea id="df-body">Reviewed — I'll reply by Friday.</textarea>
      <div class="actions"><button id="do-draft">Mint draft token</button>
        <span class="note">Works with no Lemonade and no mailbox — it just mints a single-use send token.</span></div>
      <div class="out" id="df-out"></div>
    </div>
  </details>

  <!-- Send (mailbox-aware) -->
  <details class="card">
    <summary><span class="sum-title">Send a reply</span>
      <span class="sum-desc">picks the connected mailbox</span>
      <span class="sum-stat" id="send-stat" style="display:none">ready</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="grid2">
        <div><label>From mailbox</label><select id="send-from"></select></div>
        <div><label>To</label><input id="send-to" value="sarah@example.com" /></div>
      </div>
      <label style="display:block;margin-top:10px">Subject</label>
      <input id="send-subject" value="Re: Prod incident follow-up" />
      <label style="display:block;margin-top:10px">Body</label>
      <textarea id="send-body">Reviewed — I'll reply by Friday.</textarea>
      <div class="actions"><button id="do-send" class="primary" disabled>Draft &amp; send</button>
        <span class="note" id="send-note">Connect a mailbox in the Connectors panel above.</span></div>
      <div class="out" id="send-out"></div>
    </div>
  </details>

  <!-- Contract -->
  <details class="card">
    <summary><span class="sum-title">Contract</span>
      <span class="sum-desc">spec · openapi</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="actions">
        <button class="ghost" onclick="window.open('/v1/email/spec','_blank')">Open HTML spec</button>
        <button class="ghost" onclick="window.open('/openapi.json','_blank')">Open openapi.json</button>
        <span class="note" id="ver"></span>
      </div>
    </div>
  </details>

  <!-- Install / setup -->
  <details class="card">
    <summary><span class="sum-title">Install &amp; setup</span>
      <span class="sum-desc">run gaia init · npm · Lemonade</span><span class="chev">›</span></summary>
    <div class="card-body">
      <div class="actions" style="margin:0 0 2px">
        <button id="do-provision" class="primary">Run gaia init</button>
        <span class="note">downloads &amp; tests the email model via the sidecar — streamed below</span></div>
      <div class="term-bar" id="term-bar" style="display:none"><span class="dot" id="term-dot"></span><span id="term-status"></span></div>
      <div class="term" id="term"></div>

      <div class="note" style="margin:16px 0 6px">Or run the steps yourself:</div>
      <div class="fix"><code class="cmd" id="c-npm">npm i @amd-gaia/agent-email</code><button class="copy" data-copy="c-npm">copy</button></div>
      <div class="fix" style="margin-top:9px"><code class="cmd" id="c-lemon">lemonade-server serve</code><button class="copy" data-copy="c-lemon">copy</button>
        <span class="muted">start the local LLM</span></div>
      <div class="fix" style="margin-top:9px"><code class="cmd" id="c-init">gaia init --profile email</code><button class="copy" data-copy="c-init">copy</button>
        <span class="muted">install Lemonade + download &amp; test the email model</span></div>
      <div class="note">Docs: <a href="https://amd-gaia.ai/docs/guides/email" target="_blank" rel="noopener">amd-gaia.ai/docs/guides/email</a></div>
    </div>
  </details>

  <div class="foot">Inference runs on local Lemonade · this page can only reach <span class="kbd">'self'</span> (CSP) · nothing is sent off your machine.</div>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
function setRow(dotId, txtId, state, text){
  const d = $(dotId); d.className = "dot " + state;
  d.textContent = state === "ok" ? "✓" : state === "bad" ? "✗" : "…";
  $(txtId).textContent = text;
}
// At-a-glance status in the Stack health summary, so the collapsed dropdown
// still says whether the stack is ready.
function setStat(text, ok){
  const s = $("health-stat"); if(!s) return;
  s.textContent = text;
  s.style.background = ok ? "var(--soft)" : "rgba(232,122,122,.14)";
  s.style.borderColor = ok ? "var(--line)" : "rgba(232,122,122,.4)";
  s.style.color = ok ? "var(--gold)" : "var(--bad)";
}
// status 0 = network/transport failure (connection refused, sidecar gone), so
// callers can diagnose it uniformly instead of seeing an undefined status.
function httpError(status, body){ const e = new Error(body || ("HTTP " + status)); e.status = status; e.body = body || ""; return e; }
async function getJSON(path){
  let r;
  try{ r = await fetch(path, { headers: { accept: "application/json" } }); }
  catch(err){ throw httpError(0, String(err && err.message || err)); }
  const t = await r.text();
  if(!r.ok){ throw httpError(r.status, t); }
  return t ? JSON.parse(t) : null;
}
async function postJSON(path, body){
  let r;
  try{
    r = await fetch(path, { method:"POST",
      headers:{ accept:"application/json", "content-type":"application/json" },
      body: JSON.stringify(body) });
  }catch(err){ throw httpError(0, String(err && err.message || err)); }
  const t = await r.text();
  if(!r.ok){ throw httpError(r.status, t); }
  return t ? JSON.parse(t) : null;
}
// Mirror examples/demo.mjs diagnoseTriage — specific causes before generic timeout.
function diagnose(e){
  const b = String(e && (e.body || e.message) || "").toLowerCase();
  if(b.includes("not reachable") || b.includes("refused") || b.includes("connection"))
    return { cause:"Lemonade not found / not running", cmd:"lemonade-server serve", hint:"start the local LLM (install via gaia init)" };
  if(b.includes("model") || b.includes("not found") || b.includes("404") || b.includes("load") || b.includes("download"))
    return { cause:"Model not downloaded / unavailable", cmd:"gaia init", hint:"download + test the model" };
  if((e && e.status === 0) || b.includes("timed out") || b.includes("timeout"))
    return { cause:"Lemonade not responding (timed out)", cmd:"lemonade-server serve", hint:"is it running on the expected port?" };
  return { cause:"LLM triage failed", cmd:"", hint:(e && (e.body || e.message)) || "unknown error" };
}
function probePayload(){
  return { payload:{ kind:"single", principal:{ email:"me@example.com" },
    message:{ message_id:"playground-probe", from:{ name:"Sarah Chen", email:"sarah@example.com" },
      to:[{ email:"me@example.com" }], subject:"Prod incident follow-up",
      body:"Please review the incident report and reply by Friday. Action required." } } };
}
async function healthCheck(){
  setRow("d-sidecar","t-sidecar","wait","checking…");
  setRow("d-lemonade","t-lemonade","wait","checking…");
  $("fix-lemonade").style.display = "none";
  $("health-note").textContent = "";
  setStat("checking…", true);
  // Confirm via the email-scoped version so this works standalone AND when the
  // router is mounted on a product app (where root /version is the host's).
  try{
    const v = await getJSON("/v1/email/version");
    const av = v.agentVersion || "?", api = v.apiVersion || "?";
    setRow("d-sidecar","t-sidecar","ok", "up · apiVersion=" + api + " · agentVersion=" + av);
    $("ver").textContent = "apiVersion " + api + " · agentVersion " + av;
    // Header badge mirrors the version stamped on architecture.webp — sourced
    // live from /version, so the screenshot can never claim a stale version.
    $("ver-badge").textContent = "v" + av;
  }catch(e){
    setRow("d-sidecar","t-sidecar","bad", "not reachable — is the sidecar running?");
    setStat("offline", false);
  }
  // Lemonade + model: probe via triage.
  try{
    const res = await postJSON("/v1/email/triage", probePayload());
    setRow("d-lemonade","t-lemonade","ok", "ready · live triage → category=" + res.result.category);
    setStat("ready", true);
  }catch(e){
    if(e.status === 502 || e.status === 0){
      const d = diagnose(e);
      setRow("d-lemonade","t-lemonade","bad", d.cause + " — " + d.hint);
      setStat("not ready", false);
      if(d.cmd){
        const fx = $("fix-lemonade"); fx.style.display = "flex"; fx.textContent = "";
        const code = document.createElement("code"); code.className = "cmd"; code.textContent = d.cmd;
        const btn = document.createElement("button"); btn.className = "copy"; btn.textContent = "copy";
        btn.onclick = () => copy(d.cmd, btn);
        fx.appendChild(code); fx.appendChild(btn);
      }
    } else {
      setRow("d-lemonade","t-lemonade","bad", "unexpected error (HTTP " + e.status + ")");
      setStat("error", false);
    }
  }
}
async function doTriage(){
  const out = $("tr-out"); out.className = "out show"; out.textContent = "Triaging…";
  const m = parseFrom($("tr-from").value);
  try{
    const res = await postJSON("/v1/email/triage", { payload:{ kind:"single",
      principal:{ email:"me@example.com" },
      message:{ message_id:"playground-1", from:m, to:[{ email:"me@example.com" }],
        subject:$("tr-subject").value, body:$("tr-body").value } } });
    renderTriage(out, res.result);
  }catch(e){
    out.textContent = ""; const d = diagnose(e);
    const p = document.createElement("div");
    p.textContent = (e.status===502||e.status===0) ? ("✗ " + d.cause + " — " + d.hint + (d.cmd?(" ("+d.cmd+")"):"")) : ("✗ HTTP " + e.status + ": " + (e.body||e.message));
    out.appendChild(p);
  }
}
function renderTriage(out, r){
  out.textContent = "";
  const head = document.createElement("div");
  const cat = document.createElement("span"); cat.className = "pill"; cat.textContent = r.category; head.appendChild(cat);
  if(r.is_spam){ const s=document.createElement("span"); s.className="pill bad"; s.textContent="spam"; head.appendChild(s); }
  if(r.is_phishing){ const s=document.createElement("span"); s.className="pill bad"; s.textContent="phishing"; head.appendChild(s); }
  if(r.suggested_action){ const s=document.createElement("span"); s.className="pill"; s.textContent="→ "+r.suggested_action; head.appendChild(s); }
  out.appendChild(head);
  const sum = document.createElement("div"); sum.style.marginTop="8px"; sum.style.fontSize="13.5px";
  sum.textContent = r.summary || "(no summary)"; out.appendChild(sum);
  if(r.action_items && r.action_items.length){
    const ul = document.createElement("ul"); ul.className = "items";
    for(const a of r.action_items){ const li=document.createElement("li"); li.textContent = a.description + (a.due_hint?(" — "+a.due_hint):""); ul.appendChild(li); }
    out.appendChild(ul);
  }
}
function tbItem(idx){
  const m = parseFrom($("tb-from"+idx).value);
  return { kind:"single", principal:{ email:"me@example.com" },
    message:{ message_id:"playground-batch-"+idx, from:m, to:[{ email:"me@example.com" }],
      subject:$("tb-subject"+idx).value, body:$("tb-body"+idx).value } };
}
async function doTriageBatch(){
  const out = $("tb-out"); out.className = "out show"; out.textContent = "Triaging batch…";
  try{
    const res = await postJSON("/v1/email/triage/batch", { items:[ tbItem(0), tbItem(1) ] });
    out.textContent = "";
    for(const item of res.results){
      const block = document.createElement("div"); block.style.marginTop = "10px";
      const hdr = document.createElement("div"); hdr.style.fontSize="12px"; hdr.style.color="var(--faint)";
      hdr.textContent = "index " + item.index; block.appendChild(hdr);
      if(item.error){
        const er = document.createElement("div"); er.textContent = "✗ " + item.error.message; block.appendChild(er);
      }else{
        const inner = document.createElement("div"); renderTriage(inner, item.result); block.appendChild(inner);
      }
      out.appendChild(block);
    }
  }catch(e){
    out.textContent = ""; const d = diagnose(e);
    const p = document.createElement("div");
    p.textContent = (e.status===502||e.status===0) ? ("✗ " + d.cause + " — " + d.hint + (d.cmd?(" ("+d.cmd+")"):"")) : ("✗ HTTP " + e.status + ": " + (e.body||e.message));
    out.appendChild(p);
  }
}
async function doDraft(){
  const out = $("df-out"); out.className = "out show"; out.textContent = "Minting…";
  try{
    const res = await postJSON("/v1/email/draft", { to:[{ email:$("df-to").value }],
      subject:$("df-subject").value, body:$("df-body").value });
    out.textContent = "";
    const a = document.createElement("div");
    a.textContent = "✓ confirmation_token: "; const c = document.createElement("code"); c.className="cmd"; c.textContent = res.confirmation_token;
    a.appendChild(c); out.appendChild(a);
    const n = document.createElement("div"); n.className="note"; n.textContent = "Single-use, bound to (to, subject, body). Echo it to /v1/email/send to authorize sending."; out.appendChild(n);
  }catch(e){ out.textContent = "✗ HTTP " + e.status + ": " + (e.body||e.message); }
}
function parseFrom(s){
  const m = String(s).match(/^\s*(.*?)\s*<\s*([^>]+?)\s*>\s*$/);
  if(m) return { name:m[1]||undefined, email:m[2] };
  return { email:String(s).trim() };
}
function copy(text, btn){
  navigator.clipboard.writeText(text).then(()=>{ const o=btn.textContent; btn.textContent="copied"; setTimeout(()=>btn.textContent=o,1200); });
}
document.querySelectorAll("button.copy[data-copy]").forEach((b)=>{
  b.onclick = () => copy($(b.getAttribute("data-copy")).textContent, b);
});
// The authoritative readiness check (#1795): GET /v1/email/init returns the
// full status on BOTH 200 (ready) and 503 (not ready) — so read the body either
// way. 404 means this sidecar predates #1795; degrade gracefully.
async function doInit(){
  const out = $("init-out"); out.className = "out show"; out.textContent = "Running /v1/email/init …";
  let r;
  try{ r = await fetch("/v1/email/init", { headers:{ accept:"application/json" } }); }
  catch(e){ out.textContent = "✗ network error: " + (e && e.message); return; }
  if(r.status === 404){ out.textContent = "✗ /v1/email/init not available — update the sidecar (the readiness endpoint ships with #1795)."; return; }
  let d; const t = await r.text();
  try{ d = t ? JSON.parse(t) : null; }catch(e){ out.textContent = "✗ unexpected response (HTTP " + r.status + ")"; return; }
  if(!d){ out.textContent = "✗ empty response (HTTP " + r.status + ")"; return; }
  out.textContent = "";
  const head = document.createElement("div");
  const rp = document.createElement("span"); rp.className = "pill" + (d.ready ? "" : " bad");
  rp.textContent = d.ready ? "✓ ready" : ("✗ not ready · HTTP " + r.status); head.appendChild(rp);
  out.appendChild(head);
  const ul = document.createElement("ul"); ul.className = "items";
  const lem = document.createElement("li");
  lem.textContent = "Lemonade: " + (d.lemonade && d.lemonade.reachable ? "reachable" : "unreachable") + " · " + (d.lemonade && d.lemonade.base_url || "?");
  ul.appendChild(lem);
  const m = d.model || {};
  const loadable = (m.loadable === null || m.loadable === undefined) ? "loadable=n/a" : ("loadable=" + m.loadable);
  const mli = document.createElement("li");
  mli.textContent = "Model: " + (m.id || "?") + " · " + (m.present ? "present" : "missing") + " · " + loadable;
  ul.appendChild(mli);
  out.appendChild(ul);
  if(d.hint){ const h = document.createElement("div"); h.className = "note"; h.textContent = "→ " + d.hint; out.appendChild(h); }
}
// "Run gaia init" — POST /v1/email/init triggers provisioning (pull + test the
// email model via Lemonade) and STREAMS terminal output. Built to the contract
// the /init PR (#1813) will serve: GET = readiness probe, POST = provision.
function termLine(text, cls){
  const t = $("term");
  const d = document.createElement("div");
  d.className = "term-line" + (cls ? " " + cls : "");
  d.textContent = text; t.appendChild(d); t.scrollTop = t.scrollHeight;
}
function termStatus(state, text){
  $("term-bar").style.display = "flex";
  $("term-dot").className = "dot " + state;
  $("term-status").textContent = text;
}
function emitLine(raw){
  const line = String(raw).replace(/\r$/, "").replace(/^data:\s?/, "");  // tolerate SSE framing
  if(line === "") return;
  const lc = line.toLowerCase();
  const cls = /error|fail|✗|✘/.test(lc) ? "bad" : /done|ready|✓|success/.test(lc) ? "ok" : "";
  termLine(line, cls);
}
async function doProvision(){
  const btn = $("do-provision");
  $("term").className = "term show"; $("term").textContent = "";
  btn.disabled = true; termStatus("run", "running…");
  termLine("$ gaia init  →  POST /v1/email/init", "cmd");
  let res;
  try{
    res = await fetch("/v1/email/init", { method:"POST", headers:{ accept:"text/event-stream, text/plain" } });
  }catch(e){
    termLine("✗ network error: " + (e && e.message), "bad"); termStatus("bad","failed"); btn.disabled=false; return;
  }
  if(res.status === 404 || res.status === 405){
    termLine("✗ provisioning endpoint not available on this sidecar yet — ships with the /v1/email/init PR (#1813).", "bad");
    termStatus("bad","unavailable"); btn.disabled=false; return;
  }
  // Wrap the read in try/finally so a mid-stream drop is reported and the
  // button is always re-enabled (never left stuck disabled).
  try{
    if(res.body && res.body.getReader){
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for(;;){
        const { done, value } = await reader.read();
        if(done) break;
        buf += dec.decode(value, { stream:true });
        let nl;
        while((nl = buf.indexOf("\n")) >= 0){ emitLine(buf.slice(0, nl)); buf = buf.slice(nl + 1); }
      }
      if(buf) emitLine(buf);
    } else {
      (await res.text()).split("\n").forEach(emitLine);
    }
    const ok = res.ok;
    termLine(ok ? "✓ done" : ("✗ exited with HTTP " + res.status), ok ? "ok" : "bad");
    termStatus(ok ? "ok" : "bad", ok ? "ready" : "failed");
    if(ok) healthCheck();  // refresh stack health after provisioning
  }catch(e){
    termLine("✗ stream interrupted: " + (e && e.message), "bad");
    termStatus("bad", "failed");
  }finally{
    btn.disabled = false;
  }
}
// ---- Connectors -----------------------------------------------------------
// The sidecar always mounts /v1/email/connectors. A 404 only happens if the
// email router is mounted somewhere that didn't add them (e.g. the Agent UI) —
// then we degrade to an explainer. OAuth runs inside the connector framework
// (its own loopback callback), so the page only kicks it off and waits.
const CONN_PROVIDERS = [
  { id:"google", label:"Google · Gmail" },
  { id:"microsoft", label:"Microsoft · Outlook" },
];
function setConnStat(text, ok){
  const s = $("conn-stat"); if(!s) return;
  s.textContent = text;
  s.style.background = ok ? "var(--soft)" : "rgba(232,122,122,.14)";
  s.style.borderColor = ok ? "var(--line)" : "rgba(232,122,122,.4)";
  s.style.color = ok ? "var(--gold)" : "var(--bad)";
}
function providerBlock(p){
  const wrap = document.createElement("div"); wrap.className = "row";
  wrap.style.flexDirection = "column"; wrap.style.alignItems = "stretch";
  const head = document.createElement("div");
  head.style.display = "flex"; head.style.alignItems = "center"; head.style.gap = "10px";
  const dot = document.createElement("div");
  dot.className = "dot " + (p.connected ? "ok" : "wait"); dot.textContent = p.connected ? "✓" : "…";
  const name = document.createElement("div"); name.className = "name"; name.textContent = p.label || p.provider;
  const status = document.createElement("span"); status.className = "note"; status.style.marginLeft = "auto";
  status.textContent = p.connected ? ("connected" + (p.account_email ? (" · " + p.account_email) : "")) : "not connected";
  head.appendChild(dot); head.appendChild(name); head.appendChild(status);
  if(p.connected){
    const db = document.createElement("button"); db.className = "ghost"; db.textContent = "Disconnect";
    db.onclick = () => disconnectProvider(p.provider, db);
    head.appendChild(db);
  }
  wrap.appendChild(head);
  const out = document.createElement("div"); out.className = "out"; out.id = "cout-" + p.provider;
  wrap.appendChild(out);
  if(!p.connected){
    const grid = document.createElement("div"); grid.className = "grid2"; grid.style.marginTop = "8px";
    function field(labelText, id, type){
      const d = document.createElement("div");
      const l = document.createElement("label"); l.textContent = labelText;
      const inp = document.createElement("input"); inp.id = id; if(type) inp.type = type;
      d.appendChild(l); d.appendChild(inp); return d;
    }
    grid.appendChild(field("Client ID", "ci-" + p.provider));
    grid.appendChild(field("Client secret", "cs-" + p.provider, "password"));
    wrap.appendChild(grid);
    const act = document.createElement("div"); act.className = "actions";
    const btn = document.createElement("button"); btn.textContent = "Save & Connect";
    btn.onclick = () => connectProvider(p.provider, btn);
    act.appendChild(btn); wrap.appendChild(act);
  }
  return wrap;
}
function renderConnectors(providers){
  const host = $("conn-providers"); host.textContent = "";
  for(const p of providers) host.appendChild(providerBlock(p));
}
async function loadConnectors(){
  let data;
  try{ data = await getJSON("/v1/email/connectors"); }
  catch(e){
    if(e.status === 404){
      $("conn-unavailable").style.display = "flex"; $("conn-live").style.display = "none";
      setConnStat("not mounted here", false); populateSend([]); return;
    }
    setConnStat("error", false); return;
  }
  $("conn-unavailable").style.display = "none"; $("conn-live").style.display = "block";
  const byId = {}; for(const p of (data.providers || [])) byId[p.provider] = p;
  const merged = CONN_PROVIDERS.map((s) =>
    Object.assign({ provider:s.id, label:s.label, connected:false }, byId[s.id] || {}));
  const connected = merged.filter((p) => p.connected);
  // Consistency: only show account emails if EVERY connected mailbox has one;
  // otherwise show none, so we never mix "Gmail · addr" with a bare "Outlook".
  if(!(connected.length && connected.every((p) => p.account_email))){
    for(const p of merged) p.account_email = null;
  }
  renderConnectors(merged);
  setConnStat(connected.length ? (connected.length + " connected") : "none connected", connected.length > 0);
  populateSend(connected);
}
async function connectProvider(provider, btn){
  const out = $("cout-" + provider); out.className = "out show";
  const cid = ($("ci-" + provider).value || "").trim();
  const csec = ($("cs-" + provider).value || "").trim();
  if(!cid){ out.textContent = "✗ client_id is required"; return; }
  btn.disabled = true; out.textContent = "Starting OAuth…";
  let res;
  try{ res = await postJSON("/v1/email/connectors/" + provider + "/configure", { client_id:cid, client_secret:csec }); }
  catch(e){ out.textContent = "✗ " + (e.body || e.message); btn.disabled = false; return; }
  out.textContent = "";
  const a = document.createElement("div"); a.textContent = "A browser window should have opened for consent. If not, ";
  const link = document.createElement("a");
  link.href = res.authorization_url; link.target = "_blank"; link.rel = "noopener"; link.textContent = "open it here";
  a.appendChild(link); out.appendChild(a);
  const w = document.createElement("div"); w.className = "note";
  w.textContent = "Waiting for you to finish authorizing (up to 2 min)…"; out.appendChild(w);
  try{
    const st = await postJSON("/v1/email/connectors/" + provider + "/complete", { flow_id: res.flow_id });
    out.textContent = "✓ connected: " + (st.account_email || provider);
    loadConnectors();
  }catch(e){ out.textContent = "✗ " + (e.body || e.message); btn.disabled = false; }
}
async function disconnectProvider(provider, btn){
  const out = $("cout-" + provider); out.className = "out show"; out.textContent = "Disconnecting…";
  btn.disabled = true;
  try{
    const r = await fetch("/v1/email/connectors/" + provider, { method:"DELETE", headers:{ accept:"application/json" } });
    if(!r.ok){ throw httpError(r.status, await r.text()); }
    loadConnectors();  // re-renders: the connect form returns for that provider
  }catch(e){ out.textContent = "✗ " + (e.body || e.message); btn.disabled = false; }
}
// ---- Send (picks the connected mailbox) -----------------------------------
// The dropdown is always present; it lists whatever mailboxes are connected and
// is empty (send disabled) when none are. The chosen provider is passed to
// draft, which binds the send token to it, so send never has to guess.
function populateSend(connected){
  const sel = $("send-from"); if(!sel) return;
  sel.textContent = "";
  // List every connected mailbox so it's always selectable — send-capability is
  // surfaced per selection (and a send to a mailbox without mail-send access
  // returns an actionable error) rather than hiding the mailbox here.
  const canSendById = {};
  const sorted = connected.slice().sort((a, b) => a.provider.localeCompare(b.provider));
  for(const p of sorted){
    canSendById[p.provider] = !!p.can_send;
    const o = document.createElement("option"); o.value = p.provider;
    o.textContent = (p.label || p.provider) + (p.account_email ? (" · " + p.account_email) : "")
      + (p.can_send ? "" : " · needs mail access");
    sel.appendChild(o);
  }
  if(!connected.length){
    const o = document.createElement("option"); o.value = ""; o.textContent = "— no mailbox connected —";
    sel.appendChild(o);
  }
  function refresh(){
    const canSend = !!canSendById[sel.value];
    const hasConnected = connected.length > 0;
    if($("do-send")) $("do-send").disabled = !hasConnected;
    if($("send-stat")) $("send-stat").style.display = canSend ? "inline-block" : "none";
    if($("send-note")){
      if(!hasConnected){
        $("send-note").textContent = "Connect a mailbox in the Connectors panel above.";
      } else if(canSend){
        $("send-note").textContent = "";
      } else {
        $("send-note").textContent = "This mailbox is missing mail-send access — reconnect to enable sending.";
      }
    }
  }
  sel.onchange = refresh;
  refresh();
}
async function doSend(){
  const out = $("send-out"); out.className = "out show"; out.textContent = "Drafting…";
  const provider = $("send-from").value || undefined;
  const to = [{ email: $("send-to").value }];
  const subject = $("send-subject").value, body = $("send-body").value;
  try{
    const d = await postJSON("/v1/email/draft", { to, subject, body, provider });
    out.textContent = "Sending…";
    const s = await postJSON("/v1/email/send", { to, subject, body, confirmation_token: d.confirmation_token, provider });
    out.textContent = "✓ sent · id=" + (s.sent_id || "(ok)");
  }catch(e){ out.textContent = "✗ HTTP " + e.status + ": " + (e.body || e.message); }
}
$("recheck").onclick = healthCheck;
$("do-init").onclick = doInit;
$("do-provision").onclick = doProvision;
$("do-triage").onclick = doTriage;
$("do-triage-batch").onclick = doTriageBatch;
$("do-draft").onclick = doDraft;
$("do-send").onclick = doSend;
healthCheck();
loadConnectors();
</script>
</body>
</html>
"""


def render_playground_html() -> str:
    """Return the self-contained playground HTML (inline CSS + JS, no network)."""
    return _HTML


__all__ = ["render_playground_html"]
