// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The dashboard: three pages (Users, Developers, CI) sharing one shell, one
 * chart renderer and one set of controls. Charts are inline SVG drawn by a
 * small script; the only external fetch is the main site's Google Fonts
 * stylesheet. The look follows the GAIA website (Space Grotesk / Inter /
 * JetBrains Mono, porcelain and graphite grounds, copper accent); chart series
 * use the validated categorical palette. Client scripts are String.raw and use
 * no template literals, so they are emitted exactly as written.
 */

const CSS = String.raw`
:root{color-scheme:light;
--bg:#f5f2ec;--bg2:#eae5dc;--surface:rgba(0,0,0,.022);--surface2:rgba(0,0,0,.045);
--line:rgba(0,0,0,.19);--line2:rgba(0,0,0,.08);--border2:rgba(0,0,0,.28);--hdr:rgba(245,242,236,.8);
--ink:#242129;--ink2:#645d6a;--mute:#6b6472;--accent:#9a4930;--accent2:#7e3a25;--accent-dim:rgba(154,73,48,.45);--accent-soft:rgba(154,73,48,.1);
--bad:#b3261e;--good:#1c7c46;--warn:#9a6a00;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--neutral:#a8a79f;--shadow:0 18px 40px -22px rgba(36,33,41,.22),0 0 0 1px rgba(154,73,48,.08)}
:root[data-theme=dark]{color-scheme:dark;
--bg:#17161c;--bg2:#222128;--surface:rgba(255,255,255,.024);--surface2:rgba(255,255,255,.045);
--line:rgba(255,255,255,.15);--line2:rgba(255,255,255,.07);--border2:rgba(255,255,255,.24);--hdr:rgba(23,22,28,.78);
--ink:#f0ede7;--ink2:#aaa5b0;--mute:#a09ba6;--accent:#eba474;--accent2:#f3c1a0;--accent-dim:rgba(235,164,116,.45);--accent-soft:rgba(235,164,116,.12);
--bad:#f2787c;--good:#5cc96e;--warn:#e5b567;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--neutral:#6f6e67;--shadow:0 24px 60px -24px rgba(0,0,0,.7),0 0 0 1px rgba(235,164,116,.14)}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:130px}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased}
.num{font-variant-numeric:tabular-nums}
a{color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.mono,code{font-family:"JetBrains Mono","Fira Code",Consolas,monospace}
code{font-size:12px;background:var(--surface2);padding:1px 5px;border-radius:5px}

.top{position:sticky;top:0;z-index:20;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);background:var(--hdr);border-bottom:1px solid var(--line)}
.row{max-width:1200px;margin:0 auto;padding:0 28px;display:flex;align-items:center;gap:16px}
.row1{height:58px}.row2{height:48px;border-top:1px solid var(--line2);gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:8px;text-decoration:none}
.wm{font-family:"Space Grotesk",Inter,sans-serif;font-size:18px;font-weight:600;letter-spacing:-.02em}
.sm{font-family:"JetBrains Mono",monospace;font-size:10px;color:var(--mute)}
.badge{font-family:"JetBrains Mono",monospace;border:1px solid var(--accent-dim);border-radius:6px;padding:2px 6px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent)}
.tabs{display:flex;gap:2px;margin-left:12px;align-self:stretch}
.tabs a{display:flex;align-items:center;text-decoration:none;color:var(--ink2);padding:0 14px;font-size:14px;border-bottom:2px solid transparent;transition:color .2s}
.tabs a:hover{color:var(--ink)}.tabs a[aria-current=page]{color:var(--ink);font-weight:600;border-bottom-color:var(--accent)}
.spacer{flex:1}
.ctl{display:flex;align-items:center;gap:8px}.ctl>span{font-family:"JetBrains Mono",monospace;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{background:none;border:0;color:var(--ink2);padding:5px 10px;font:inherit;font-size:12.5px;cursor:pointer;border-right:1px solid var(--line);transition:color .2s,background-color .2s}
.seg button:last-child{border-right:0}.seg button:hover{color:var(--ink);background:var(--surface2)}
.seg button[aria-pressed=true]{background:var(--accent-soft);color:var(--accent);font-weight:600}
.icon{background:none;border:1px solid var(--line);color:var(--ink2);border-radius:9px;width:34px;height:30px;cursor:pointer;font-size:14px;transition:color .2s,border-color .2s}
.icon:hover{color:var(--accent);border-color:var(--accent-dim)}
.toggle{background:none;border:1px solid var(--line);color:var(--ink2);border-radius:9px;padding:5px 10px;font:inherit;font-size:12.5px;cursor:pointer}
.toggle[aria-pressed=true]{background:var(--accent-soft);color:var(--accent);border-color:var(--accent-dim);font-weight:600}
select{background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:5px 8px;font:inherit;font-size:12.5px;max-width:240px}

main{max-width:1200px;margin:0 auto;padding:4px 28px 64px}
section{margin-top:52px}
.head{display:flex;align-items:baseline;gap:10px;margin-bottom:2px}
h1{font-family:"Space Grotesk",Inter,sans-serif;font-size:clamp(1.9rem,1.3rem + 2vw,40px);font-weight:600;letter-spacing:-.03em;margin:36px 0 4px;line-height:1.05}
h2{font-family:"Space Grotesk",Inter,sans-serif;font-size:22px;font-weight:600;margin:0;letter-spacing:-.02em}
.lede{color:var(--ink2);margin:6px 0 22px;max-width:680px;font-size:15px;line-height:1.6}
.card{border-radius:14px;border:1px solid var(--line);background:var(--surface);min-width:0}
.kpi,.big{transition:border-color .35s,background-color .35s,box-shadow .35s}
.kpi:hover,.big:hover{border-color:var(--accent-dim);background:var(--surface2);box-shadow:var(--shadow)}
.ch{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 18px 4px}
.ch .t{font-family:"Space Grotesk",Inter,sans-serif;font-weight:600;font-size:15px;letter-spacing:-.01em;display:flex;align-items:center}
.ch .d{color:var(--ink2);font-size:12.5px;flex-basis:100%;margin-top:-6px}

.hero{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:14px}
.big{padding:24px 26px}
.kl{font-family:"JetBrains Mono",monospace;color:var(--mute);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;display:flex;align-items:center}
.bigval{font-family:"Space Grotesk",Inter,sans-serif;font-size:clamp(3rem,2rem + 3vw,64px);line-height:1;margin:12px 0 10px;letter-spacing:-.035em;font-weight:600}
.ks{color:var(--ink2);font-size:13px}.ks b{color:var(--ink);font-weight:600}
.bar2{display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px;margin:16px 0 8px;background:var(--line2)}.bar2 span{display:block;height:100%}
.grid4{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{padding:15px 17px}
.kv{font-family:"Space Grotesk",Inter,sans-serif;font-size:28px;font-weight:600;line-height:1.1;margin:8px 0 3px;letter-spacing:-.025em}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.stack>*+*{margin-top:14px}

label.chk{display:inline-flex;gap:6px;align-items:center;color:var(--ink2);font-size:12.5px;cursor:pointer}
input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px;margin:0}
.split{display:grid;grid-template-columns:minmax(0,1fr) 250px}
.cb{padding:4px 12px 8px;min-width:0;position:relative}
.side{border-left:1px solid var(--line);padding:16px 18px}
.legend{display:flex;gap:4px 14px;flex-wrap:wrap;color:var(--ink2);font-size:12px;padding:0 140px 2px 8px;min-height:18px}
.srow{break-inside:avoid}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}
.zoom{position:absolute;right:16px;top:2px;font-size:11px;color:var(--mute);display:flex;gap:8px;align-items:center}
.zoom button{background:var(--accent-soft);color:var(--accent);border:0;border-radius:7px;padding:3px 9px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer}
svg.c{width:100%;height:auto;display:block;overflow:visible;user-select:none;-webkit-user-select:none;cursor:crosshair}
.grid{stroke:var(--line2)}.axis{stroke:var(--mute);opacity:.6}
.tick{fill:var(--mute);font-size:10.5px;font-family:"JetBrains Mono",monospace}.vl{fill:var(--ink2);font-size:10.5px;font-family:"JetBrains Mono",monospace}
.hit{fill:transparent}.hit.on{fill:var(--accent-soft)}.sel{fill:var(--accent-soft);stroke:var(--accent-dim)}
.share h4{margin:0 0 8px;font-family:"JetBrains Mono",monospace;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);font-weight:500}
.share h4:not(:first-of-type){margin-top:18px}
.srow{display:grid;grid-template-columns:90px 1fr 38px;gap:8px;align-items:center;font-size:12.5px;margin:6px 0}
.srow .b{height:6px;background:var(--line2);border-radius:3px;overflow:hidden}.srow .b i{display:block;height:100%;border-radius:3px}
.srow .p{text-align:right;color:var(--ink2)}
details.tv{border-top:1px solid var(--line)}details.tv summary{cursor:pointer;color:var(--ink2);padding:9px 18px;font-size:12.5px}details.tv summary:hover{color:var(--accent)}
.scroll{max-height:340px;overflow:auto;padding:0 18px 12px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{padding:6px 10px;border-bottom:1px solid var(--line2);text-align:left;white-space:nowrap}
th{font-family:"JetBrains Mono",monospace;color:var(--mute);font-weight:500;font-size:10px;letter-spacing:.1em;text-transform:uppercase;position:sticky;top:0;background:var(--bg)}
.n{text-align:right;font-variant-numeric:tabular-nums}
.trunc{max-width:340px;overflow:hidden;text-overflow:ellipsis}
td.trunc.clipped{cursor:zoom-in}td.trunc.clipped:hover{color:var(--accent)}
td.trunc.wide{white-space:normal;max-width:none!important;overflow:visible;word-break:break-word;cursor:zoom-out}
.st{display:inline-flex;align-items:center;gap:6px}.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.pb{display:inline-block;width:56px;height:5px;border-radius:3px;background:var(--line2);vertical-align:middle;margin-right:8px;overflow:hidden}.pb i{display:block;height:100%}
.more{background:none;border:0;color:var(--accent);cursor:pointer;font:inherit;font-size:12.5px;font-weight:500;padding:10px 18px}
.good{color:var(--good)}.bad{color:var(--bad)}.warnc{color:var(--warn)}
.kpi.lv-good{box-shadow:inset 3px 0 0 var(--good)}.kpi.lv-warn{box-shadow:inset 3px 0 0 var(--warn)}.kpi.lv-bad{box-shadow:inset 3px 0 0 var(--bad)}
.lvdot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:7px;flex:none}
.attn{border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:0 0 16px;background:var(--surface);font-size:13px;display:flex;gap:8px 18px;flex-wrap:wrap;align-items:center}
.attn b{font-family:"JetBrains Mono",monospace;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute);font-weight:500}
.attn a{text-decoration:none;display:inline-flex;align-items:center;gap:2px}.attn a:hover{text-decoration:underline}
.legend span{cursor:pointer;user-select:none;border-radius:6px;padding:1px 4px;margin:-1px -4px}.legend span:hover{background:var(--surface2)}
.legend span.off{opacity:.45;text-decoration:line-through}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.chip{border:1px solid var(--line);background:none;color:var(--ink2);border-radius:999px;padding:3px 10px;font:inherit;font-size:12px;cursor:pointer}
.chip:hover{color:var(--ink);border-color:var(--accent-dim)}
.chip[aria-pressed=true]{background:var(--accent-soft);color:var(--accent);border-color:var(--accent-dim);font-weight:600}
.cover{color:var(--mute);font-size:11.5px;padding:0 8px 4px}
.banner{background:var(--surface);padding:10px 14px;border-radius:9px;color:var(--ink2);font-size:13px;margin:12px 0;border:1px solid var(--line);border-left:3px solid var(--warn)}
.empty{color:var(--mute);padding:28px 20px;text-align:center;font-size:13px}
.skel{height:180px;margin:12px;border-radius:10px;background:linear-gradient(90deg,var(--line2),var(--surface2),var(--line2));background-size:200% 100%;animation:sh 1.4s infinite}
@keyframes sh{to{background-position:-200% 0}}
.info{width:14px;height:14px;padding:0;margin-left:6px;border:0;background:none;color:var(--mute);cursor:help;display:inline-grid;place-items:center;vertical-align:middle;opacity:.75;transition:color .2s,opacity .2s;flex:none}
.info svg{width:14px;height:14px;display:block}
.info:hover,.info:focus-visible{color:var(--accent);opacity:1}
#tip{position:fixed;pointer-events:none;background:var(--bg2);color:var(--ink);border:1px solid var(--border2);border-radius:10px;padding:8px 11px;font-size:12px;line-height:1.45;box-shadow:var(--shadow);display:none;z-index:30;max-width:330px}
#tip .it{display:block;margin-bottom:3px;font-weight:600;color:var(--accent)}
footer{margin-top:56px;border-top:1px solid var(--line);padding-top:14px;color:var(--mute);font-size:12px}
footer summary{cursor:pointer;display:flex;align-items:center;gap:4px}
@media(prefers-reduced-motion:reduce){.skel{animation:none}html{scroll-behavior:auto}.kpi,.big{transition:none}}
@media(max-width:960px){.hero,.split,.grid2{grid-template-columns:1fr}.side{border-left:0;border-top:1px solid var(--line)}
.row{padding:0 16px}.row2{height:auto;padding-top:8px;padding-bottom:8px}main{padding:4px 16px 56px}.sm,.badge{display:none}.tabs{margin-left:4px}.tabs a{padding:0 9px}}
`;

