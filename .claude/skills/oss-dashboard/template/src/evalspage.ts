/** The Evals page: CI eval and benchmark results per run, tied to the commit or release each one measured. */

import { EVAL_ARTIFACT_DAYS } from "./config";
import { INFO_COMMON, card, infoBtn, seg, shell } from "./page";

const EVALS_JS = `var ART_DAYS=${EVAL_ARTIFACT_DAYS};` + String.raw`
var INFO={` + INFO_COMMON + String.raw`
'e-latest':['Latest pass rate','Pass rate of the newest run shown: results that passed, out of those that passed, failed or timed out. ERROR (the harness or infrastructure broke) and SKIPPED results are left out. Not coloured: what counts as good depends on the suite; the Regressions card says whether it got worse.'],
'e-release':['Latest release','Pass rate of the newest run shown on a release or release-candidate tag, by the same definition. A release is a run whose branch or tag matches the release tag pattern in config.ts. Check the tier and model before comparing it with other runs.'],
'e-reg':['Regressions','Results in the newest run shown that passed the last time they had a pass or fail outcome in an earlier run of the same suite, tier and model, and now fail or time out. Errors and skips are not regressions. Green: none. Amber: 1 or 2. Red: 3 or more.'],
'e-err':['Runs with errors','Runs shown where at least one result ended in ERROR: the harness, the runner or a service it needs broke, so the result says nothing about the thing under test. Green: none. Amber: up to 20% of runs. Red: more.'],
'e-cost':['Cost per result','Sum of cost_usd over the runs shown, divided by the number of results that reported a cost, so runs of different sizes compare fairly. Only what the eval files report; runner time is not included.'],
'e-time':['Time per result','Median of elapsed_s across every result in the runs shown that reported it. Rising time with a flat pass rate usually means slower inference, a slower service, or longer loops.'],
'trend':['Pass rate over time','One point per run, oldest to newest, one line per tier and model: only runs on the same line are comparable. Pass rate is PASS out of PASS, FAIL and TIMEOUT. Cancelled runs are left out, since they measured only what finished before they stopped. Click a point to open that run.'],
'outcomes':['Outcomes per run','How every result in each run ended. Errors are the harness or infrastructure, not the thing under test; skipped results were not run. Click a bar to open that run.'],
'perf':['Cost, time and score per run','Per run: cost per result (over results that report a cost), median time per result, or the average score. The score is on whatever scale the suite reports, so it is not coloured.'],
'cats':['By category','Passed out of passed, failed and timed out per category in the selected run, with the change from the newest earlier run of the same suite, tier and model that measured the category. Weakest first.'],
'runs':['Runs','Every run in the range that matches the filters, newest first: its trigger, branch or tag, commit, tier and model. Regressed counts results that passed in the previous comparable run and now fail. Click a row to open it.'],
'res':['Results in this run','Each result next to its last pass or fail outcome in an earlier run of the same suite, tier and model. ▼ means it passed before and fails now; ▲ means it was fixed. The note is whatever the eval file reported, usually a short reason.'],
'rel':['Least reliable results','Each result across the runs shown, ordered by how often it passes. Only pass, fail and timeout count; errors and skips are listed as No outcome. Flaky means the same result both passed and failed in the range.']};
var P={trig:pick(qs.get('trig'),['main','scheduled','release','rc','pr','manual','all'],'main'),suite:qs.get('suite')||'',tier:qs.get('tier')||'',model:qs.get('model')||'',cat:qs.get('cat')||'',run:qs.get('run')||'',pm:pick(qs.get('pm'),['cost','time','score'],'cost'),sf:pick(qs.get('sf'),['all','fail','changed'],'all')};
function fq(){return 'suite='+encodeURIComponent(P.suite)+'&trig='+encodeURIComponent(P.trig)+'&tier='+encodeURIComponent(P.tier)+'&model='+encodeURIComponent(P.model)+'&cat='+encodeURIComponent(P.cat);}
var SC={PASS:'var(--good)',FAIL:'var(--bad)',TIMEOUT:'var(--s5)',ERROR:'var(--s4)',SKIPPED:'var(--mute)'};
function stBadge(s){return s?'<span class="st"><span class="dot" style="background:'+(SC[s]||'var(--mute)')+'"></span>'+esc(s.toLowerCase())+'</span>':'<span style="color:var(--mute)">—</span>';}
function isFail(s){return s==='FAIL'||s==='TIMEOUT';}
function isRel(r){return r.kind==='Release'||r.kind==='Release candidate';}
function ago(iso){var d=Math.round((Date.now()-Date.parse(iso))/864e5);return d<=0?'today':d===1?'yesterday':d+' days ago';}
function dash(s){return s?esc(s):'—';}
function seriesName(r,runs){var t={},m={};runs.forEach(function(x){t[x.tier]=1;m[x.model]=1;});var p=[];
  if(Object.keys(t).length>1)p.push(r.tier?r.tier+' tier':'no tier');if(Object.keys(m).length>1)p.push(r.model||'no model');return p.join(' · ')||'pass rate';}
function short(r){return isRel(r)?r.ref:shortLab(r.created_at.slice(0,10),'day')+' '+r.sha.slice(0,7);}
function longName(r){return r.kind+' · '+(isRel(r)?r.ref:(r.ref||'')+' @ '+r.sha.slice(0,7))+' · '+longLab(r.created_at.slice(0,10),'day')+(r.tier?' · '+r.tier:'')+(r.model?' · '+r.model:'')+' · '+fmt(r.results)+' results';}
function refLink(r){if(!r.ref)return '—';if(r.kind==='Pull request')return esc(r.ref);var u=isRel(r)?'https://github.com/'+REPO+'/releases/tag/'+encodeURIComponent(r.ref):'https://github.com/'+REPO+'/tree/'+encodeURIComponent(r.ref);return '<a href="'+u+'" target="_blank" rel="noopener">'+esc(r.ref)+'</a>';}
function shaLink(sha){return '<a class="mono" href="https://github.com/'+REPO+'/commit/'+encodeURIComponent(sha)+'" target="_blank" rel="noopener">'+esc(sha.slice(0,7))+'</a>';}
function outOf(r){var n=r.passed+r.failed+r.timeout;return n?fmt(r.passed)+' of '+fmt(n):'no pass or fail outcomes';}
function money(v,d){return v==null?'—':'$'+v.toFixed(d==null?2:d);}
function refetch(){P.run='';load(jobs(),render);}
function selectRun(key){P.run=key;var g=++GEN;api('/api/evals?'+fq()+'&run='+encodeURIComponent(key)).then(function(r){if(g!==GEN)return;D.evals=r;Z={};render();}).catch(function(e){if(g!==GEN)return;el('errors').innerHTML='<div class="banner">Could not load run '+esc(key)+': '+esc(e.message)+'</div>';});}

function drawFilters(){var e=D.evals;if(!e)return;
  document.querySelectorAll('[data-trig]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.trig===P.trig));});
  var su=e.suites||[];
  el('suite-bar').innerHTML=su.length>1?'<span class="kl" style="margin-right:4px">Suite</span>'+su.map(function(s){return '<button type="button" class="chip mono" style="font-size:11.5px" data-suite="'+esc(s.suite)+'" aria-pressed="'+(s.suite===e.suite)+'" title="'+plural(s.runs,'run')+' in this range">'+esc(s.suite)+'</button>';}).join(''):'';
  el('suite-bar').querySelectorAll('[data-suite]').forEach(function(b){b.onclick=function(){P.suite=b.getAttribute('data-suite');P.tier='';P.model='';P.cat='';Z={};refetch();};});
  function fill(id,vals,cur,allLabel){var s=el(id),opts='<option value="">'+allLabel+'</option>'+vals.map(function(v){return '<option value="'+esc(v)+'">'+esc(v)+'</option>';}).join('');if(s.getAttribute('data-opts')!==opts){s.innerHTML=opts;s.setAttribute('data-opts',opts);}s.value=cur;s.hidden=!vals.length;}
  fill('f-tier',e.options.tiers,P.tier,'All tiers');fill('f-model',e.options.models,P.model,'All models');
  var cats=e.options.categories||[];if(cats.length<2&&!P.cat)cats=[];
  el('cat-bar').style.display=cats.length?'':'none';
  el('cat-bar').innerHTML=cats.length?'<span class="kl" style="margin-right:4px">Category</span><button type="button" class="chip" data-cat="" aria-pressed="'+(!P.cat)+'">All</button>'+cats.map(function(c){return '<button type="button" class="chip mono" style="font-size:11.5px" data-cat="'+esc(c)+'" aria-pressed="'+(P.cat===c)+'">'+esc(c)+'</button>';}).join(''):'';
  el('cat-bar').querySelectorAll('[data-cat]').forEach(function(b){b.onclick=function(){P.cat=b.getAttribute('data-cat');Z={};refetch();};});}

function drawKpis(runs){var e=D.evals,h='';
  if(!e||!e.stored){el('ov').innerHTML='<div class="card empty">No eval results collected yet. Each workflow in EVAL_WORKFLOWS must upload an artifact named eval-results… holding eval-results.json files (references/evals-contract.md); then run the collector. GitHub keeps artifacts '+ART_DAYS+' days, so older runs cannot be read.</div>';return;}
  if(!e.suite){el('ov').innerHTML='<div class="card empty">No eval results in this range.</div>';return;}
  var scope=P.cat?' · '+esc(P.cat):'',last=runs[runs.length-1],rel=runs.filter(isRel).pop();
  h+=kpi('e-latest','Latest pass rate',last?pct(last.passRate):'—',last?outOf(last)+' · '+esc(last.kind.toLowerCase())+' · '+ago(last.created_at)+scope:'no runs shown','');
  h+=kpi('e-release','Latest release',rel?pct(rel.passRate):'—',rel?esc(rel.ref)+' · '+outOf(rel)+scope:'none in this view','');
  var rg=last?last.regressed:null;
  h+=kpi('e-reg','Regressions',rg==null?'—':fmt(rg),rg==null?(last?'no earlier run of this tier and model to compare with':'no runs shown'):'in the latest run · '+fmt(last.fixed)+' fixed',rg==null?'':level(rg,{good:0,warn:2,higher:false}),rg==null?'':rg+' results passed before and fail in the latest run');
  var er=runs.filter(function(r){return r.errors>0;}).length,ir=runs.length?er/runs.length:null;
  h+=kpi('e-err','Runs with errors',fmt(er),'of '+plural(runs.length,'run')+' had results that ended in ERROR',level(ir,{good:0,warn:.2,higher:false}),er+' of '+runs.length+' runs had harness or infrastructure errors');
  var cs=0,cn=0;runs.forEach(function(r){if(r.costUsd!=null&&r.costPerResult){cs+=r.costUsd;cn+=r.costUsd/r.costPerResult;}});
  h+=kpi('e-cost','Cost per result',cn?money(cs/cn,3):'—',cn?money(cs)+' across the runs shown':'no results report a cost','');
  h+=kpi('e-time','Time per result',e.medianSeconds!=null?dur(Math.round(e.medianSeconds)):'—',e.medianSeconds!=null?'median of every result shown':'no results report a time','');
  el('ov').innerHTML='<div class="strip">'+h+'</div>';}

function drawCharts(runs){
  if(!runs.length){['trend','outcomes','perf'].forEach(function(i){delete SPECS[i];el(i).innerHTML='<div class="empty">No runs match these filters in this range.</div>';});return;}
  var keys=runs.map(function(r){return r.key;}),cats=runs.map(short),cl=runs.map(longName),sel=runs.findIndex(function(r){return r.key===P.run;});
  var go=function(i){selectRun(runs[i].key);},pc=function(v){return Math.round(v*100)+'%';},sl=sel<0?null:sel,COL=['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)'],ser=[];
  runs.forEach(function(r){var n=seriesName(r,runs);if(ser.indexOf(n)<0)ser.push(n);});
  chart('trend',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'line',connect:true,ymin:0,ymax:1,yfmt:pc,selected:sl,onClick:go,h:260,title:'Pass rate per run',
    series:ser.map(function(n,i){return {name:n,color:COL[i%COL.length],vals:runs.map(function(r){return seriesName(r,runs)===n?r.passRate:null;}),fmt:function(v){return pct(v);}};})});
  chart('outcomes',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'bar',stacked:true,selected:sl,onClick:go,h:220,title:'Outcomes per run',
    series:[{name:'Passed',color:'var(--good)',vals:runs.map(function(r){return r.passed;})},{name:'Failed',color:'var(--bad)',vals:runs.map(function(r){return r.failed;})},
      {name:'Timeout',color:'var(--s5)',vals:runs.map(function(r){return r.timeout;})},{name:'Error',color:'var(--s4)',vals:runs.map(function(r){return r.errors;})},
      {name:'Skipped',color:'var(--neutral)',vals:runs.map(function(r){return r.skipped;})}]});
  document.querySelectorAll('[data-pm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.pm===P.pm));});
  var PM={cost:['Cost per result',function(r){return r.costPerResult;},function(v){return money(v,3);}],time:['Median time per result',function(r){return r.medianSeconds;},function(v){return dur(Math.round(v));}],
    score:['Average score',function(r){return r.avgScore;},function(v){return v.toFixed(2);}]}[P.pm];
  chart('perf',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'line',ymin:0,selected:sl,onClick:go,h:220,title:PM[0],yfmt:PM[2],series:[{name:PM[0],color:'var(--s5)',vals:runs.map(PM[1]),fmt:PM[2]}],empty:'The eval files of these runs do not report this.'});}

function drawRun(runs){var e=D.evals,r=runs.find(function(x){return x.key===P.run;});
  if(!r){el('run-head').innerHTML='<div class="empty">Pick a run in the charts or the runs table.</div>';el('cats').innerHTML='';el('res').innerHTML='';el('res-sum').innerHTML='';return;}
  el('run-head').innerHTML='<div class="strip" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px">'
   +[['Run','<a href="'+esc(r.html_url)+'" target="_blank" rel="noopener">'+esc(r.workflow)+' ↗</a>'],['Trigger',esc(r.kind)],['Ref',refLink(r)],['Commit',shaLink(r.sha)],['When',longLab(r.created_at.slice(0,10),'day')+', '+esc(r.created_at.slice(11,16))+' UTC'],
     ['Suite',esc(r.suite)],['Tier',dash(r.tier)],['Model',dash(r.model)],['Pass rate',pct(r.passRate)+' ('+fmt(r.passed)+'/'+fmt(r.passed+r.failed+r.timeout)+')'],['Errors / skipped',fmt(r.errors)+' / '+fmt(r.skipped)],
     ['Cost',money(r.costUsd)],['Result time',r.totalSeconds==null?'—':dur(Math.round(r.totalSeconds))]]
     .map(function(x){return '<div style="min-width:0"><div class="kl">'+x[0]+'</div><div class="ks trunc" style="margin-top:3px;color:var(--ink);max-width:none">'+x[1]+'</div></div>';}).join('')+'</div>';
  var c=e.byCategory||[];
  el('cats').innerHTML=c.length?c.map(function(x){var d=x.prevPassRate==null||x.passRate==null?null:x.passRate-x.prevPassRate,w=(x.passRate||0)*100;
    return '<div class="srow" style="grid-template-columns:minmax(120px,1fr) 90px 48px 64px;margin:4px 0"><span class="mono trunc" style="font-size:11.5px" title="'+esc(x.category)+'">'+dash(x.category)+'</span><span class="b"><i style="width:'+w+'%;background:var(--s1)"></i></span><span class="p num" title="'+x.passed+' of '+x.decided+' passed">'+x.passed+'/'+x.decided+'</span><span class="p num '+(d==null?'':d>0?'good':d<0?'bad':'')+'" title="Change from the previous comparable run that measured this category">'+(x.prevPassRate==null?'new':d==null?'—':d===0?'±0':(d>0?'+':'')+Math.round(d*100)+' pts')+'</span></div>';}).join(''):'<div class="empty">No categories.</div>';
  document.querySelectorAll('[data-sf]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.sf===P.sf));});
  var all=e.results||[],changed=function(s){return s.previous!=null&&(s.status==='PASS'&&isFail(s.previous)||s.previous==='PASS'&&isFail(s.status));};
  var rows=all.filter(function(s){if(P.sf==='fail')return s.status!=='PASS';if(P.sf==='changed')return changed(s);return true;});
  // Regressions first, then other failures, errors, newly fixed, passing, skipped.
  var rank=function(s){return s.status==='SKIPPED'?5:s.status==='PASS'?(s.previous&&isFail(s.previous)?3:4):s.status==='ERROR'?2:s.previous==='PASS'?0:1;};
  rows=rows.slice().sort(function(a,b){return rank(a)-rank(b);});
  var reg=all.filter(function(s){return s.previous==='PASS'&&isFail(s.status);}).length,fix=all.filter(function(s){return isFail(s.previous)&&s.status==='PASS';}).length;
  el('res-sum').innerHTML='<span class="bad">'+fmt(reg)+' regressed</span> · <span class="good">'+fmt(fix)+' fixed</span> against each result’s previous comparable run';
  el('res').innerHTML=rows.length?'<div class="scroll" style="max-height:520px"><table><tr><th>Result</th><th>Category</th><th>Status</th><th>Before</th><th class="n">Score</th><th class="n">Time</th><th class="n">Tokens in / out</th><th class="n">Cost</th><th>Note</th></tr>'
   +rows.map(function(s){var chg=s.previous==='PASS'&&isFail(s.status)?' <span class="bad" title="Passed in the previous comparable run">▼</span>':isFail(s.previous)&&s.status==='PASS'?' <span class="good" title="Failed in the previous comparable run">▲</span>':'';
     return '<tr><td class="trunc mono" style="max-width:250px;font-size:12px" title="'+esc(s.result_id)+'">'+esc(s.result_id)+'</td><td class="trunc mono" style="max-width:150px;font-size:12px" title="'+esc(s.category)+'">'+dash(s.category)+'</td><td>'+stBadge(s.status)+chg+'</td><td>'+stBadge(s.previous)+'</td>'
      +'<td class="n">'+(s.score==null?'—':s.score.toFixed(2))+'</td><td class="n">'+(s.elapsed_s==null?'—':dur(Math.round(s.elapsed_s)))+'</td><td class="n">'+(s.input_tokens==null&&s.output_tokens==null?'—':(s.input_tokens==null?'—':cmp(s.input_tokens))+' / '+(s.output_tokens==null?'—':cmp(s.output_tokens)))+'</td><td class="n">'+money(s.cost_usd,3)+'</td>'
      +'<td class="trunc" style="max-width:240px" title="'+esc(s.note||'')+'">'+esc(s.note||'')+'</td></tr>';}).join('')+'</table></div>':'<div class="empty">No results match this filter.</div>';}

function drawRunsTable(runs){var box=el('runs');if(!runs.length){box.innerHTML='<div class="empty">No runs match these filters.</div>';return;}
  box.innerHTML='<div class="scroll" style="max-height:420px"><table><tr><th>When (UTC)</th><th>Trigger</th><th>Ref</th><th>Commit</th><th>Tier</th><th>Model</th><th class="n">Results</th><th class="n">Pass rate</th><th class="n">Regressed</th><th class="n">Errors</th><th class="n">Cost</th><th></th></tr>'
   +runs.slice().reverse().map(function(r){var on=r.key===P.run;return '<tr data-run="'+esc(r.key)+'" tabindex="0" aria-label="Open run '+esc(short(r))+'" style="cursor:pointer'+(on?';background:var(--accent-soft)':'')+'"><td>'+esc(r.created_at.slice(0,16).replace('T',' '))+'</td><td>'+esc(r.kind)+'</td><td class="trunc" style="max-width:190px">'+refLink(r)+'</td><td>'+shaLink(r.sha)+'</td><td>'+dash(r.tier)+'</td><td class="trunc" style="max-width:170px" title="'+esc(r.model)+'">'+dash(r.model)+'</td>'
     +'<td class="n">'+fmt(r.results)+'</td><td class="n"><b>'+pct(r.passRate)+'</b></td><td class="n'+(r.regressed?' bad':'')+'">'+(r.regressed==null?'—':fmt(r.regressed))+'</td><td class="n">'+(r.errors||'')+'</td><td class="n">'+money(r.costUsd)+'</td><td><a href="'+esc(r.html_url)+'" target="_blank" rel="noopener" title="Open the run on GitHub">↗</a></td></tr>';}).join('')+'</table></div>';
  box.querySelectorAll('tr[data-run]').forEach(function(tr){var open=function(){selectRun(tr.getAttribute('data-run'));};
    tr.addEventListener('click',function(ev){if(ev.target.closest('a')||ev.target.closest('td.trunc.clipped,td.trunc.wide'))return;open();});
    tr.addEventListener('keydown',function(ev){if(ev.target!==tr)return;if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();open();}});});}

function drawReliability(){var e=D.evals,box=el('rel');
  var rows=(e&&e.reliability||[]).filter(function(x){return x.runs>=2&&(x.passed+x.failed)>0;}).slice(0,25);
  box.innerHTML=rows.length?'<div class="scroll" style="max-height:420px"><table><tr><th>Result</th><th>Category</th><th class="n">Runs</th><th class="n">Pass rate</th><th class="n">No outcome</th><th>Flaky</th><th>Last status</th><th class="n">Avg time</th></tr>'
   +rows.map(function(x){return '<tr><td class="trunc mono" style="max-width:300px;font-size:12px" title="'+esc(x.result_id)+'">'+esc(x.result_id)+'</td><td class="mono" style="font-size:12px">'+dash(x.category)+'</td><td class="n">'+x.runs+'</td><td class="n"><span class="pb"><i style="width:'+((x.passRate||0)*100)+'%;background:var(--s1)"></i></span>'+pct(x.passRate,0)+' <span style="color:var(--mute)">('+x.passed+'/'+(x.passed+x.failed)+')</span></td><td class="n">'+(x.undecided||'')+'</td><td>'+(x.flaky?'<span class="badge" style="color:var(--warn);border-color:var(--warn)">flaky</span>':'')+'</td><td>'+stBadge(x.lastStatus)+'</td><td class="n">'+(x.avgSeconds==null?'—':dur(Math.round(x.avgSeconds)))+'</td></tr>';}).join('')+'</table></div>'
   :'<div class="empty">Needs at least two runs with a pass or fail outcome for the same result in this view.</div>';}

function render(){var e=D.evals,runs=e?e.runs:[];
  // The server already resolved the selected run (the one asked for, or the newest shown) and sent its results.
  if(e&&!runs.some(function(r){return r.key===P.run;}))P.run=e.selected||'';
  ATTN=[];
  setUrl({trig:P.trig!=='main'?P.trig:'',suite:P.suite,tier:P.tier,model:P.model,cat:P.cat,run:P.run,pm:P.pm!=='cost'?P.pm:'',sf:P.sf!=='all'?P.sf:''});
  syncShell();drawFilters();drawKpis(runs);drawCharts(runs);drawRun(runs);drawRunsTable(runs);drawReliability();drawAttention();drawStatus();}
document.querySelectorAll('[data-trig]').forEach(function(b){b.addEventListener('click',function(){P.trig=b.dataset.trig;Z={};refetch();});});
document.querySelectorAll('[data-pm]').forEach(function(b){b.addEventListener('click',function(){P.pm=b.dataset.pm;delete Z.perf;render();});});
document.querySelectorAll('[data-sf]').forEach(function(b){b.addEventListener('click',function(){P.sf=b.dataset.sf;render();});});
el('f-tier').addEventListener('change',function(e){P.tier=e.target.value;Z={};refetch();});
el('f-model').addEventListener('change',function(e){P.model=e.target.value;Z={};refetch();});
function jobs(){return [['evals','/api/evals?'+fq()+(P.run?'&run='+encodeURIComponent(P.run):'')],['status','/api/status']];}
boot(jobs,render);
`;