const CORE_JS = String.raw`
var qs=new URLSearchParams(location.search);
var RANGES=['1','7','30','90','365','all'],GRANS=['auto','day','week','month'];
function pick(v,ok,d){return ok.indexOf(v)>=0?v:d;}
var S={range:pick(qs.get('range'),RANGES,'30'),gran:pick(qs.get('gran'),GRANS,'auto'),labels:qs.get('labels')==='1'};
var D={},Z={},SPECS={},DRAW={},HID={},ATTN=[];
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var RANGE_TEXT={'1':'last 24 hours','7':'last 7 days','30':'last 30 days','90':'last 90 days','365':'last 12 months','all':'all time'};
function fmt(n){return n==null?'—':Number(n).toLocaleString('en-US');}
function cmp(n){if(n==null)return '—';var a=Math.abs(n);return a>=1e6?(n/1e6).toFixed(a>=1e7?0:1).replace(/\.0$/,'')+'M':a>=1e4?(n/1e3).toFixed(a>=1e5?0:1).replace(/\.0$/,'')+'k':fmt(Math.round(n*100)/100);}
function pct(x,d){return x==null?'—':(x*100).toFixed(d==null?1:d)+'%';}
function dur(s){if(s==null)return '—';if(s<90)return s+'s';if(s<5400)return Math.round(s/60)+' min';return (s/3600).toFixed(1)+' h';}
function ddur(d){if(d==null)return '—';if(d<1/24)return Math.max(1,Math.round(d*1440))+' min';if(d<1)return (d*24).toFixed(d<5/24?1:0)+' h';return d.toFixed(d<10?1:0)+' days';}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function el(id){return document.getElementById(id);}
function rangeText(){return RANGE_TEXT[S.range]||('last '+S.range+' days');}
/* Daily data: the last N calendar days, today included (24h covers yesterday and today, since days are whole). */
function sinceDay(){if(S.range==='all')return '0000-00-00';var n=Math.max(2,Number(S.range));return new Date(Date.now()-(n-1)*864e5).toISOString().slice(0,10);}
function today(){return new Date().toISOString().slice(0,10);}
function dayText(){return S.range==='1'?'since yesterday':S.range==='all'?'all time':'last '+S.range+' days';}
function sinceMs(){return S.range==='all'?0:Date.now()-Number(S.range)*864e5;}
function weekKey(d){var t=Date.parse(d+'T00:00:00Z'),w=(new Date(t).getUTCDay()+6)%7;return new Date(t-w*864e5).toISOString().slice(0,10);}
function granFor(n){if(S.gran!=='auto')return S.gran;return n<=45?'day':n<=400?'week':'month';}
/* Group daily rows into day/week/month buckets. how: {field:'sum'|'last'}; a bucket whose values are all null stays null. */
function bucket(rows,how,gran){var out=[],idx={};rows.forEach(function(r){var k=gran==='day'?r.day:gran==='week'?weekKey(r.day):r.day.slice(0,7);
  var b=idx[k];if(!b){b={key:k};Object.keys(how).forEach(function(f){b[f]=null;});idx[k]=b;out.push(b);}
  Object.keys(how).forEach(function(f){var v=r[f];if(v==null)return;b[f]=how[f]==='sum'?(b[f]||0)+v:v;});});return out;}
function shortLab(k,g){var m=MON[+k.slice(5,7)-1];return g==='month'?m+' ’'+k.slice(2,4):m+' '+(+k.slice(8,10));}
function longLab(k,g){var m=MON[+k.slice(5,7)-1];if(g==='month')return m+' '+k.slice(0,4);var s=m+' '+(+k.slice(8,10))+', '+k.slice(0,4);return g==='week'?'Week of '+s:s;}
function niceTicks(lo,hi,maxN,fixed){if(!(hi>lo))hi=lo+1;var raw=(hi-lo)/Math.max(1,maxN-1),p=Math.pow(10,Math.floor(Math.log10(raw))),st=p*10,c=[1,2,2.5,5,10];
  for(var i=0;i<c.length;i++){if(c[i]*p>=raw-1e-12){st=c[i]*p;break;}}
  if(st<1&&!fixed&&hi-lo>=2)st=1;
  var a=Math.ceil(lo/st-1e-9)*st,b=fixed?hi:Math.ceil(hi/st-1e-9)*st,t=[];for(var v=a;v<=b+st/1e3;v+=st)t.push((Math.round(v/st)*st)||0);return {ticks:t,max:b};}
var tip=el('tip'),pinned=null;
function showTip(e,h){tip.innerHTML=h;tip.style.display='block';var w=tip.offsetWidth,hh=tip.offsetHeight,x=e.clientX+14,y=e.clientY+14;
  if(x+w>innerWidth-8)x=e.clientX-w-14;if(y+hh>innerHeight-8)y=e.clientY-hh-14;tip.style.left=Math.max(8,x)+'px';tip.style.top=Math.max(8,y)+'px';}
function hideTip(){tip.style.display='none';}
var GLYPH='__GLYPH__';
function info(k){return '<button type="button" class="info" data-info="'+k+'" aria-label="About: '+esc(INFO[k]?INFO[k][0]:k)+'">'+GLYPH+'</button>';}
function showInfo(b){var t=INFO[b.getAttribute('data-info')];if(!t)return;tip.innerHTML='<span class="it">'+esc(t[0])+'</span>'+esc(t[1]);tip.style.display='block';
  var r=b.getBoundingClientRect(),w=tip.offsetWidth,hh=tip.offsetHeight,y=r.bottom+8;if(y+hh>innerHeight-8)y=r.top-hh-8;tip.style.left=Math.max(8,Math.min(r.left-10,innerWidth-w-8))+'px';tip.style.top=Math.max(8,y)+'px';}
document.addEventListener('mouseover',function(e){var b=e.target.closest&&e.target.closest('.info');if(b&&!pinned)showInfo(b);});
document.addEventListener('mouseout',function(e){var b=e.target.closest&&e.target.closest('.info');if(b&&!pinned)hideTip();});
document.addEventListener('focusin',function(e){var b=e.target.closest&&e.target.closest('.info');if(b)showInfo(b);});
document.addEventListener('focusout',function(e){var b=e.target.closest&&e.target.closest('.info');if(b&&pinned!==b)hideTip();});
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('.info');if(b){e.preventDefault();e.stopPropagation();if(pinned===b){pinned=null;hideTip();}else{pinned=b;showInfo(b);}}else if(pinned){pinned=null;hideTip();}},true);
document.addEventListener('keydown',function(e){if(e.key==='Escape'){pinned=null;hideTip();}});

/* Clipped table cells: mark them, and a click expands (or collapses) the whole column. */
function markClipped(root){(root||document).querySelectorAll('td.trunc').forEach(function(td){if(td.classList.contains('wide'))return;td.classList.toggle('clipped',td.scrollWidth>td.clientWidth+1);if(td.classList.contains('clipped')&&!td.title)td.title=td.textContent;});}
document.addEventListener('click',function(e){var td=e.target.closest&&e.target.closest('td.trunc');if(!td||e.target.closest('a'))return;if(!td.classList.contains('clipped')&&!td.classList.contains('wide'))return;
  var col=td.cellIndex,on=!td.classList.contains('wide');td.closest('table').querySelectorAll('tr').forEach(function(tr){var c=tr.cells[col];if(c&&c.classList.contains('trunc'))c.classList.toggle('wide',on);});e.stopPropagation();markClipped(td.closest('table'));});
new MutationObserver(function(){clearTimeout(markClipped.t);markClipped.t=setTimeout(function(){markClipped();},60);}).observe(document.querySelector('main'),{childList:true,subtree:true});
document.addEventListener('toggle',function(){markClipped();},true);
function tableView(head,rows){return '<details class="tv"><summary>Table view</summary><div class="scroll"><table><tr>'+head.map(function(h,i){return '<th'+(i?' class="n"':'')+'>'+esc(h)+'</th>';}).join('')+'</tr>'+rows.map(function(r){return '<tr>'+r.map(function(c,i){return '<td'+(i?' class="n"':'')+'>'+c+'</td>';}).join('')+'</tr>';}).join('')+'</table></div></details>';}
/* Place value labels without overlap: largest values first, skip any that would collide. */
function placeLabels(cands,minX,maxX){var placed=[],out='';cands.sort(function(a,b){return b.v-a.v;}).forEach(function(c){var w=c.text.length*6.8+10,x0=c.x-w/2,x1=c.x+w/2;if(x0<minX||x1>maxX)return;
  for(var i=0;i<placed.length;i++){var p=placed[i];if(x0<p[1]&&x1>p[0]&&Math.abs(c.y-p[2])<16)return;}placed.push([x0,x1,c.y]);out+='<text x="'+c.x.toFixed(1)+'" y="'+c.y.toFixed(1)+'" class="vl" text-anchor="middle">'+esc(c.text)+'</text>';});return out;}
function svgPoint(svg,e){var p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
/* Drag-to-zoom: onZoom(p0,p1) receives the selected x span in svg pixels. */
function enableZoom(svg,L,R,T,B,W,H,onZoom){var start=null,rect=null;
  svg.addEventListener('mousedown',function(e){if(e.button!==0)return;var p=svgPoint(svg,e);if(p.x<L||p.x>W-R)return;start=p.x;rect=document.createElementNS('http://www.w3.org/2000/svg','rect');rect.setAttribute('class','sel');rect.setAttribute('y',T);rect.setAttribute('height',H-T-B);rect.setAttribute('x',start);rect.setAttribute('width',0);svg.appendChild(rect);window.addEventListener('mouseup',up);e.preventDefault();});
  svg.addEventListener('mousemove',function(e){if(start==null)return;var x=Math.max(L,Math.min(W-R,svgPoint(svg,e).x));rect.setAttribute('x',Math.min(start,x));rect.setAttribute('width',Math.abs(x-start));});
  var up=function(e){if(start==null)return;var x=Math.max(L,Math.min(W-R,svgPoint(svg,e).x)),a=Math.min(start,x),b=Math.max(start,x);start=null;if(rect)rect.remove();rect=null;window.removeEventListener('mouseup',up);if(b-a>8)onZoom(a,b);};
}
function zoomBar(id,zoomed){return '<div class="zoom">'+(zoomed?'<button type="button" data-unzoom="'+id+'">Reset zoom</button>':'<span>Drag to zoom</span>')+'</div>';}
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-unzoom]');if(b){var id=b.getAttribute('data-unzoom');delete Z[id];(DRAW[id]||function(){draw(id);})();}});

/* Generic time or category chart.
   spec: {keys, gran?, cats?, catName?, series:[{name,color,vals,fmt?}], type:'bar'|'line'|'area', stacked?, ymin?, ymax?, yfmt?, h?, empty?, note?, title} */
function chart(id,spec){SPECS[id]=spec;var h=HID[id];if(h){var names={};spec.series.forEach(function(x){names[x.name]=1;});Object.keys(h).forEach(function(k){if(!names[k])delete h[k];});if(spec.series.every(function(x){return h[x.name];}))delete HID[id];}draw(id);}
/* Show a message instead of a chart, and forget the old chart so a resize cannot bring it back. */
function emptyBox(id,msg){delete SPECS[id];var b=el(id);if(b)b.innerHTML='<div class="empty">'+msg+'</div>';}
function draw(id){
  var spec=SPECS[id],box=el(id);if(!box||!spec)return;
  var all=spec.keys.length;if(!all){box.innerHTML='<div class="empty">'+esc(spec.empty||'No data for this range yet.')+'</div>';return;}
  var hid=HID[id]||{},z=Z[id],a=z?z[0]:0,b=z?z[1]:all-1,n=b-a+1,keys=spec.keys.slice(a,b+1),S2=spec.series.filter(function(s){return !hid[s.name];}).map(function(s){return {name:s.name,color:s.color,fmt:s.fmt,vals:s.vals.slice(a,b+1)};});
  var lab=function(i){return spec.cats?spec.cats[a+i]:shortLab(keys[i],spec.gran);},lng=function(i){return spec.catsLong?spec.catsLong[a+i]:spec.cats?spec.cats[a+i]:longLab(keys[i],spec.gran);};
  var W=Math.max(320,Math.round(box.clientWidth-24)),H=spec.h||240,L=52,R=12,T=18,B=30,yfmt=spec.yfmt||cmp,stacked=spec.stacked&&spec.type!=='line';
  var mx=0;for(var i=0;i<n;i++){var t=0;S2.forEach(function(s){var v=s.vals[i];if(v==null)return;if(stacked)t+=v;else if(v>mx)mx=v;});if(stacked&&t>mx)mx=t;}
  var ymin=spec.ymin==null?0:spec.ymin,tk=niceTicks(ymin,spec.ymax!=null?spec.ymax:mx,Math.max(4,Math.floor((H-T-B)/26)),spec.ymax!=null),ymax=tk.max;
  var cw=(W-L-R)/n,X=function(i){return L+(i+.5)*cw;},Y=function(v){return T+(1-(v-ymin)/((ymax-ymin)||1))*(H-T-B);};
  var s='';tk.ticks.forEach(function(v){s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(v).toFixed(1)+'" y2="'+Y(v).toFixed(1)+'" class="grid"/><text x="'+(L-8)+'" y="'+(Y(v)+3.5).toFixed(1)+'" class="tick" text-anchor="end">'+yfmt(v)+'</text>';});
  var lw=0;for(i=0;i<n;i++)lw=Math.max(lw,String(lab(i)).length+(spec.cats?0:4));var every=Math.max(1,Math.ceil((lw*6.4+14)/cw)),lastYear=null;
  s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+(H-B)+'" y2="'+(H-B)+'" class="axis"/>';
  for(i=0;i<n;i++){var x=X(i).toFixed(1);if(i%every===0){var tx=lab(i);if(!spec.cats&&spec.gran!=='month'){var yr=keys[i].slice(0,4);if(lastYear&&yr!==lastYear)tx+=' ’'+yr.slice(2);lastYear=yr;}
      s+='<line x1="'+x+'" x2="'+x+'" y1="'+(H-B)+'" y2="'+(H-B+5)+'" class="axis"/><text x="'+x+'" y="'+(H-9)+'" class="tick" text-anchor="middle">'+esc(tx)+'</text>';}
    else if(cw>=4)s+='<line x1="'+x+'" x2="'+x+'" y1="'+(H-B)+'" y2="'+(H-B+3)+'" class="axis"/>';}
  var labels=[];
  if(spec.selected!=null&&spec.selected>=a&&spec.selected<=b)s+='<rect x="'+(L+(spec.selected-a)*cw).toFixed(1)+'" y="'+T+'" width="'+cw.toFixed(1)+'" height="'+(H-T-B)+'" fill="var(--accent-soft)"/>';
  if(spec.type==='area'){var acc=[];for(i=0;i<n;i++)acc.push(0);
    S2.forEach(function(se){var top=[],bot=[],k;for(k=0;k<n;k++){var v=se.vals[k]||0;bot.push(acc[k]);acc[k]+=v;top.push(acc[k]);}
      var d='M'+X(0).toFixed(1)+','+Y(top[0]).toFixed(1);for(k=1;k<n;k++)d+=' L'+X(k).toFixed(1)+','+Y(top[k]).toFixed(1);for(k=n-1;k>=0;k--)d+=' L'+X(k).toFixed(1)+','+Y(bot[k]).toFixed(1);
      s+='<path d="'+d+' Z" fill="'+se.color+'" fill-opacity=".75" stroke="'+se.color+'" stroke-width="1.2" stroke-linejoin="round"/>';});
  }else if(spec.type==='line'){
    S2.forEach(function(se,si){var d='',pen=false;for(var k=0;k<n;k++){var v=se.vals[k];if(v==null){if(!spec.connect)pen=false;continue;}d+=(pen?'L':'M')+X(k).toFixed(1)+','+Y(v).toFixed(1)+' ';pen=true;}
      s+='<path fill="none" stroke="'+se.color+'" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" d="'+d+'"/>';
      for(var q=0;q<n;q++){if(se.vals[q]==null)continue;var lone=!spec.connect&&(q===0||se.vals[q-1]==null)&&(q===n-1||se.vals[q+1]==null);
        if(n<=60||lone||spec.connect)s+='<circle cx="'+X(q).toFixed(1)+'" cy="'+Y(se.vals[q]).toFixed(1)+'" r="3" fill="'+se.color+'" stroke="var(--bg)" stroke-width="1.5"/>';}
      if(S.labels)for(q=0;q<n;q++){if(se.vals[q]!=null)labels.push({x:X(q),y:Y(se.vals[q])-8,v:se.vals[q],text:(se.fmt||yfmt)(se.vals[q])});}});
  }else{
    var gs=stacked?1:S2.length,gw=Math.max(2,Math.min(34,cw*.76)),bw=gw/gs;
    for(var c=0;c<n;c++){var ac=0;S2.forEach(function(se,si){var v=se.vals[c];if(v==null||v<=0)return;
      var x0=stacked?X(c)-gw/2:X(c)-gw/2+si*bw,w=Math.max(stacked?gw:bw-1.5,1.2),y0=stacked?Y(ac):Y(ymin),y1=stacked?Y(ac+v):Y(v);
      s+='<rect x="'+x0.toFixed(1)+'" y="'+y1.toFixed(1)+'" width="'+w.toFixed(1)+'" height="'+Math.max(y0-y1-(stacked&&ac>0?1.5:0),1).toFixed(1)+'" rx="'+(w>6?2:1)+'" fill="'+se.color+'"/>';
      if(stacked)ac+=v;else if(S.labels)labels.push({x:x0+w/2,y:y1-5,v:v,text:(se.fmt||yfmt)(v)});});
      if(stacked&&S.labels&&ac>0)labels.push({x:X(c),y:Y(ac)-5,v:ac,text:yfmt(ac)});}
  }
  s+=placeLabels(labels,L,W-R);
  if(spec.target!=null&&spec.target>=ymin&&spec.target<=ymax){var ty=Y(spec.target).toFixed(1);s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+ty+'" y2="'+ty+'" stroke="var(--accent)" stroke-width="1.2" stroke-dasharray="5 4"/><text x="'+(W-R-4)+'" y="'+(Number(ty)-5)+'" class="vl" text-anchor="end" style="fill:var(--accent)">'+esc(spec.targetLabel||'Target')+'</text>';}
  for(i=0;i<n;i++)s+='<rect class="hit" x="'+(L+i*cw).toFixed(1)+'" y="'+T+'" width="'+cw.toFixed(1)+'" height="'+(H-T-B)+'" data-i="'+i+'"/>';
  var leg='<div class="legend">'+(spec.series.length>1?spec.series.map(function(se){var off=!!hid[se.name];return '<span role="button" tabindex="0" aria-pressed="'+!off+'" title="Click to '+(off?'show':'hide')+' '+esc(se.name)+'" data-leg="'+esc(se.name)+'"'+(off?' class="off"':'')+'><i style="background:'+se.color+'"></i>'+esc(se.name)+'</span>';}).join(''):'')+'</div>';
  var tv=tableView([spec.cats?(spec.catName||'Bucket'):(spec.gran==='week'?'Week of':spec.gran==='month'?'Month':'Day')].concat(spec.series.map(function(se){return se.name;})),
    spec.keys.map(function(k,i){return [esc(spec.catsLong?spec.catsLong[i]:spec.cats?spec.cats[i]:longLab(k,spec.gran).replace('Week of ',''))].concat(spec.series.map(function(se){var v=se.vals[i];return v==null?'—':(se.fmt||fmt)(v);}));}).reverse());
  box.innerHTML=(n>3||z?zoomBar(id,!!z):'')+leg+'<svg class="c" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(spec.title||'chart')+'">'+s+'</svg>'+(spec.cover?'<div class="cover">'+esc(spec.cover)+'</div>':'')+(spec.note?'<div class="ks" style="padding:2px 8px 6px">'+spec.note+'</div>':'')+tv;
  box.querySelectorAll('[data-leg]').forEach(function(sp){var tg=function(){var nm=sp.getAttribute('data-leg'),h=HID[id]||(HID[id]={});
    if(h[nm])delete h[nm];else if(spec.series.filter(function(x){return !h[x.name];}).length>1)h[nm]=1;draw(id);};
    sp.addEventListener('click',tg);sp.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();tg();}});});
  var svg=box.querySelector('svg.c'),hits=svg.querySelectorAll('.hit');
  hits.forEach(function(r){r.addEventListener('mousemove',function(e){hits.forEach(function(h){h.classList.remove('on');});r.classList.add('on');var k=Number(r.getAttribute('data-i'));
    var h='<span class="it">'+esc(lng(k))+'</span>',tot=0;S2.forEach(function(se){var v=se.vals[k];if(v!=null)tot+=v;h+='<span style="color:'+se.color+'">■</span> '+esc(se.name)+': <b class="num">'+(v==null?'—':(se.fmt||fmt)(v))+'</b><br>';});
    if(stacked&&S2.length>1&&!spec.ymax)h+='Total: <b class="num">'+(spec.totalFmt||yfmt)(tot)+'</b>';showTip(e,h);});
    r.addEventListener('mouseleave',function(){r.classList.remove('on');hideTip();});});
  if(spec.onClick)hits.forEach(function(r){r.style.cursor='pointer';r.addEventListener('click',function(){spec.onClick(a+Number(r.getAttribute('data-i')));});});
  svg.addEventListener('dblclick',function(){delete Z[id];draw(id);});
  if(n>3)enableZoom(svg,L,R,T,B,W,H,function(p0,p1){var i0=Math.max(0,Math.floor((p0-L)/cw)),i1=Math.min(n-1,Math.floor((p1-L)/cw));if(i1-i0<1)return;Z[id]=[a+i0,a+i1];draw(id);});
}
/* Start of the bucket that contains a day, so the first week or month is whole, not clipped. */
function bucketStart(d,g){return g==='week'?weekKey(d):g==='month'?d.slice(0,7)+'-01':d;}
function daysFrom(a,b){var out=[];for(var t=Date.parse(a+'T00:00:00Z'),e=Date.parse(b+'T00:00:00Z');t<=e;t+=864e5)out.push(new Date(t).toISOString().slice(0,10));return out;}
/* A daily series spanning the whole selected range (days without data stay empty), regrouped into whole buckets.
   first: the first day that has data, so the chart can say where its coverage begins. */
function series(rows,how){
  var first=rows.length?rows[0].day:null,start=S.range==='all'?(first||today()):sinceDay(),g=granFor(daysFrom(start,today()).length);
  start=bucketStart(start,g);var by={};rows.forEach(function(r){by[r.day]=r;});
  var dense=daysFrom(start,today()).map(function(d){return by[d]||{day:d};});
  return {g:g,b:bucket(dense,how,g),first:first,start:start};}
function coverNote(x,what){return x.first&&x.first>x.start?'No '+(what||'data')+' before '+longLab(x.first,'day')+': collection began then.':'';}

/* Status levels. rule: {good, warn, higher}; 'good' | 'warn' | 'bad', or '' when there is no value. */
function level(v,rule){if(v==null||isNaN(v))return '';var hi=rule.higher!==false;
  if(hi)return v>=rule.good?'good':v>=rule.warn?'warn':'bad';return v<=rule.good?'good':v<=rule.warn?'warn':'bad';}
function worst(a,b){var o={'':0,good:1,warn:2,bad:3};return o[a]>=o[b]?a:b;}
/* A table cell's class for a value scored by the same rule as the KPI cards. */
function cellLv(v,rule){var lv=level(v,rule);return lv?' class="n '+(lv==='good'?'good':lv==='warn'?'warnc':'bad')+'"':' class="n"';}
var LVC={good:'var(--good)',warn:'var(--warn)',bad:'var(--bad)'};
/* A KPI card; amber and red ones are also listed in the page's attention bar with the reason. */
function kpi(key,label,value,sub,lv,why){if(lv==='warn'||lv==='bad')ATTN.push({key:key,label:label,value:value,lv:lv,why:why||''});
  return '<div class="card kpi'+(lv?' lv-'+lv:'')+'" id="k-'+key+'"'+(why?' title="'+esc(why)+'"':'')+'><div class="kl">'+(lv?'<span class="lvdot" style="background:'+LVC[lv]+'"></span>':'')+label+info(key)+'</div><div class="kv num'+(lv==='bad'?' bad':lv==='warn'?' warnc':'')+'">'+value+'</div><div class="ks">'+sub+'</div></div>';}
function drawAttention(){var box=el('attn');if(!box)return;
  if(!ATTN.length){box.innerHTML='<div class="attn"><span class="lvdot" style="background:var(--good)"></span>Every headline number on this page is in its healthy range.</div>';return;}
  var list=ATTN.filter(function(a){return a.lv==='bad';}).concat(ATTN.filter(function(a){return a.lv==='warn';}));
  box.innerHTML='<div class="attn"><b>Needs attention</b>'+list.map(function(a){return '<a href="#k-'+a.key+'" title="'+esc(a.why)+'"><span class="lvdot" style="background:'+LVC[a.lv]+'"></span>'+a.label+': <span class="'+(a.lv==='bad'?'bad':'warnc')+'">'+a.value+'</span></a>';}).join('')+'</div>';}

function setUrl(extra){var p=new URLSearchParams();p.set('range',S.range);if(S.gran!=='auto')p.set('gran',S.gran);if(S.labels)p.set('labels','1');
  Object.keys(extra||{}).forEach(function(k){if(extra[k])p.set(k,extra[k]);});history.replaceState(null,'','?'+p.toString());}
function api(path){return fetch('/admin'+path+(path.indexOf('?')<0?'?':'&')+'range='+S.range).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j;});});}
/* Only the newest request may update the page: a slow older response is dropped, never shown under newer labels. */
var GEN=0;
function load(jobs,render){var g=++GEN,got={},errs=[];
  return Promise.all(jobs.map(function(j){return api(j[1]).then(function(r){got[j[0]]=r;}).catch(function(e){got[j[0]]=null;errs.push('<div class="banner">Could not load '+esc(j[0])+': '+esc(e.message)+'</div>');});}))
    .then(function(){if(g!==GEN)return;Object.keys(got).forEach(function(k){D[k]=got[k];});Z={};el('errors').innerHTML=errs.join('');render();});}
function syncShell(){document.querySelectorAll('[data-range]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.range===S.range));});
  document.querySelectorAll('[data-gran]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.gran===S.gran));});el('labels').setAttribute('aria-pressed',String(S.labels));}
function drawStatus(){var o=D.status;if(!o||!o.log.length){el('status').innerHTML='No data collected yet.';el('fresh').textContent='';return;}
  var latest={};o.log.forEach(function(l){if(!latest[l.source])latest[l.source]=l;});var srcs=Object.keys(latest),bad=srcs.filter(function(k){return !latest[k].ok;}),last=o.log[0];
  el('fresh').textContent='Updated '+last.finished_at.slice(5,16).replace('T',' ')+' UTC';
  el('status').innerHTML='<details><summary>'+(bad.length?'<span class="bad">'+bad.length+' of '+srcs.length+' data sources failed on their last run</span>':'<span class="good">All '+srcs.length+' data sources ok</span>')+' · last collected '+esc(last.finished_at.replace('T',' ').slice(0,16))+' UTC'+info('collection')+'</summary>'
   +'<div class="scroll" style="padding:8px 0 0"><table><tr><th>When (UTC)</th><th>Source</th><th>Result</th></tr>'+o.log.map(function(l){return '<tr><td>'+esc(l.finished_at.replace('T',' ').slice(0,16))+'</td><td>'+esc(l.source)+'</td><td class="'+(l.ok?'':'bad')+'" style="white-space:normal">'+(l.ok?'':'Failed: ')+esc(l.detail)+'</td></tr>';}).join('')+'</table></div></details>';}
var rz;window.addEventListener('resize',function(){clearTimeout(rz);rz=setTimeout(function(){Object.keys(SPECS).forEach(function(id){draw(id);});if(DRAW.rel)DRAW.rel();},150);});
function boot(jobs,render){
  document.querySelectorAll('[data-range]').forEach(function(b){b.addEventListener('click',function(){S.range=b.dataset.range;syncShell();load(jobs(),render);});});
  document.querySelectorAll('[data-gran]').forEach(function(b){b.addEventListener('click',function(){S.gran=b.dataset.gran;Z={};render();});});
  el('labels').addEventListener('click',function(){S.labels=!S.labels;render();});
  el('theme').addEventListener('click',function(){var t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;try{localStorage.setItem('gaia-admin-theme',t);}catch(e){}});
  load(jobs(),render);}
`;

export const INFO_COMMON = String.raw`
'collection':['Data collection','A collector runs every night at 03:17 UTC and stores a snapshot in this dashboard’s own database. Each data source is logged; if one fails, its charts keep the last good data and the failure shows here instead of zeros.'],
'range':['Time range','Developer and CI headline numbers count the selected window by timestamp; numbers built from daily data (stars, package downloads) count whole calendar days. Charts show the whole days, weeks or months that overlap it. Group by Auto picks days for short ranges, weeks for up to a year and months beyond.'],
`;

const USERS_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'installs':['Installs to date','Downloads of installer and app files (.exe, .msi, .deb, .AppImage, .rpm, .dmg, .pkg, .whl and similar) attached to GitHub releases, across every release and release candidate. Read from the GitHub Releases API. GitHub counts every download, including automation and repeat downloads, so this is an upper bound on people.'],
'polls':['Not counted as installs','Older desktop apps (v0.17 to v0.22) check GitHub for updates by downloading latest.yml every time they start, and GitHub counts that as a download. Current versions check hub.amd-gaia.ai instead, so this number is mostly old installs still in use.'],
'stars':['Stars added','Stars added in the selected days, against the same number of days just before. Green: at least 90% of the earlier pace. Amber: 60 to 90%. Red: below 60%. The total is GitHub’s own count.'],
'pkg':['Package downloads','PyPI (pypistats.org, mirrors excluded) plus npm, over the selected days up to the newest day PyPI has published, against the same number of days just before. Includes CI and automated installs, so treat it as reach rather than users. Green: flat or up, or down less than 10%. Amber: down 10 to 30%. Red: down more than 30%.'],
'latest':['Latest release','The newest final release and its installs so far. Its installs keep growing for a few weeks after it ships. Coloured by days since it shipped, for a monthly release cadence. Green: within 35 days. Amber: 35 to 60. Red: more than 60.'],
'rels':['Releases','Final releases and release candidates published in the selected range, on the main product line (agent packages excluded). Coloured by release candidates per final release: many means stabilising late. Green: 3 or fewer. Amber: 4 to 6. Red: 7 or more. A proposed default; no published benchmark exists.'],
'per-release':['Downloads per release','One bar per release or release candidate at its publish date; its height is every download it has had so far, so older releases have had longer to accumulate. Files are grouped by OS and product from their names; unknown files show as Unclassified. Click a legend entry to hide or show it.'],
'share':['Install share','Each OS or product’s share of install-file downloads, for releases published in the selected range.'],
'daily':['Downloads per day','GitHub only reports running totals, so this is the change between nightly snapshots. History starts with the first snapshot; a missed night is a gap, not a zero.'],
'by-date':['Installs by release date','Every release’s installs so far, placed in the period the release came out. It shows how much each period’s releases have been downloaded since, not when the downloads happened; for that, see downloads per day.'],
'packages':['Package downloads over time','PyPI and npm downloads per period, from the registries. PyPI keeps about 180 days of daily history and npm about a year; periods before that have no bar.']};
var OS=['Windows','Linux','macOS','Cross-platform','Unclassified'],PROD=['Installer','Agent UI','CLI / TUI','Python package','Agents','Unclassified'];
var COL={Release:'var(--s1)','Release candidate':'var(--s2)',Windows:'var(--s1)',Linux:'var(--s2)',macOS:'var(--s3)','Cross-platform':'var(--s4)',
Installer:'var(--s1)','Agent UI':'var(--s2)','CLI / TUI':'var(--s3)','Python package':'var(--s4)',Agents:'var(--s5)',Unclassified:'var(--neutral)','Update checks':'var(--mute)'};
var P={mode:qs.get('mode')||'os',install:qs.get('install')!=='0',rc:qs.get('rc')!=='0'};
var TREND={good:.9,warn:.6},DL_TREND={good:.9,warn:.7};
function relSeries(r){if(P.mode==='os')return OS.map(function(k){return [k,r.byOs[k]||0];}).concat(P.install?[]:[['Update checks',r.meta]]);
  if(P.mode==='product')return PROD.map(function(k){return [k,r.byProduct[k]||0];}).concat(P.install?[]:[['Update checks',r.meta]]);
  return [[r.rc?'Release candidate':'Release',P.install?r.install:r.install+r.meta]];}