export function renderEvalsPage(): string {
  const body = `
<div class="card" style="padding:12px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px">
<span class="chips" id="suite-bar"></span>
${seg("trig", [["main", "Default branch + releases"], ["scheduled", "Scheduled"], ["release", "Releases"], ["rc", "Release candidates"], ["pr", "Pull requests"], ["manual", "Manual"], ["all", "All"]], "Trigger")}
<select id="f-tier" aria-label="Tier" hidden></select><select id="f-model" aria-label="Model" hidden></select></div>
<div class="card chips" id="cat-bar" style="padding:10px 16px;margin-bottom:14px;display:none"></div>
<div id="ov"><div class="skel"></div></div>
<section class="stack">
${card("trend", "Pass rate over time", "One point per run, one line per tier and model. Click a point to open that run.")}
<div class="grid2">${card("outcomes", "Outcomes per run", "How every result ended.")}${card("perf", "Cost, time and score", "Per run.", seg("pm", [["cost", "Cost"], ["time", "Time"], ["score", "Score"]], "Metric"))}</div>
</section>
<section class="stack"><div class="head"><h2>Selected run</h2></div>
<div class="card" style="padding:16px 18px" id="run-head"></div>
<div class="card"><div class="ch"><span class="t">By category${infoBtn("cats", "By category")}</span><span class="d">Passed out of decided, weakest first, with the change since the previous comparable run.</span></div><div id="cats" style="padding:6px 18px 14px;columns:3 300px;column-gap:28px"></div></div>
<div class="card"><div class="ch"><span class="t">Results${infoBtn("res", "Results")}</span><span class="spacer"></span>${seg("sf", [["all", "All"], ["fail", "Not passing"], ["changed", "Changed"]], "Show")}<span class="d" id="res-sum"></span></div><div id="res"></div></div>
</section>
<section class="stack"><div class="head"><h2>History</h2></div>
<div class="card"><div class="ch"><span class="t">Runs${infoBtn("runs", "Runs")}</span><span class="d">Newest first. Click a row to open it; click a column title to sort.</span></div><div id="runs"></div></div>
<div class="card"><div class="ch"><span class="t">Least reliable results${infoBtn("rel", "Least reliable results")}</span><span class="d">Across the runs shown, lowest pass rate first.</span></div><div id="rel"></div></div>
</section>`;
  return shell(
    "evals",
    "Evals",
    `Eval and benchmark results from CI, run by run, and the commit or release each one measured. GitHub keeps the artifacts for ${EVAL_ARTIFACT_DAYS} days, so history starts when collection began.`,
    body,
    EVALS_JS,
  );
}