function relInRange(r){return r.main&&Date.parse(r.publishedAt)>=sinceMs()&&(P.rc||!r.rc);}
function ago(iso){var d=Math.round((Date.now()-Date.parse(iso))/864e5);return d<=0?'today':d===1?'yesterday':d+' days ago';}
/* Sum a daily field over [from, to] (inclusive day strings). */
function sumDays(rows,f,from,to){return rows.reduce(function(s,r){return r.day>=from&&r.day<=to&&r[f]!=null?s+r[f]:s;},0);}
function shift(day,n){return new Date(Date.parse(day+'T00:00:00Z')+n*864e5).toISOString().slice(0,10);}

function drawOverview(){var d=D.downloads,t=D.traction,h='';
  if(!d||d.empty){el('ov').innerHTML='<div class="card empty">'+esc(d?d.reason:'Could not load downloads.')+'</div>';return;}
  var all=0,ins=0,byOs={};d.releases.forEach(function(r){all+=r.install+r.meta;ins+=r.install;OS.forEach(function(k){byOs[k]=(byOs[k]||0)+(r.byOs[k]||0);});});
  var fin=d.releases.filter(function(r){return r.main&&!r.rc;}),last=fin[fin.length-1],ks=OS.filter(function(k){return byOs[k]>0;});
  h+='<div class="hero"><div class="card big"><div class="kl">Installs to date'+info('installs')+'</div><div class="bigval num">'+fmt(ins)+'</div>'
   +'<div class="ks">Plus '+fmt(all-ins)+' update checks from older desktop apps, not counted.'+info('polls')+'</div>'
   +'<div class="kl" style="margin-top:16px">By OS, all releases</div><div class="bar2" style="margin-top:8px">'+ks.map(function(k){return '<span style="width:'+(byOs[k]/ins*100)+'%;background:'+COL[k]+'"></span>';}).join('')+'</div>'
   +'<div class="ks">'+ks.map(function(k){return '<span style="margin-right:14px;white-space:nowrap"><i style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;background:'+COL[k]+'"></i>'+k+' '+Math.round(byOs[k]/ins*100)+'%</span>';}).join('')+'</div></div><div class="grid4">';
  var n=S.range==='all'?null:Math.max(2,Number(S.range)),from=sinceDay(),to=today();
  if(t&&t.stars.length){var cur=sumDays(t.stars,'added',from,to),prev=n?sumDays(t.stars,'added',shift(from,-n),shift(from,-1)):null,rr=prev?cur/prev:null,lv=n?level(rr,TREND):'';
    h+=kpi('stars','Stars added','+'+fmt(cur),fmt(t.starsHeadline!=null?t.starsHeadline:t.stars[t.stars.length-1].total)+' total · '+dayText()+(prev!=null?' · '+fmt(prev)+' the '+n+' days before':''),lv,
      prev!=null?'+'+fmt(cur)+' stars against +'+fmt(prev)+' in the previous '+n+' days ('+Math.round((rr||0)*100)+'% of that pace)':'');}
  else h+=kpi('stars','Stars added','—','collecting…','');
  if(t&&t.packages.length){var lastDay=t.packages.filter(function(r){return r.pypi!=null;}).map(function(r){return r.day;}).pop()||to,
      pf=n?shift(lastDay,-(n-1)):'0000',cur2=sumDays(t.packages,'pypi',pf,lastDay)+sumDays(t.packages,'npm',pf,lastDay),
      prev2=n?sumDays(t.packages,'pypi',shift(pf,-n),shift(pf,-1))+sumDays(t.packages,'npm',shift(pf,-n),shift(pf,-1)):null,r2=prev2?cur2/prev2:null;
    h+=kpi('pkg','Package downloads',fmt(cur2),(n?n+' days':'all time')+' to '+shortLab(lastDay,'day')+(prev2!=null?' · '+fmt(prev2)+' the '+n+' days before':''),n?level(r2,DL_TREND):'',
      prev2!=null?fmt(cur2)+' downloads against '+fmt(prev2)+' in the previous '+n+' days':'');}
  else h+=kpi('pkg','Package downloads','—','collecting…','');
  var age=last?(Date.now()-Date.parse(last.publishedAt))/864e5:null;
  h+=kpi('latest','Latest release',last?esc(last.tag):'—',last?'<b class="num">'+fmt(last.install)+'</b> installs · '+ago(last.publishedAt):'',level(age,{good:35,warn:60,higher:false}),age==null?'':Math.round(age)+' days since the last final release');
  var rr2=d.releases.filter(function(r){return r.main&&Date.parse(r.publishedAt)>=sinceMs();});
  var nf=rr2.filter(function(r){return !r.rc;}).length,nrc=rr2.filter(function(r){return r.rc;}).length,rcp=nf?nrc/nf:null;
  h+=kpi('rels','Releases',fmt(nf),'plus '+fmt(nrc)+' release candidates · '+rangeText(),level(rcp,{good:3,warn:6,higher:false}),rcp==null?'':(Math.round(rcp*10)/10)+' release candidates per final release');
  el('ov').innerHTML=h+'</div></div>';}

DRAW.rel=drawReleases;
function drawReleases(){var d=D.downloads,box=el('rel');
  document.querySelectorAll('[data-mode]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.mode===P.mode));});el('o-install').checked=P.install;el('o-rc').checked=P.rc;
  if(!d||d.empty){box.innerHTML='<div class="empty">'+esc(d?d.reason:'Could not load downloads.')+'</div>';el('share').innerHTML='';el('rel-table').innerHTML='';return;}
  var rel=d.releases.filter(relInRange);
  if(!rel.length){box.innerHTML='<div class="empty">No releases were published in the '+rangeText()+'. Pick a longer range.</div>';el('share').innerHTML='';el('rel-table').innerHTML='';return;}
  var hid=HID.rel||{},W=Math.max(320,Math.round(box.clientWidth-24)),H=360,L=52,R=12,T=20,B=30;
  // The axis spans the selected range (to now), so the chart moves with the range even when releases are sparse.
  var dom0=S.range==='all'?Date.parse(rel[0].publishedAt)-5*864e5:sinceMs(),dom1=Date.now()+864e5,z=Z.rel,t0=z?z[0]:dom0,t1=z?z[1]:dom1;
  var vis=rel.filter(function(r){var t=Date.parse(r.publishedAt);return t>=t0&&t<=t1;});
  var rows=vis.map(function(r){var s=relSeries(r).filter(function(x){return !hid[x[0]];});return {r:r,s:s,tot:s.reduce(function(a,x){return a+x[1];},0)};});
  var tk=niceTicks(0,Math.max.apply(null,rows.map(function(x){return x.tot;}).concat([1])),Math.floor((H-T-B)/26)),ym=tk.max;
  var X=function(ms){return L+(ms-t0)/(t1-t0)*(W-L-R);},Y=function(v){return T+(1-v/ym)*(H-T-B);};
  var bw=Math.max(4,Math.min(16,(W-L-R)/Math.max(rows.length,1)*.6)),s='';
  tk.ticks.forEach(function(v){s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(v).toFixed(1)+'" y2="'+Y(v).toFixed(1)+'" class="grid"/><text x="'+(L-8)+'" y="'+(Y(v)+3.5).toFixed(1)+'" class="tick" text-anchor="end">'+cmp(v)+'</text>';});
  s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(0)+'" y2="'+Y(0)+'" class="axis"/>';
  var spanD=(t1-t0)/864e5,pxDay=(W-L-R)/spanD;
  if(spanD<220){var steps=[1,2,7,14,28],stepD=28;for(var si=0;si<steps.length;si++){if(steps[si]*pxDay>=62){stepD=steps[si];break;}}
    var minor=stepD>=7?(pxDay>=4?1:7):0;
    for(var t=Math.ceil(t0/864e5)*864e5;t<=t1;t+=864e5){var dn=Math.round(t/864e5),dow=(new Date(t).getUTCDay()+6)%7,x=X(t).toFixed(1),k=new Date(t).toISOString().slice(0,10);
      var major=stepD<7?dn%stepD===0:(dow===0&&Math.round((t-Date.UTC(2024,0,1))/864e5/7)%(stepD/7)===0);
      if(major)s+='<line x1="'+x+'" x2="'+x+'" y1="'+Y(0)+'" y2="'+(Y(0)+5)+'" class="axis"/><text x="'+x+'" y="'+(H-9)+'" class="tick" text-anchor="middle">'+shortLab(k,'day')+'</text>';
      else if(minor&&(minor===1||dow===0))s+='<line x1="'+x+'" x2="'+x+'" y1="'+Y(0)+'" y2="'+(Y(0)+3)+'" class="axis"/>';}}
  else{var ppm=(W-L-R)/(spanD/30.4),stepM=ppm>=58?1:ppm>=29?2:ppm>=19?3:6,m=new Date(t0);m=new Date(Date.UTC(m.getUTCFullYear(),m.getUTCMonth(),1));
    while(m.getTime()<=t1){if(m.getTime()>=t0){var xm=X(m.getTime()).toFixed(1);
      if(m.getUTCMonth()%stepM===0)s+='<line x1="'+xm+'" x2="'+xm+'" y1="'+Y(0)+'" y2="'+(Y(0)+5)+'" class="axis"/><text x="'+xm+'" y="'+(H-9)+'" class="tick" text-anchor="middle">'+shortLab(m.toISOString().slice(0,10),'month')+'</text>';
      else s+='<line x1="'+xm+'" x2="'+xm+'" y1="'+Y(0)+'" y2="'+(Y(0)+3)+'" class="axis"/>';}m=new Date(Date.UTC(m.getUTCFullYear(),m.getUTCMonth()+1,1));}}
  var used={},labels=[];
  rel.forEach(function(r){relSeries(r).forEach(function(sg){if(sg[1]>0)used[sg[0]]=1;});});
  rows.forEach(function(row,i){var x=X(Date.parse(row.r.publishedAt))-bw/2,acc=0;
    row.s.forEach(function(sg){if(sg[1]<=0)return;var y=Y(acc+sg[1]);s+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+Math.max(Y(acc)-y-(acc>0?1.5:0),1).toFixed(1)+'" rx="2" fill="'+COL[sg[0]]+'"/>';acc+=sg[1];});
    s+='<rect class="hit" x="'+(x-3).toFixed(1)+'" y="'+T+'" width="'+(bw+6).toFixed(1)+'" height="'+(H-T-B)+'" data-i="'+i+'"/>';
    if(S.labels)labels.push({x:x+bw/2,y:Y(row.tot)-6,v:row.tot,text:row.r.tag+' · '+cmp(row.tot)});});
  s+=placeLabels(labels,L-30,W-R+30);
  var order=OS.concat(PROD,['Release','Release candidate','Update checks']),keys=Object.keys(used).sort(function(a,b){return order.indexOf(a)-order.indexOf(b);});
  box.innerHTML=zoomBar('rel',!!z)+'<div class="legend">'+(keys.length>1?keys.map(function(k){return '<span role="button" tabindex="0" aria-pressed="'+!hid[k]+'" data-leg="'+esc(k)+'" title="Click to '+(hid[k]?'show':'hide')+' '+esc(k)+'"'+(hid[k]?' class="off"':'')+'><i style="background:'+COL[k]+'"></i>'+esc(k)+'</span>';}).join(''):'')+'</div><svg class="c" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Downloads per release">'+s+'</svg>';
  box.querySelectorAll('[data-leg]').forEach(function(sp){var tg=function(){var nm=sp.getAttribute('data-leg'),h=HID.rel||(HID.rel={});if(h[nm])delete h[nm];else if(keys.filter(function(k){return !h[k];}).length>1)h[nm]=1;drawReleases();};
    sp.addEventListener('click',tg);sp.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();tg();}});});
  var svg=box.querySelector('svg.c'),hits=svg.querySelectorAll('.hit');
  hits.forEach(function(r){r.addEventListener('mousemove',function(e){var row=rows[Number(r.getAttribute('data-i'))];
    var h='<span class="it">'+esc(row.r.tag)+(row.r.rc?' · release candidate':'')+'</span>Published '+longLab(row.r.publishedAt.slice(0,10),'day')+'<br><b class="num">'+fmt(row.tot)+'</b> downloads<br>';
    row.s.forEach(function(sg){if(sg[1]>0)h+='<span style="color:'+COL[sg[0]]+'">■</span> '+esc(sg[0])+': <b class="num">'+fmt(sg[1])+'</b><br>';});showTip(e,h);});r.addEventListener('mouseleave',hideTip);});
  svg.addEventListener('dblclick',function(){delete Z.rel;drawReleases();});
  enableZoom(svg,L,R,T,B,W,H,function(p0,p1){var a=t0+(p0-L)/(W-L-R)*(t1-t0),b=t0+(p1-L)/(W-L-R)*(t1-t0);if(b-a<2*864e5)return;Z.rel=[a,b];drawReleases();});
  var os={},pr={},ti=0;vis.forEach(function(r){ti+=r.install;OS.forEach(function(k){os[k]=(os[k]||0)+(r.byOs[k]||0);});PROD.forEach(function(k){pr[k]=(pr[k]||0)+(r.byProduct[k]||0);});});
  function lst(title,keys2,map){return '<h4>'+title+'</h4>'+keys2.filter(function(k){return map[k]>0;}).sort(function(a,b){return map[b]-map[a];}).map(function(k){var p=map[k]/ti;
    return '<div class="srow"><span>'+esc(k)+'</span><span class="b"><i style="width:'+(p*100)+'%;background:'+COL[k]+'"></i></span><span class="p num">'+(p<.01?'<1':Math.round(p*100))+'%</span></div>';}).join('');}
  el('share').innerHTML=ti?'<div class="kl">Install share'+info('share')+'</div><div class="ks" style="margin:2px 0 12px">Releases published in the '+(Z.rel?'zoomed window':rangeText())+'</div>'+lst('By OS',OS,os)+lst('By product',PROD,pr)+'<div class="ks" style="margin-top:14px">'+fmt(ti)+' installs from '+vis.length+' release'+(vis.length===1?'':'s')+'</div>':'';
  el('rel-table').innerHTML=tableView(['Release','Published','Installs','Update checks'].concat(OS.slice(0,4)),vis.slice().reverse().map(function(r){return [esc(r.tag)+(r.rc?' <span class="mono" style="color:var(--s2);font-size:10px">RC</span>':''),esc(r.publishedAt.slice(0,10)),fmt(r.install),fmt(r.meta)].concat(OS.slice(0,4).map(function(o){return fmt(r.byOs[o]||0);}));}));}

function drawByDate(){var d=D.downloads;if(!d||d.empty){emptyBox('by-date',d?esc(d.reason):'Could not load downloads.');return;}
  var rows=d.releases.filter(function(r){return r.main&&(P.rc||!r.rc);}).map(function(r){var o={day:r.publishedAt.slice(0,10)};OS.forEach(function(k){o[k]=r.byOs[k]||0;});o.meta=r.meta;return o;});
  var merged={};rows.forEach(function(r){var m=merged[r.day]||(merged[r.day]={day:r.day,meta:0});OS.forEach(function(k){m[k]=(m[k]||0)+r[k];});m.meta+=r.meta;});
  var list=Object.keys(merged).sort().map(function(k){return merged[k];}),how={meta:'sum'};OS.forEach(function(k){how[k]='sum';});
  var x=series(list,how),K=x.b.map(function(r){return r.key;});
  var se=OS.filter(function(k){return list.some(function(r){return r[k]>0;});}).map(function(k){return {name:k,color:COL[k],vals:x.b.map(function(r){return r[k];})};});
  if(!P.install)se.push({name:'Update checks',color:'var(--mute)',vals:x.b.map(function(r){return r.meta;})});
  chart('by-date',{keys:K,gran:x.g,type:'bar',stacked:true,title:'Installs by release date',h:220,series:se,empty:'No releases in this range.'});}

function drawDaily(){var d=D.downloads;if(!d||d.empty){emptyBox('daily',d?esc(d.reason):'Could not load downloads.');return;}
  if((d.daily||[]).length<1){emptyBox('daily','First day of counts arrives after tomorrow’s snapshot (collection began '+esc(longLab(d.firstSnapshotDay,'day'))+').');return;}
  var x=series(d.daily,{install:'sum',meta:'sum'});
  var se=[{name:'Installs',color:'var(--s1)',vals:x.b.map(function(r){return r.install;})}];if(!P.install)se.push({name:'Update checks',color:'var(--mute)',vals:x.b.map(function(r){return r.meta;})});
  chart('daily',{keys:x.b.map(function(r){return r.key;}),gran:x.g,series:se,type:'bar',stacked:true,title:'Downloads per day',h:200,cover:coverNote(x,'daily counts'),
    note:d.negativeDays.length?'Counts fell on '+d.negativeDays.map(esc).join(', ')+' because a file or release was deleted; those days leave the drop out.':''});}

function drawPackages(){var t=D.traction;if(!t||!t.packages.length){emptyBox('packages','No package history collected yet.');return;}
  var p=series(t.packages,{pypi:'sum',npm:'sum'});
  chart('packages',{keys:p.b.map(function(r){return r.key;}),gran:p.g,type:'bar',stacked:true,title:'Package downloads',h:220,cover:p.first&&p.first>p.start?'No data before '+longLab(p.first,'day')+': the registries keep about 180 days (PyPI) to a year (npm).':'',
    series:[{name:'PyPI',color:'var(--s1)',vals:p.b.map(function(r){return r.pypi;})},{name:'npm',color:'var(--s2)',vals:p.b.map(function(r){return r.npm;})}],empty:'No package downloads in this range yet.'});}

function render(){ATTN=[];setUrl({mode:P.mode!=='os'?P.mode:'',install:P.install?'':'0',rc:P.rc?'':'0'});
  syncShell();drawOverview();drawReleases();drawByDate();drawDaily();drawPackages();drawAttention();drawStatus();}
document.querySelectorAll('[data-mode]').forEach(function(b){b.addEventListener('click',function(){P.mode=b.dataset.mode;delete HID.rel;render();});});
el('o-install').addEventListener('change',function(e){P.install=e.target.checked;render();});
el('o-rc').addEventListener('change',function(e){P.rc=e.target.checked;delete Z.rel;render();});
boot(function(){return [['downloads','/api/downloads'],['traction','/api/traction'],['status','/api/status']];},render);
`;

const DEV_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'k-merged':['Pull requests merged','PRs merged in the selected window, by merge time, and the weekly pace against the window before. Coloured by the worse of two checks. Merged against opened: green at least 80%, amber 60 to 80%, red below. Pace: green within 20% of the window before, amber down 20 to 40%, red down more (week-to-week swings of 20% are normal at this volume).'],
'k-closed':['Issues closed','Issues closed against issues opened in the window (a closure ratio, as CHAOSS defines for change requests; the cut-offs are a proposed default). A bulk clean-up can lift it for a week. Green: 90% or more. Amber: 70 to 90%. Red: below 70%.'],
'k-open':['Open issues','Issues open now, and how the backlog changed over the window. Green: shrinking or flat. Amber: grew up to 10%. Red: grew more than 10%.'],
'k-prs':['Open pull requests','PRs open now, and how many have waited more than 14 days. Green: 10% or fewer are that old. Amber: up to 25%. Red: more than 25%, which means review is not keeping up.'],
'k-commits':['Commits','Commits on the default branch in the window, and the people who made them (by GitHub login), against the window before. Green: at least 75% as many active contributors as before. Amber: 50 to 75%. Red: below 50%.'],
'k-ttm':['Time to merge','Median and 90th-percentile time from a PR opening to merging, for PRs merged in the window. Bands from LinearB’s 2026 benchmarks (8.1 million PRs): elite under 25 hours, good up to 72. Green: median under 25 hours and P90 under 3 days. Amber: median up to 3 days, P90 up to 161 hours. Red: slower.'],
'k-ttc':['Time to close','Median and 90th-percentile time from an issue opening to closing, for issues closed in the window. No published benchmark exists, so these are a proposed default; a clean-up of old issues raises it for a while. Green: median within 14 days and P90 within 90. Amber: median up to 30 days. Red: median over 30 days or P90 over 180.'],
'issues':['Issues opened and closed','Per period. When closed bars are taller than opened, the backlog shrank. Click a legend entry to hide or show it.'],
'backlog':['Open issues','Issues open at the end of each period (opened so far minus closed so far). Pull requests are excluded.'],
'ttm-h':['How long PRs take to merge','Each bar counts PRs merged in the selected window that took that long from opened to merged. A long tail to the right means some work waits a long time for review.'],
'ttc-h':['How long issues stay open','Each bar counts issues closed in the selected window by how long they were open. Issues still open are not included.'],
'prs':['Pull requests opened and merged','Per period. A PR closed without merging counts as opened but not merged.'],
'pr-backlog':['Open pull requests','Pull requests open at the end of each period: opened so far, minus merged or closed. A rising line means review is falling behind.'],
'commits':['Commits','Commits on the default branch per period, by author date, including merge commits. Follows the people filter.'],
'people':['Contributors','Active is the number of people who committed in the 28 days up to that point. New is people making their first commit to the project. Follows the people filter.'],
'loc':['Lines of code changed','Lines added and deleted on the default branch per period, from GitHub’s code-frequency statistics (weeks start on Sunday on GitHub; shown from the following Monday). With people hidden, it sums GitHub’s per-contributor statistics instead, which cover the top 100 contributors. Lockfiles and generated files count too, so read spikes with care.'],
'split':['Core team and outside contributors','The same pull-request numbers for the core team (maintainer accounts plus organisation members and collaborators) and for everyone else, bots left out. Ignores the people filter above. Merge time uses LinearB’s bands, as above. The outside row is also compared with the core team’s: outside PRs need more review, so some gap is normal. Green: outside median within 2 times the core team’s. Amber: 2 to 4 times. Red: more.'],
'who':['Contributors in the range','Everyone who committed, opened a PR or opened an issue in the selected window. Lines come from GitHub’s weekly statistics, so they cover whole weeks. Untick a person, or use the buttons, to leave them out of every number on this page.'],
'community':['Stars and forks','Rebuilt from GitHub’s dated stargazer and fork lists, so the full history is available. Total is the running count; New is how many were added in each period.']};
var MINE=__MAINTAINERS__;
var P={pm:qs.get('pm')||'active',comm:qs.get('comm')||'stars',cmode:qs.get('cmode')||'total',bots:qs.get('bots')!=='0',
  exclude:(qs.get('exclude')!==null?qs.get('exclude'):(function(){try{return localStorage.getItem('gaia-admin-exclude')||'';}catch(e){return '';}})()).split(',').filter(Boolean)};
var HB=[[0,1/24,'<1h'],[1/24,6/24,'1–6h'],[6/24,1,'6–24h'],[1,3,'1–3d'],[3,7,'3–7d'],[7,14,'1–2w'],[14,30,'2–4w'],[30,90,'1–3mo'],[90,1e9,'3mo+']];
function peopleQuery(){return 'exclude='+encodeURIComponent(P.exclude.join(','))+'&bots='+(P.bots?'1':'0');}
function savePeople(){try{localStorage.setItem('gaia-admin-exclude',P.exclude.join(','));}catch(e){}}
function hist(id,vals,name,color,what,cap){var c=HB.map(function(){return 0;});vals.forEach(function(v){for(var i=0;i<HB.length;i++){if(v>=HB[i][0]&&v<HB[i][1]){c[i]++;break;}}});
  if(!vals.length){delete SPECS[id];el(id).innerHTML='<div class="empty">Nothing in this range.</div>';return;}
  chart(id,{keys:HB.map(function(b){return b[2];}),cats:HB.map(function(b){return b[2];}),catName:'Took',type:'bar',title:name,h:210,series:[{name:what,color:color,vals:c}],note:cap});}
function wk(n){var d=S.range==='all'?0:Number(S.range);return d>=7?fmt(Math.round(n*7/d))+' a week':'';}
function pace(c,p){return p?c/p-1:null;}
var PACE={good:-.2,warn:-.4},MERGE_MED={good:25/24,warn:3,higher:false},MERGE_P90={good:3,warn:161/24,higher:false};
function drawKpis(){var m=D.momentum;if(!m){el('ov').innerHTML='';return;}var s=m.summary,p=m.previous,rt=rangeText(),h='';
  var mr=s.prsOpened?s.prsMerged/s.prsOpened:null,mp=p?pace(s.prsMerged,p.prsMerged):null;
  h+=kpi('k-merged','PRs merged',fmt(s.prsMerged),(wk(s.prsMerged)?wk(s.prsMerged)+(p?' (was '+wk(p.prsMerged).replace(' a week','')+')':'')+' · ':'')+fmt(s.prsOpened)+' opened · '+rt,worst(level(mr,{good:.8,warn:.6}),level(mp,PACE)),(mr==null?'':Math.round(mr*100)+'% as many merged as opened')+(mp==null?'':', pace '+(mp>=0?'up ':'down ')+Math.abs(Math.round(mp*100))+'%'));
  var cr=s.issuesOpened?s.issuesClosed/s.issuesOpened:null;
  h+=kpi('k-closed','Issues closed',fmt(s.issuesClosed),(wk(s.issuesClosed)?wk(s.issuesClosed)+(p?' (was '+wk(p.issuesClosed).replace(' a week','')+')':'')+' · ':'')+fmt(s.issuesOpened)+' opened · '+(cr==null?'':Math.round(cr*100)+'% closure · ')+rt,level(cr,{good:.9,warn:.7}),cr==null?'':'closed '+Math.round(cr*100)+'% of the issues opened');
  var start=p?p.openIssues:null,gr=start?(s.openIssues-start)/start:null;
  h+=kpi('k-open','Open issues',fmt(s.openIssues),(start==null?'now':(s.openIssues>=start?'+':'')+fmt(s.openIssues-start)+' over '+rt)+(m.botOpenIssues&&P.bots?' · '+fmt(m.botOpenIssues)+' filed by bots':''),level(gr,{good:0,warn:.1,higher:false}),gr==null?'':'backlog '+(gr>=0?'grew ':'shrank ')+Math.abs(Math.round(gr*100))+'% over the window');
  var stale=s.openPrs?s.staleOpenPrs/s.openPrs:null;
  h+=kpi('k-prs','Open PRs',fmt(s.openPrs),fmt(s.staleOpenPrs)+' open more than 14 days',level(stale,{good:.1,warn:.25,higher:false}),stale==null?'':Math.round(stale*100)+'% of open PRs are older than 14 days');
  var ac=p&&p.contributors?s.contributors/p.contributors:null;
  h+=kpi('k-commits','Commits',fmt(s.commits),fmt(s.contributors)+' contributors'+(p?' (were '+fmt(p.contributors)+')':'')+' · '+rt,level(ac,{good:.75,warn:.5}),ac==null?'':fmt(s.contributors)+' active contributors against '+fmt(p.contributors)+' in the window before');
  var lm=worst(level(s.medianDaysToMerge,MERGE_MED),level(s.p90DaysToMerge,MERGE_P90));
  h+=kpi('k-ttm','Time to merge',ddur(s.medianDaysToMerge),'P90 '+ddur(s.p90DaysToMerge)+' · '+fmt(s.prsMerged)+' merged PRs',lm,'median '+ddur(s.medianDaysToMerge)+', 90th percentile '+ddur(s.p90DaysToMerge));
  h+=kpi('k-ttc','Time to close',ddur(s.medianDaysToClose),'P90 '+ddur(s.p90DaysToClose)+' · '+fmt(s.issuesClosed)+' closed issues',worst(level(s.medianDaysToClose,{good:14,warn:30,higher:false}),level(s.p90DaysToClose,{good:90,warn:180,higher:false})==='bad'?'bad':''),'median '+ddur(s.medianDaysToClose)+', P90 '+ddur(s.p90DaysToClose)+' (healthy: median within 14 days)');
  el('ov').innerHTML='<div class="strip">'+h+'</div>';drawSplit();}
function drawSplit(){var m=D.momentum,box=el('split');if(!box)return;if(!m||!m.split){box.innerHTML='';return;}var x=m.split;
  var row=function(name,c,pv,ratio){var mp=pv?pace(c.prsMerged,pv.prsMerged):null;
    return '<tr><td>'+name+'</td><td class="n">'+(wk(c.prsMerged)?fmt(Math.round(c.prsMerged*7/Number(S.range))):fmt(c.prsMerged))+'</td><td'+cellLv(mp,PACE)+'>'+(mp==null?'—':(mp>=0?'+':'')+Math.round(mp*100)+'%')+'</td><td'+cellLv(c.medianDaysToMerge,MERGE_MED)+'>'+ddur(c.medianDaysToMerge)+'</td><td'+cellLv(c.p90DaysToMerge,MERGE_P90)+'>'+ddur(c.p90DaysToMerge)+'</td><td'+cellLv(ratio,{good:2,warn:4,higher:false})+'>'+(ratio==null?'—':ratio.toFixed(1)+'×')+'</td><td class="n">'+fmt(c.openPrs)+'</td><td class="n">'+fmt(c.issuesOpened)+'</td><td class="n">'+fmt(c.openIssues)+'</td></tr>';};
  var ratio=x.core.medianDaysToMerge&&x.outside.medianDaysToMerge!=null?x.outside.medianDaysToMerge/x.core.medianDaysToMerge:null,per=wk(1)?'a week':'in the range';
  if(ratio!=null){var rl=level(ratio,{good:2,warn:4,higher:false});if(rl==='warn'||rl==='bad')ATTN.push({key:'split',label:'Outside PRs wait',value:ratio.toFixed(1)+'× longer',lv:rl,why:'outside contributors’ PRs take '+ratio.toFixed(1)+' times as long to merge as the core team’s'});}
  box.innerHTML='<div class="scroll" style="max-height:none"><table><tr><th>Who</th><th class="n">PRs merged '+per+'</th><th class="n">Pace vs before</th><th class="n">Merge median</th><th class="n">Merge P90</th><th class="n">Wait vs core</th><th class="n">Open PRs</th><th class="n">Issues opened</th><th class="n">Open issues</th></tr>'
    +row('Core team',x.core,x.corePrevious,null)+row('Outside contributors',x.outside,x.outsidePrevious,ratio)+'</table></div>';}
function drawPeople(){var m=D.momentum;
  var chips=(MINE.length?'<button type="button" class="chip" id="hide-mine" title="'+esc(MINE.join(', '))+'" aria-pressed="'+MINE.every(function(x){return P.exclude.indexOf(x)>=0;})+'">Hide maintainers</button>':'')
   +'<button type="button" class="chip" id="hide-bots" aria-pressed="'+!P.bots+'">Hide bots</button>'
   +P.exclude.map(function(x){return '<button type="button" class="chip" data-unex="'+esc(x)+'" title="Show '+esc(x)+' again">'+esc(x)+' ×</button>';}).join('');
  el('people-bar').innerHTML='<span class="kl" style="margin-right:4px">People</span>'+chips+(P.exclude.length||!P.bots?'<span class="ks">Hidden people are left out of every number on this page.</span>':'<span class="ks">Showing everyone.</span>');
  if(el('hide-mine'))el('hide-mine').onclick=function(){var all=MINE.every(function(x){return P.exclude.indexOf(x)>=0;});P.exclude=all?P.exclude.filter(function(x){return MINE.indexOf(x)<0;}):P.exclude.concat(MINE.filter(function(x){return P.exclude.indexOf(x)<0;}));savePeople();reload();};
  el('hide-bots').onclick=function(){P.bots=!P.bots;reload();};
  el('people-bar').querySelectorAll('[data-unex]').forEach(function(b){b.onclick=function(){var x=b.getAttribute('data-unex');P.exclude=P.exclude.filter(function(y){return y!==x;});savePeople();reload();};});
  if(!m){el('who').innerHTML='';return;}
  var ex={};P.exclude.forEach(function(x){ex[x.toLowerCase()]=1;});
  var rows=m.contributors.filter(function(r){return r.commits||r.prsOpened||r.issuesOpened;});
  el('who').innerHTML=rows.length?'<div class="scroll" style="max-height:420px"><table><tr><th>Show</th><th>Person</th><th class="n">Commits</th><th class="n">PRs opened</th><th class="n">PRs merged</th><th class="n">Issues opened</th><th class="n">Lines +</th><th class="n">Lines −</th><th>First commit</th></tr>'
   +rows.map(function(r){var off=ex[r.author.toLowerCase()]||(r.bot&&!P.bots);return '<tr'+(off?' style="opacity:.5"':'')+'><td><input type="checkbox" data-person="'+esc(r.author)+'"'+(off?'':' checked')+(r.bot&&!P.bots?' disabled title="Bots are hidden"':'')+' aria-label="Include '+esc(r.author)+'"></td><td class="trunc mono" style="max-width:220px;font-size:12px">'+(/^[A-Za-z0-9]/.test(r.author)?'<a href="https://github.com/'+encodeURIComponent(r.author.replace(/\[bot\]$/,''))+'" target="_blank" rel="noopener">'+esc(r.author)+'</a>':esc(r.author))+(r.bot?' <span class="badge">bot</span>':'')+'</td>'
     +'<td class="n">'+fmt(r.commits)+'</td><td class="n">'+fmt(r.prsOpened)+'</td><td class="n">'+fmt(r.prsMerged)+'</td><td class="n">'+fmt(r.issuesOpened)+'</td><td class="n good">'+(r.additions?'+'+cmp(r.additions):'')+'</td><td class="n bad">'+(r.deletions?'−'+cmp(r.deletions):'')+'</td><td>'+(r.firstCommit?esc(r.firstCommit):'—')+'</td></tr>';}).join('')+'</table></div>'
   :'<div class="empty">Nobody contributed in this range.</div>';
  el('who').querySelectorAll('[data-person]').forEach(function(cb){cb.onchange=function(){var x=cb.getAttribute('data-person');P.exclude=cb.checked?P.exclude.filter(function(y){return y.toLowerCase()!==x.toLowerCase();}):P.exclude.concat([x]);savePeople();reload();};});}
function drawCharts(){var m=D.momentum;
  document.querySelectorAll('[data-pm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.pm===P.pm));});
  if(!m||!m.daily.length){['issues','backlog','prs','pr-backlog','commits','people','ttm-h','ttc-h','loc'].forEach(function(i){emptyBox(i,'No history collected yet.');});return;}
  var x=series(m.daily,{issuesOpened:'sum',issuesClosed:'sum',openIssues:'last',openPrs:'last',prsOpened:'sum',prsMerged:'sum',commits:'sum',activeContributors28d:'last',newContributors:'sum'});
  var K=x.b.map(function(r){return r.key;}),col=function(f){return x.b.map(function(r){return r[f];});},minOf=function(a){var v=a.filter(function(z){return z!=null;});return v.length?Math.min.apply(null,v):0;};
  chart('issues',{keys:K,gran:x.g,type:'bar',title:'Issues opened and closed',h:210,series:[{name:'Opened',color:'var(--s2)',vals:col('issuesOpened')},{name:'Closed',color:'var(--s3)',vals:col('issuesClosed')}]});
  var ob=col('openIssues');chart('backlog',{keys:K,gran:x.g,type:'line',title:'Open issues',h:210,series:[{name:'Open issues',color:'var(--s1)',vals:ob}],ymin:Math.max(0,Math.floor(minOf(ob)*0.9))});
  var s=m.summary;
  hist('ttm-h',s.mergeDurations,'Time to merge','var(--s1)','Pull requests','Median '+ddur(s.medianDaysToMerge)+' · P90 '+ddur(s.p90DaysToMerge));
  hist('ttc-h',s.closeDurations,'Time to close','var(--s3)','Issues','Median '+ddur(s.medianDaysToClose)+' · P90 '+ddur(s.p90DaysToClose));
  chart('prs',{keys:K,gran:x.g,type:'bar',title:'Pull requests opened and merged',h:210,series:[{name:'Opened',color:'var(--s2)',vals:col('prsOpened')},{name:'Merged',color:'var(--s3)',vals:col('prsMerged')}]});
  var op=col('openPrs');chart('pr-backlog',{keys:K,gran:x.g,type:'line',title:'Open pull requests',h:210,series:[{name:'Open pull requests',color:'var(--s2)',vals:op}],ymin:Math.max(0,Math.floor(minOf(op)*0.8))});
  chart('commits',{keys:K,gran:x.g,type:'bar',title:'Commits',h:210,series:[{name:'Commits',color:'var(--s1)',vals:col('commits')}]});
  if(P.pm==='active')chart('people',{keys:K,gran:x.g,type:'line',title:'Active contributors',h:210,ymin:0,series:[{name:'Active in the last 28 days',color:'var(--s3)',vals:col('activeContributors28d')}]});
  else chart('people',{keys:K,gran:x.g,type:'bar',title:'New contributors',h:210,series:[{name:'First-time committers',color:'var(--s5)',vals:col('newContributors')}]});
  var loc=(m.loc||[]).map(function(w){return {day:w.week,additions:w.additions,deletions:w.deletions};}),lx=series(loc,{additions:'sum',deletions:'sum'});
  var weeklyOnly=lx.g==='day';if(weeklyOnly){lx=(function(){var g='week',start=bucketStart(S.range==='all'?(loc[0]?loc[0].day:today()):sinceDay(),g),rows=loc.filter(function(r){return r.day>=start;});return {g:g,b:bucket(rows,{additions:'sum',deletions:'sum'},g),first:loc[0]?loc[0].day:null,start:start};})();}
  chart('loc',{keys:lx.b.map(function(r){return r.key;}),gran:lx.g,type:'bar',title:'Lines changed',h:230,yfmt:cmp,
    series:[{name:'Added',color:'var(--good)',vals:lx.b.map(function(r){return r.additions;}),fmt:function(v){return '+'+fmt(v);}},{name:'Deleted',color:'var(--bad)',vals:lx.b.map(function(r){return r.deletions;}),fmt:function(v){return '−'+fmt(v);}}],
    note:[weeklyOnly?'GitHub reports lines per week, so this chart is weekly even with Group by Day.':'',m.locFiltered?'Hidden people left out (from GitHub’s per-contributor statistics).':''].filter(Boolean).join(' '),empty:'GitHub has not computed line statistics yet; they arrive with the next collection.'});}
function drawCommunity(){var t=D.traction;
  document.querySelectorAll('[data-comm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.comm===P.comm));});
  document.querySelectorAll('[data-cmode]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.cmode===P.cmode));});
  if(!t||!t.stars.length){emptyBox('community','No history collected yet.');return;}
  var src=P.comm==='stars'?t.stars:t.forks,name=P.comm==='stars'?'Stars':'Forks',c=P.comm==='stars'?'var(--s1)':'var(--s3)';
  var x=series(src,{added:'sum',total:'last'}),K=x.b.map(function(r){return r.key;});
  var hl=P.comm==='forks'?t.forksHeadline:t.starsHeadline,last=src.length?src[src.length-1].total:null,note=hl!=null&&last!=null&&hl!==last?'GitHub’s own count is '+fmt(hl)+'; its dated list returns '+fmt(last)+'. The chart follows the dated list because it carries the history.':'';
  if(P.cmode==='total'){var tt=x.b.map(function(r){return r.total;}),v=tt.filter(function(z){return z!=null;});chart('community',{keys:K,gran:x.g,type:'line',title:name,h:220,note:note,series:[{name:'Total '+name.toLowerCase(),color:c,vals:tt}],ymin:v.length?Math.max(0,Math.floor(Math.min.apply(null,v)*0.9)):0});}
  else chart('community',{keys:K,gran:x.g,type:'bar',title:name,h:220,series:[{name:'New '+name.toLowerCase(),color:c,vals:x.b.map(function(r){return r.added;})}]});}
function render(){ATTN=[];setUrl({pm:P.pm!=='active'?P.pm:'',comm:P.comm!=='stars'?P.comm:'',cmode:P.cmode!=='total'?P.cmode:'',exclude:P.exclude.join(','),bots:P.bots?'':'0'});
  syncShell();drawKpis();drawPeople();drawCharts();drawCommunity();drawAttention();drawStatus();}
function jobs(){return [['momentum','/api/momentum?'+peopleQuery()+'&core='+encodeURIComponent(MINE.join(','))],['traction','/api/traction'],['status','/api/status']];}
function reload(){load(jobs(),render);}
document.querySelectorAll('[data-pm]').forEach(function(b){b.addEventListener('click',function(){P.pm=b.dataset.pm;delete Z.people;render();});});
document.querySelectorAll('[data-comm]').forEach(function(b){b.addEventListener('click',function(){P.comm=b.dataset.comm;delete Z.community;render();});});
document.querySelectorAll('[data-cmode]').forEach(function(b){b.addEventListener('click',function(){P.cmode=b.dataset.cmode;delete Z.community;render();});});
boot(jobs,render);
`;

const CI_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'k-pass':['Pass rate on main','Share of finished push and nightly runs on main that succeeded in the selected window. Issue- and comment-triggered automation, manual dispatches, and cancelled or skipped runs are left out. CircleCI’s 2025 State of Software Delivery sets 90% as the benchmark for the default branch (the observed average is 82%). Green: 90% or better. Amber: 80 to 90%. Red: below 80%.'],
'k-runs':['Runs','Finished push and nightly runs on main in the window that either passed or failed.'],
'k-below':['Workflows below 90%','Workflows on main whose pass rate in the window is under 90%. Green: none. Amber: up to 3. Red: more than 3.'],
'k-flaky':['Flaky commits','Commits where at least one workflow failed and a later run passed, counted once per commit. A failure fixed by re-running the same run cannot be seen, because GitHub replaces the earlier attempt. Green: at most 2% of commits. Amber: up to 5%. Red: more.'],
'k-tests':['Unit tests','Tests in the latest main-branch Unit Tests run and the change across the window. Green: none failing and the count not shrinking. Amber: the count went down. Red: tests failing on main.'],
'k-time':['CI time','Wall-clock time of every finished run in the window, all branches and triggers, against the window before (shown only once that earlier window is fully collected). Runs that took more than 6 hours almost always sat waiting for approval or a runner, so they are counted separately, not added. Green: up at most 10%. Amber: up to 30%. Red: up more than 30%.'],
'outcomes':['Workflow runs','Every GitHub Actions run, grouped by when it started. Counts stacks the outcomes; Share shows each outcome’s percentage; Pass rate shows passed against failed with cancelled and skipped left out. With main selected, only push and nightly runs count. Click a legend entry to hide or show it.'],
'tests':['Unit tests','Read from the pytest summary line (for example “1234 passed, 5 skipped in 312s”) of the latest main-branch Unit Tests run each day, so it covers the unit suite only. If a run has several jobs, the one with the most tests is used. Days without a readable summary are left out, not shown as zero.'],
'time':['Where CI time goes','Wall-clock time of finished runs per period, split by workflow (the largest eight, the rest grouped as Other). This is run duration from start to finish, not billed runner minutes: a run with parallel jobs counts once. Runs over 6 hours are left out as waiting time. Click a legend entry to hide a workflow.'],
'time-table':['CI time by workflow','Total and average time per run in the window, and the change against the window before. Waiting counts runs over 6 hours, which are left out of the times.'],
'table':['Workflow health','Each workflow’s push and nightly runs on main in the window. Runs counts those that passed or failed (cancelled and skipped left out). Flaky counts commits where it failed and a later run passed. Runtime median and P90 are over successful runs, leaving out runs over 6 hours (waiting time). Status follows the pass rate: healthy at 90% or better, watch at 80 to 90%, failing below. Runtime median is coloured against CircleCI’s 10-minute benchmark: green under 10 minutes, amber up to 20, red above; eval and hardware lanes run long by design.']};
var P={om:pick(qs.get('om'),['count','share','rate'],'count'),wf:qs.get('wf')||'',br:pick(qs.get('br'),['main','all'],'main'),tm:pick(qs.get('tm'),['total','outcome','rate'],'total'),ctm:pick(qs.get('ctm'),['hours','share'],'hours'),allWf:false,allTime:false};
var OC={success:'var(--good)',failure:'var(--bad)',cancelled:'var(--neutral)',other:'var(--s4)'};
var WC=['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)','#7a6ee0','#2aa7b8','#b5651d'];
function covText(ci){var f=ci.coverage.first.slice(0,10);return f>sinceDay()?'since '+longLab(f,'day')+' (earliest collected)':rangeText();}
function drawKpis(){var ci=D.ci,t=D.tests,ct=D.citime,h='';if(!ci||!ci.coverage||!ci.coverage.first){el('ov').innerHTML='<div class="card empty">No workflow runs collected yet.</div>';return;}
  var w=ci.main.workflows,ok=0,tot=0,fl=0,below=0,shas=0;w.forEach(function(x){ok+=x.passed;tot+=x.passed+x.failed;fl+=x.flaky;if(x.passRate!=null&&x.passRate<.9)below++;});
  var pr=tot?ok/tot:null;
  h+=kpi('k-pass','Pass rate on main',pct(pr),covText(ci),level(pr,{good:.9,warn:.8}),pct(pr)+' of push and nightly runs passed');
  h+=kpi('k-runs','Runs',fmt(tot),fmt(ok)+' passed · '+fmt(tot-ok)+' failed','');
  h+=kpi('k-below','Workflows below 90%',fmt(below),'of '+w.length+' that ran on main',level(below,{good:0,warn:3,higher:false}),below+' workflows pass less than 90% of the time');
  var fc=ci.main.flakyCommits,fr=ci.main.commits?fc/ci.main.commits:null;
  h+=kpi('k-flaky','Flaky commits',fmt(fc),(fr==null?'':pct(fr,1)+' of '+fmt(ci.main.commits)+' commits · ')+'failed, then passed',level(fr,{good:.02,warn:.05,higher:false}),fc+' commits had a workflow fail and then pass');
  var tr=t&&t.rows.length?t.rows:null,tl=tr?tr[tr.length-1]:null,tf=tr?tr[0]:null;
  var tlv=!tl?'':tl.failed+tl.errors>0?'bad':tl.total<tf.total?'warn':'good';
  h+=kpi('k-tests','Unit tests',tl?fmt(tl.total):'—',tl?(tr.length>1?(tl.total-tf.total>=0?'+':'')+fmt(tl.total-tf.total)+' since '+shortLab(tf.day,'day'):'latest run '+esc(tl.day))+(tl.failed+tl.errors?' · '+fmt(tl.failed+tl.errors)+' failing':''):'not collected yet',tlv,tl?(tl.failed+tl.errors?fmt(tl.failed+tl.errors)+' unit tests failing on main':tl.total<tf.total?'the unit-test count went down':''):'');
  if(ct){var cs=0,ps=0,lr=0;ct.current.forEach(function(r){cs+=r.seconds;lr+=r.long;});(ct.previous||[]).forEach(function(r){ps+=r.seconds;});var g=ct.previous&&ps?(cs-ps)/ps:null;
    h+=kpi('k-time','CI time',fmt(Math.round(cs/3600))+' h',(g==null?'':(g>=0?'+':'')+Math.round(g*100)+'% vs the window before · ')+'all runs'+(lr?' · '+fmt(lr)+' waiting runs left out':''),level(g,{good:.1,warn:.3,higher:false}),g==null?'':'CI time '+(g>=0?'up ':'down ')+Math.abs(Math.round(g*100))+'% against the window before');}
  el('ov').innerHTML='<div class="strip">'+h+'</div>';}
function drawOutcomes(){var t=D.citrend;
  document.querySelectorAll('[data-om]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.om===P.om));});
  var sel=el('wf'),key=t?t.workflows.join('\n'):'';if(t&&sel.getAttribute('data-opts')!==key){sel.length=1;t.workflows.forEach(function(w){var o=document.createElement('option');o.value=w;o.textContent=w.length>46?w.slice(0,45)+'…':w;o.title=w;sel.appendChild(o);});sel.setAttribute('data-opts',key);if(P.wf&&t.workflows.indexOf(P.wf)<0)P.wf='';}
  sel.value=P.wf;el('br').value=P.br;
  if(!t){emptyBox('outcomes','Could not load workflow runs.');return;}
  // A page load can land after a newer menu choice; never draw one workflow's runs under another's name.
  if(t.branch!==P.br||t.workflow!==P.wf){emptyBox('outcomes','Loading…');reloadTrend();return;}
  var rows=t.rows.map(function(r){return {day:r.period,success:r.success,failure:r.failure,cancelled:r.cancelled,other:r.other,total:r.total};});
  var x=series(rows,{success:'sum',failure:'sum',cancelled:'sum',other:'sum',total:'sum'}),b=x.b,K=b.map(function(r){return r.key;}),col=function(f){return b.map(function(r){return r[f];});};
  var se=[{name:'Passed',color:OC.success,vals:col('success')},{name:'Failed',color:OC.failure,vals:col('failure')},{name:'Cancelled',color:OC.cancelled,vals:col('cancelled')},{name:'Skipped or other',color:OC.other,vals:col('other')}];
  var cover=coverNote(x,'runs');
  if(P.om==='count')chart('outcomes',{keys:K,gran:x.g,type:'bar',stacked:true,title:'Workflow runs',series:se,cover:cover});
  else if(P.om==='share')chart('outcomes',{keys:K,gran:x.g,type:'area',ymax:1,yfmt:function(v){return Math.round(v*100)+'%';},title:'Share of outcomes',cover:cover,
    series:se.map(function(s){return {name:s.name,color:s.color,vals:s.vals.map(function(v,i){return b[i].total?(v||0)/b[i].total:null;}),fmt:function(v){return pct(v);}};})});
  else{var prr=b.map(function(r){var d=(r.success||0)+(r.failure||0);return d?r.success/d:null;}),vv=prr.filter(function(v){return v!=null;}),lo=Math.max(0,Math.floor(((vv.length?Math.min.apply(null,vv):1)-.05)*20)/20);
    chart('outcomes',{keys:K,gran:x.g,type:'line',ymin:lo,ymax:1,yfmt:function(v){return Math.round(v*100)+'%';},title:'Pass rate',cover:cover,target:.95,targetLabel:'95% healthy',
      series:[{name:'Pass rate',color:OC.success,vals:prr,fmt:function(v){return pct(v);}}],note:'Fail rate is 100% minus the pass rate.'});}}
function drawTests(){var t=D.tests;
  document.querySelectorAll('[data-tm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.tm===P.tm));});
  if(!t||!t.rows.length){emptyBox('tests','No test counts in this range. They come from the pytest summary in each day’s main-branch Unit Tests log.');return;}
  var x=series(t.rows,{passed:'last',failed:'last',errors:'last',skipped:'last',total:'last'}),b=x.b,K=b.map(function(r){return r.key;}),col=function(f){return b.map(function(r){return r[f];});},cover=coverNote(x,'test counts');
  if(P.tm==='total'){var tt=col('total'),v=tt.filter(function(z){return z!=null;});chart('tests',{keys:K,gran:x.g,type:'line',title:'Number of tests',cover:cover,ymin:v.length?Math.max(0,Math.floor(Math.min.apply(null,v)*.95)):0,series:[{name:'Tests',color:'var(--s1)',vals:tt}]});}
  else if(P.tm==='outcome')chart('tests',{keys:K,gran:x.g,type:'bar',stacked:true,title:'Test outcomes',cover:cover,series:[{name:'Passed',color:OC.success,vals:col('passed')},{name:'Failed',color:OC.failure,vals:col('failed')},{name:'Errors',color:'var(--s2)',vals:col('errors')},{name:'Skipped',color:OC.cancelled,vals:col('skipped')}]});
  else{var rt=b.map(function(r){var d=(r.passed||0)+(r.failed||0)+(r.errors||0);return r.passed==null?null:d?r.passed/d:null;}),vv=rt.filter(function(v){return v!=null;}),lo=Math.max(0,Math.floor(((vv.length?Math.min.apply(null,vv):1)-.01)*100)/100);
    chart('tests',{keys:K,gran:x.g,type:'line',ymin:lo,ymax:1,yfmt:function(v){return (v*100).toFixed(1)+'%';},title:'Test pass rate',cover:cover,series:[{name:'Pass rate (skipped excluded)',color:OC.success,vals:rt,fmt:function(v){return pct(v,2);}}]});}}
function drawTime(){var ct=D.citime;
  document.querySelectorAll('[data-ctm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.ctm===P.ctm));});
  if(!ct||!ct.rows.length){emptyBox('time','No finished runs in this range.');el('time-table').innerHTML='';return;}
  var tot={},runs={},waits={};ct.current.forEach(function(r){tot[r.workflow]=r.seconds;runs[r.workflow]=r.runs;waits[r.workflow]=r.long;});
  var names=Object.keys(tot).sort(function(a,b){return tot[b]-tot[a];}),top=names.slice(0,8),isTop={};top.forEach(function(n){isTop[n]=1;});
  var byDay={};ct.rows.forEach(function(r){var d=byDay[r.day]||(byDay[r.day]={day:r.day});var k=isTop[r.workflow]?r.workflow:'Other';d[k]=(d[k]||0)+r.seconds/3600;});
  var how={};top.concat(['Other']).forEach(function(n){how[n]='sum';});
  var x=series(Object.keys(byDay).sort().map(function(k){return byDay[k];}),how),K=x.b.map(function(r){return r.key;});
  var se=top.map(function(n,i){return {name:n.length>40?n.slice(0,39)+'…':n,color:WC[i%WC.length],vals:x.b.map(function(r){return r[n]==null?null:r[n];}),fmt:function(v){return v.toFixed(1)+' h';}};});
  if(names.length>8)se.push({name:'Other',color:'var(--neutral)',vals:x.b.map(function(r){return r.Other==null?null:r.Other;}),fmt:function(v){return v.toFixed(1)+' h';}});
  if(P.ctm==='share'){var sums=x.b.map(function(r,i){return se.reduce(function(a,s){return a+(s.vals[i]||0);},0);});
    se=se.map(function(s){return {name:s.name,color:s.color,vals:s.vals.map(function(v,i){return sums[i]?(v||0)/sums[i]:null;}),fmt:function(v){return pct(v);}};});
    chart('time',{keys:K,gran:x.g,type:'area',ymax:1,yfmt:function(v){return Math.round(v*100)+'%';},title:'Share of CI time',series:se,cover:coverNote(x,'runs')});}
  else chart('time',{keys:K,gran:x.g,type:'bar',stacked:true,title:'CI time',yfmt:function(v){return cmp(v)+' h';},totalFmt:function(v){return v.toFixed(1)+' h';},series:se,cover:coverNote(x,'runs')});
  var prev={};(ct.previous||[]).forEach(function(r){prev[r.workflow]=r.seconds;});var all=names.reduce(function(a,n){return a+tot[n];},0);
  var rowsH=names.slice(0,P.allTime?names.length:12).map(function(n){var p=prev[n],g=p?(tot[n]-p)/p:null,lv=level(g,{good:.1,warn:.3,higher:false});
    return '<tr><td class="trunc" style="max-width:340px">'+esc(n)+'</td><td class="n">'+(tot[n]/3600).toFixed(1)+' h</td><td class="n"><span class="pb"><i style="width:'+(all?tot[n]/all*100:0)+'%;background:var(--s1)"></i></span>'+(all?pct(tot[n]/all,0):'—')+'</td><td class="n">'+fmt(runs[n])+'</td><td class="n">'+(runs[n]?dur(Math.round(tot[n]/runs[n])):'—')+'</td><td class="n">'+(waits[n]||'')+'</td><td class="n '+(lv==='bad'?'bad':lv==='warn'?'warnc':lv==='good'?'good':'')+'">'+(!ct.previous?'—':g==null?'new':(g>=0?'+':'')+Math.round(g*100)+'%')+'</td></tr>';}).join('');
  el('time-table').innerHTML='<div class="scroll" style="max-height:none"><table><tr><th>Workflow</th><th class="n">Total</th><th class="n">Share</th><th class="n">Runs</th><th class="n">Avg per run</th><th class="n">Waiting</th><th class="n">vs before</th></tr>'+rowsH+'</table></div>'+(names.length>12?'<button class="more" id="more-time">'+(P.allTime?'Show fewer':'Show all '+names.length+' workflows')+'</button>':'');
  var mt=el('more-time');if(mt)mt.onclick=function(){P.allTime=!P.allTime;drawTime();};}
function drawTable(){var ci=D.ci,box=el('wtable');if(!ci||!ci.coverage||!ci.coverage.first){box.innerHTML='<div class="empty">No workflow runs collected yet.</div>';return;}
  var w=ci.main.workflows,rows=P.allWf?w:w.slice(0,10);
  if(!w.length){box.innerHTML='<div class="empty">No push or nightly runs on main in this range.</div>';return;}
  function st(p){if(p==null)return '<span class="st"><span class="dot" style="background:var(--mute)"></span>no result</span>';if(p>=.9)return '<span class="st good"><span class="dot" style="background:var(--good)"></span>healthy</span>';if(p>=.8)return '<span class="st warnc"><span class="dot" style="background:var(--warn)"></span>watch</span>';return '<span class="st bad"><span class="dot" style="background:var(--bad)"></span>failing</span>';}
  var h='<div class="scroll" style="max-height:none"><table><tr><th>Workflow</th><th>Status</th><th class="n">Pass rate</th><th class="n">Runs</th><th class="n">Failed</th><th class="n">Flaky</th><th class="n">Runtime median</th><th class="n">Runtime P90</th><th class="n">Total time</th><th class="n">Waiting</th></tr>';
  rows.forEach(function(x){var c=x.passRate==null?'var(--mute)':x.passRate>=.9?'var(--good)':x.passRate>=.8?'var(--warn)':'var(--bad)';
    h+='<tr><td class="trunc" title="'+esc(x.workflow)+'">'+esc(x.workflow)+'</td><td>'+st(x.passRate)+'</td><td class="n"><span class="pb"><i style="width:'+(x.passRate==null?0:x.passRate*100)+'%;background:'+c+'"></i></span>'+pct(x.passRate)+'</td><td class="n">'+fmt(x.passed+x.failed)+'</td><td class="n">'+x.failed+'</td><td class="n">'+x.flaky+'</td><td'+cellLv(x.p50==null?null:x.p50/60,{good:10,warn:20,higher:false})+'>'+dur(x.p50)+'</td><td class="n">'+dur(x.p90)+'</td><td class="n">'+(x.totalSeconds?(x.totalSeconds/3600).toFixed(1)+' h':'—')+'</td><td class="n">'+(x.longRuns||'')+'</td></tr>';});
  box.innerHTML=h+'</table></div>'+(w.length>10?'<button class="more" id="more">'+(P.allWf?'Show fewer':'Show all '+w.length+' workflows')+'</button>':'');
  var mb=el('more');if(mb)mb.addEventListener('click',function(){P.allWf=!P.allWf;drawTable();});}
function render(){ATTN=[];setUrl({om:P.om!=='count'?P.om:'',wf:P.wf,br:P.br!=='main'?P.br:'',tm:P.tm!=='total'?P.tm:'',ctm:P.ctm!=='hours'?P.ctm:''});syncShell();drawKpis();drawOutcomes();drawTests();drawTime();drawTable();drawAttention();drawStatus();}
function jobs(){return [['ci','/api/ci'],['tests','/api/tests'],['status','/api/status'],['citime','/api/ci-time'],['citrend','/api/ci-trend?branch='+encodeURIComponent(P.br)+'&workflow='+encodeURIComponent(P.wf)]];}
var TREND_GEN=0;
function reloadTrend(){var g=++TREND_GEN,pg=GEN;api(jobs()[4][1]).then(function(r){if(g!==TREND_GEN||pg!==GEN)return;D.citrend=r;delete Z.outcomes;render();}).catch(function(e){if(g!==TREND_GEN||pg!==GEN)return;emptyBox('outcomes','Could not load workflow runs: '+esc(e.message));});}
document.querySelectorAll('[data-om]').forEach(function(b){b.addEventListener('click',function(){P.om=b.dataset.om;render();});});
document.querySelectorAll('[data-tm]').forEach(function(b){b.addEventListener('click',function(){P.tm=b.dataset.tm;delete Z.tests;render();});});
document.querySelectorAll('[data-ctm]').forEach(function(b){b.addEventListener('click',function(){P.ctm=b.dataset.ctm;delete Z.time;render();});});
el('wf').addEventListener('change',function(e){P.wf=e.target.value;reloadTrend();});
el('br').addEventListener('change',function(e){P.br=e.target.value;P.wf='';reloadTrend();});
boot(jobs,render);
`;

const THEME_BOOT = `(function(){var t=null;try{t=localStorage.getItem('gaia-admin-theme');}catch(e){}
if(!t)t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';document.documentElement.dataset.theme=t;})();`;

const GLYPH =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="7.3" y="7" width="1.4" height="4.6" rx=".7" fill="currentColor"/><circle cx="8" cy="4.9" r=".9" fill="currentColor"/></svg>';
export const infoBtn = (key: string, label = "this chart"): string =>
  `<button type="button" class="info" data-info="${key}" aria-label="About: ${label}">${GLYPH}</button>`;

/** A chart card: title with an info button, optional controls, a one-line description, and the chart body. */
export const card = (id: string, title: string, desc: string, controls = ""): string =>
  `<div class="card"><div class="ch"><span class="t">${title}${infoBtn(id, title)}</span><span class="spacer"></span>${controls}<span class="d">${desc}</span></div><div class="cb" id="${id}"><div class="skel"></div></div></div>`;
export const seg = (attr: string, opts: [string, string][], label: string): string =>
  `<div class="seg" role="group" aria-label="${label}">${opts.map(([v, l]) => `<button type="button" data-${attr}="${v}">${l}</button>`).join("")}</div>`;

export type PageId = "users" | "growth" | "developers" | "ci" | "evals";
const TABS: [PageId, string, string][] = [
  ["users", "Users", "/admin"],
  ["growth", "Growth", "/admin/growth"],
  ["developers", "Developers", "/admin/developers"],
  ["ci", "CI", "/admin/ci"],
  ["evals", "Evals", "/admin/evals"],
];

export function shell(active: PageId, title: string, lede: string, body: string, js: string): string {
  const ranges: [string, string][] = [["1", "24h"], ["7", "7d"], ["30", "30d"], ["90", "90d"], ["365", "1y"], ["all", "All"]];
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>${title} · GAIA admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<script>${THEME_BOOT}</script><style>${CSS}</style></head><body>
<div class="top">
<div class="row row1"><a class="brand" href="/admin"><span class="wm">GAIA</span><span class="sm">/ ADMIN</span><span class="badge">Private</span></a>
<nav class="tabs" aria-label="Dashboards">${TABS.map(([id, label, href]) => `<a href="${href}"${id === active ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
<span class="spacer"></span><span class="sm" id="fresh"></span>
<button type="button" class="icon" id="theme" aria-label="Toggle light and dark theme" title="Toggle theme">&#9680;</button></div>
<div class="row row2">
<div class="ctl"><span>Range</span>${seg("range", ranges, "Time range")}${infoBtn("range", "the time range")}</div>
${active === "evals" || active === "growth" ? "" : `<div class="ctl"><span>Group by</span>${seg("gran", [["auto", "Auto"], ["day", "Day"], ["week", "Week"], ["month", "Month"]], "Group by")}</div>`}
<span class="spacer"></span>
<button type="button" class="toggle" id="labels" aria-pressed="false" title="Show values on the charts; otherwise hover to see them">Value labels</button>
</div></div>
<main><h1>${title}</h1><p class="lede">${lede}</p><div id="errors"></div><div id="attn"></div>${body}
<footer id="status">Loading&hellip;</footer>
</main><div id="tip" role="tooltip"></div><script>${CORE_JS.replace("__GLYPH__", GLYPH)}${js}</script></body></html>`;
}

export function renderUsersPage(): string {
  const body = `
<div id="ov"><div class="skel"></div></div>
<section><div class="card">
<div class="ch"><span class="t">Downloads per release${infoBtn("per-release", "Downloads per release")}</span><span class="spacer"></span>
${seg("mode", [["total", "Release vs RC"], ["os", "By OS"], ["product", "By product"]], "Split by")}
<label class="chk"><input type="checkbox" id="o-install"> Installs only</label><label class="chk"><input type="checkbox" id="o-rc"> Release candidates</label>
<span class="d">Each bar is a release at its publish date; its height is every download it has had so far.</span></div>
<div class="split"><div class="cb" id="rel"><div class="skel"></div></div><aside class="side share" id="share" aria-label="Install share"></aside></div>
<div id="rel-table"></div></div></section>
<section class="stack"><div class="head"><h2>Over time</h2></div>
<div class="grid2">
${card("by-date", "Installs by release date", "Each period’s releases and the installs they have had since.")}
${card("daily", "Downloads per day", "Day-to-day change in GitHub’s running totals.")}
</div>
${card("packages", "Package downloads", "PyPI and npm, from the registries.")}
</section>`;
  return shell(
    "users",
    "Users",
    "Who is getting GAIA. Public data only: GitHub release downloads, PyPI and npm. Stars and forks are on the Developers page.",
    body,
    USERS_JS,
  );
}

/** GitHub logins: letters, digits and hyphens, optionally a [bot] suffix. */
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$/;

/**
 * The maintainers' own accounts, for the "Hide maintainers" button. Set at deploy
 * time (MAINTAINER_LOGINS, comma-separated) so personal accounts stay out of the repo.
 */
export function parseMaintainers(raw: string | undefined): string[] {
  const list = (raw ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  const bad = list.filter((x) => !LOGIN.test(x));
  if (bad.length) throw new Error(`MAINTAINER_LOGINS has entries that are not GitHub logins: ${bad.join(", ")}.`);
  return list;
}

export function renderDevelopersPage(maintainers: string[] = []): string {
  const body = `
<div class="card chips" id="people-bar" style="padding:10px 16px;margin-bottom:14px"></div>
<div id="ov"><div class="skel"></div></div>
<section class="stack"><div class="card"><div class="ch"><span class="t">Core team and outside contributors${infoBtn("split", "Core team and outside contributors")}</span><span class="d">The same pull-request numbers for each group, bots left out.</span></div><div id="split"></div></div></section>
<section class="stack"><div class="head"><h2>Issues</h2></div>
<div class="grid2">${card("issues", "Opened and closed", "Closed taller than opened means the backlog shrank.")}${card("backlog", "Open issues", "Backlog at the end of each period.")}</div></section>
<section class="stack"><div class="head"><h2>Pull requests</h2></div>
<div class="grid2">${card("prs", "Opened and merged", "Merged is work that shipped.")}${card("pr-backlog", "Open pull requests", "Waiting for review or merge, at the end of each period.")}</div>
<div class="grid2">${card("ttm-h", "How long PRs take to merge", "PRs merged in the range, by time from opened to merged.")}${card("ttc-h", "How long issues stay open", "Issues closed in the range, by time open.")}</div></section>
<section class="stack"><div class="head"><h2>Contributors</h2></div>
<div class="grid2">${card("commits", "Commits", "On the default branch.")}${card("people", "Contributors", "People committing code.", seg("pm", [["active", "Active"], ["new", "New"]], "Show"))}</div>
${card("loc", "Lines of code changed", "Added and deleted on the default branch.")}
<div class="card"><div class="ch"><span class="t">Who contributed${infoBtn("who", "Who contributed")}</span><span class="d">Untick anyone to leave them out of every number on this page.</span></div><div id="who"></div></div>
</section>
<section class="stack"><div class="head"><h2>Community</h2></div>
${card("community", "Stars and forks", "Growth of the GitHub community.", seg("comm", [["stars", "Stars"], ["forks", "Forks"]], "Metric") + seg("cmode", [["total", "Total"], ["new", "New"]], "Show"))}
</section>`;
  return shell(
    "developers",
    "Developers",
    "How fast the project moves and who moves it: issues, pull requests, commits, lines of code, contributors and the GitHub community.",
    body,
    DEV_JS.replace("__MAINTAINERS__", JSON.stringify(maintainers)),
  );
}

export function renderCiPage(): string {
  const body = `
<div id="ov"><div class="skel"></div></div>
<section class="stack">
${card(
  "outcomes",
  "Workflow runs",
  "Outcome of every GitHub Actions run.",
  seg("om", [["count", "Counts"], ["share", "Share"], ["rate", "Pass rate"]], "Show") +
    `<select id="wf" aria-label="Workflow"><option value="">All workflows</option></select><select id="br" aria-label="Branch"><option value="main">main (push and nightly)</option><option value="all">All branches and triggers</option></select>`,
)}
${card("tests", "Unit tests", "From the pytest summary of the daily main-branch run.", seg("tm", [["total", "Count"], ["outcome", "Outcomes"], ["rate", "Pass rate"]], "Show"))}
</section>
<section class="stack"><div class="head"><h2>Where CI time goes</h2></div>
${card("time", "CI time by workflow", "Run time per period, all branches and triggers.", seg("ctm", [["hours", "Hours"], ["share", "Share"]], "Show"))}
<div class="card"><div class="ch"><span class="t">Time per workflow${infoBtn("time-table", "Time per workflow")}</span><span class="d">In the selected range, against the window before.</span></div><div id="time-table"></div></div>
</section>
<section class="stack"><div class="head"><h2>Workflow health</h2></div>
<div class="card"><div class="ch"><span class="t">Workflows on main${infoBtn("table", "Workflows on main")}</span><span class="d">Push and nightly runs in the selected range, most failures first.</span></div><div id="wtable"><div class="skel"></div></div></div>
</section>`;
  return shell("ci", "CI", "Whether the build is healthy and getting better: run outcomes, test counts, where CI time goes and the workflows that fail most.", body, CI_JS);
}
