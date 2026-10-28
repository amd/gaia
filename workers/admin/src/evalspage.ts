// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Evals page: CI eval results per run, tied to the commit or release each one measured. */

import { INFO_COMMON, card, infoBtn, seg, shell } from "./page";

const EVALS_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'e-nightly':['Latest nightly','Pass rate of the most recent scheduled run in the current view, against the 80% target. Only a scheduled run from the last 7 days is scored. The weekly backlog run is never scored: it re-runs scenarios known to fail or flip, so it is meant to score low, and its result is shown for reference. Green: 80% or better. Amber: 60 to 80%. Red: below 60%.'],
'e-release':['Latest release eval','The most recent eval on a release or release-candidate tag in the current view. RC tags run a 30-scenario smoke tier; final tags and some RCs run the full sweep of about 360 scenarios, so check the scenario count before comparing. Green: 80% or better. Amber: 60 to 80%. Red: below 60%.'],
'e-score':['Judge score','Average score the judge model gave each scenario in the newest run shown, out of 10. It moves before the pass rate does, so it is an early warning. Green: 8 or more. Amber: 6.5 to 8. Red: below 6.5.'],
'e-cost':['Cost per scenario','Estimated cost of the judge and driver model calls divided by the scenarios they ran, over the runs shown, so runs of different sizes compare fairly. It does not include the GPU time of the self-hosted runners.'],
'e-time':['Time per scenario','Median wall-clock time of one scenario, across every scenario in the runs shown. Rising time with a flat pass rate usually means slower inference or longer agent loops.'],
'e-infra':['Infrastructure errors','Runs shown where at least one scenario ended in an infrastructure, setup or harness error instead of an answer. These say nothing about the agent; fix the runner or the backend, then re-run. Green: none. Amber: up to 20% of runs. Red: more.'],
'trend':['Pass rate over time','One point per eval run, oldest to newest; cancelled runs are left out. Pass rate is PASS out of every scenario attempted, the definition behind the committed baseline and the 80% target. Judged pass rate also leaves out timeouts and infrastructure errors, so it isolates the agent’s answers. Runs differ in size (a full sweep is about 360 scenarios, an RC smoke run 30), so filter by tier before comparing. Click a point to open that run.'],
'outcomes':['Outcomes per run','How every scenario in each run ended. A tall yellow block is the infrastructure, not the agent; pink is scenarios that ran out of time. Cancelled runs are left out, since they only measured the lanes that finished. Click a bar to open that run.'],
'perf':['Speed and cost per run','Average generation speed (tokens per second) and time to first token of the agent model, estimated cost per scenario, or median scenario time. Measured inside each scenario and averaged per run.'],
'cats':['By category','Passed out of attempted per category in the selected run, with the change from the last earlier run that measured the same category. Weakest first.'],
'runs':['Runs','Every eval run in the range that matches the filters, newest first, with its trigger, the tag or branch, and the commit it measured. Click a row to open it.'],
'scen':['Scenarios in this run','Each scenario’s result next to the result it had in the last earlier run that included it. ▼ means it passed before and does not now; ▲ means it was fixed. Root cause is the judge’s explanation of a failure.'],
'rel':['Least reliable scenarios','Scenarios across the runs shown, ordered by how often they pass when judged. Only pass and fail count; infrastructure errors and skips are listed as No answer, since they say nothing about the agent. Flaky means the same scenario both passed and failed in the range.']};
var P={tr:pick(qs.get('tr'),['pass','judged'],'pass'),trig:pick(qs.get('trig'),['main','nightly','release','pr','manual','all'],'main'),tier:qs.get('tier')||'',model:qs.get('model')||'',suite:pick(qs.get('suite'),['agent','swebench'],'agent'),cat:qs.get('cat')||'',run:Number(qs.get('run'))||0,pm:pick(qs.get('pm'),['tps','ttft','cost','time'],'tps'),sf:pick(qs.get('sf'),['all','fail','changed'],'all')};
function fq(){return 'suite='+encodeURIComponent(P.suite)+'&trig='+encodeURIComponent(P.trig)+'&tier='+encodeURIComponent(P.tier)+'&model='+encodeURIComponent(P.model)+'&cat='+encodeURIComponent(P.cat);}
var PASS={good:.8,warn:.6};
var SC={PASS:'var(--good)',FAIL:'var(--bad)',TIMEOUT:'var(--s5)'};
function stColor(s){return SC[s]||(/^SKIPPED/.test(s)?'var(--mute)':'var(--s4)');}
function stBadge(s){return s?'<span class="st"><span class="dot" style="background:'+stColor(s)+'"></span>'+esc(s.replace(/_/g,' ').toLowerCase())+'</span>':'<span style="color:var(--mute)">—</span>';}
function isSwe(r){return /SWE-bench/i.test(r.workflow);}
function ago(iso){var d=Math.round((Date.now()-Date.parse(iso))/864e5);return d<=0?'today':d===1?'yesterday':d+' days ago';}
function filtered(){return D.evals?D.evals.runs:[];}
function short(r){return r.is_tag?r.ref:r.kind==='Nightly'?'nightly '+shortLab(r.created_at.slice(0,10),'day'):r.sha.slice(0,7);}
function longName(r){return r.kind+' · '+(r.is_tag?r.ref:(r.ref||'')+' @ '+r.sha.slice(0,7))+' · '+longLab(r.created_at.slice(0,10),'day')+' · '+r.tierLabel+' · '+fmt(r.scenarios)+' scenarios';}
function refLink(r){var u=r.is_tag?'https://github.com/amd/gaia/releases/tag/'+encodeURIComponent(r.ref):'https://github.com/amd/gaia/tree/'+encodeURIComponent(r.ref||'');return '<a href="'+u+'" target="_blank" rel="noopener">'+esc(r.ref||'—')+'</a>';}
function shaLink(sha){return '<a class="mono" href="https://github.com/amd/gaia/commit/'+encodeURIComponent(sha)+'" target="_blank" rel="noopener">'+esc(sha.slice(0,7))+'</a>';}
function prc(p){return p==null?'':p>=.8?'good':p>=.6?'warnc':'bad';}
function refetch(){P.run=0;load(jobs(),render);}
function selectRun(id){P.run=id;var g=++GEN;api('/api/evals?'+fq()+'&run='+id).then(function(r){if(g!==GEN)return;D.evals=r;Z={};render();}).catch(function(e){if(g!==GEN)return;el('errors').innerHTML='<div class="banner">Could not load run '+id+': '+esc(e.message)+'</div>';});}

function drawFilters(){var e=D.evals;if(!e)return;
  document.querySelectorAll('[data-trig]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.trig===P.trig));});
  document.querySelectorAll('[data-suite]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.suite===P.suite));});
  var tiers={},models={};e.allRuns.filter(function(r){return (P.suite==='swebench')===isSwe(r);}).forEach(function(r){tiers[r.tierLabel]=1;if(r.agent_model)models[r.agent_model]=1;});
  function fill(id,vals,cur,allLabel){var s=el(id),opts='<option value="">'+allLabel+'</option>'+Object.keys(vals).sort().map(function(v){return '<option value="'+esc(v)+'">'+esc(v)+'</option>';}).join('');if(s.getAttribute('data-opts')!==opts){s.innerHTML=opts;s.setAttribute('data-opts',opts);}s.value=cur;}
  fill('f-tier',tiers,P.tier,'All tiers');fill('f-model',models,P.model,'All models');
  var cats=e.categoriesAvailable||[];
  el('cat-bar').innerHTML=P.suite==='swebench'||!cats.length?'':'<span class="kl" style="margin-right:4px">Category</span><button type="button" class="chip" data-cat="" aria-pressed="'+(!P.cat)+'">All</button>'+cats.map(function(c){return '<button type="button" class="chip mono" style="font-size:11.5px" data-cat="'+esc(c)+'" aria-pressed="'+(P.cat===c)+'">'+esc(c)+'</button>';}).join('');
  el('cat-bar').querySelectorAll('[data-cat]').forEach(function(b){b.onclick=function(){P.cat=b.getAttribute('data-cat');Z={};refetch();};});}

function drawKpis(runs){var e=D.evals,h='';
  if(!e||!e.allRuns.length){el('ov').innerHTML='<div class="card empty">No eval runs collected yet. Eval artifacts are kept 14 days; run the collector (see README).</div>';return;}
  var scope=P.cat?' · '+esc(P.cat):'';
  var week=Date.now()-7*864e5,sched=runs.filter(function(r){return r.kind==='Nightly';}),night=sched.filter(function(r){return r.tierLabel!=='backlog'&&Date.parse(r.created_at)>=week;}).pop(),backlog=sched.filter(function(r){return r.tierLabel==='backlog';}).pop(),rel=runs.filter(function(r){return r.kind==='Release'||r.kind==='Release candidate';}).pop(),last=runs[runs.length-1];
  h+=kpi('e-nightly','Latest nightly',night?pct(night.passRate):'—',night?fmt(night.passed)+' of '+fmt(night.scenarios-night.skipped)+' · '+esc(night.tierLabel)+' tier · '+ago(night.created_at)+scope:'none in the last 7 days'+(backlog?' · weekly backlog run '+pct(backlog.passRate)+', expected low':''),night?level(night.passRate,PASS):'',night?'nightly pass rate '+pct(night.passRate)+' (target 80%)':'');
  h+=kpi('e-release','Latest release eval',rel?pct(rel.passRate):'—',rel?esc(rel.ref)+' · '+esc(rel.tierLabel)+' · '+fmt(rel.scenarios)+' scenarios'+scope:'none in this view',rel?level(rel.passRate,PASS):'',rel?esc(rel.ref)+' passed '+pct(rel.passRate)+' (target 80%)':'');
  h+=kpi('e-score','Judge score',last&&last.avgScore!=null?last.avgScore.toFixed(2):'—',last?'out of 10 · '+esc(short(last)):'no runs shown',last&&last.avgScore!=null?level(last.avgScore,{good:8,warn:6.5}):'',last&&last.avgScore!=null?'average judge score '+last.avgScore.toFixed(2)+' of 10':'');
  var cs=0,cn=0,cr=0;runs.forEach(function(r){if(r.costUsd!=null&&r.costPerAttempted){cs+=r.costUsd;cn+=r.costUsd/r.costPerAttempted;cr++;}});
  h+=kpi('e-cost','Cost per scenario',cn?'$'+(cs/cn).toFixed(3):'—','$'+cs.toFixed(2)+' across '+plural(cr,'run')+' with costs','');
  h+=kpi('e-time','Time per scenario',e.medianScenarioSeconds!=null?dur(Math.round(e.medianScenarioSeconds)):'—','median of every scenario shown','');
  var inf=runs.filter(function(r){return r.infra>0;}).length,ir=runs.length?inf/runs.length:null;
  h+=kpi('e-infra','Infrastructure errors',fmt(inf),'of '+plural(runs.length,'run')+' had scenarios with no answer',level(ir,{good:0,warn:.2,higher:false}),inf+' of '+runs.length+' runs lost scenarios to infrastructure errors');
  el('ov').innerHTML='<div class="strip">'+h+'</div>';}

function drawCharts(runs){
  if(!runs.length){['trend','outcomes','perf'].forEach(function(i){delete SPECS[i];el(i).innerHTML='<div class="empty">No runs match these filters in this range.</div>';});return;}
  var keys=runs.map(function(r){return String(r.run_id);}),cats=runs.map(short),cl=runs.map(longName),sel=runs.findIndex(function(r){return r.run_id===P.run;});
  var pick=function(i){selectRun(runs[i].run_id);},pc=function(v){return Math.round(v*100)+'%';},sl=sel<0?null:sel;
  document.querySelectorAll('[data-tr]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.tr===P.tr));});
  var TC={full:'var(--s1)',smoke:'var(--s2)',backlog:'var(--s3)',partial:'var(--s4)',pr:'var(--s5)',targeted:'var(--neutral)'},tiers=[];runs.forEach(function(r){if(tiers.indexOf(r.tierLabel)<0)tiers.push(r.tierLabel);});
  var metric=P.tr==='judged'?'judgedPassRate':'passRate';
  chart('trend',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'line',connect:true,ymin:0,ymax:1,yfmt:pc,target:P.tr==='judged'?null:.8,targetLabel:'80% target',selected:sl,onClick:pick,h:260,title:'Pass rate per run',
    series:tiers.map(function(t,i){return {name:t+' tier',color:TC[t]||['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)'][i%5],vals:runs.map(function(r){return r.tierLabel===t?r[metric]:null;}),fmt:function(v){return pct(v);}};})});
  chart('outcomes',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'bar',stacked:true,selected:sl,onClick:pick,h:220,title:'Outcomes per run',
    series:[{name:'Passed',color:'var(--good)',vals:runs.map(function(r){return r.passed;})},{name:'Failed',color:'var(--bad)',vals:runs.map(function(r){return r.failed;})},
      {name:'Timeout',color:'var(--s5)',vals:runs.map(function(r){return r.timeout;})},{name:'Infra or harness error',color:'var(--s4)',vals:runs.map(function(r){return r.infra;})},
      {name:'Skipped or other',color:'var(--neutral)',vals:runs.map(function(r){return r.skipped+r.other;})}]});
  document.querySelectorAll('[data-pm]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.pm===P.pm));});
  var PM={tps:['Tokens per second',function(r){return r.avgTps;},function(v){return v.toFixed(1);}],ttft:['Time to first token',function(r){return r.avgTtft;},function(v){return v.toFixed(2)+'s';}],
    cost:['Cost per scenario',function(r){return r.costPerAttempted;},function(v){return '$'+v.toFixed(3);}],time:['Median scenario time',function(r){return r.medianSeconds;},function(v){return dur(Math.round(v));}]}[P.pm];
  chart('perf',{keys:keys,cats:cats,catsLong:cl,catName:'Run',type:'line',ymin:0,selected:sl,onClick:pick,h:220,title:PM[0],yfmt:PM[2],series:[{name:PM[0],color:'var(--s5)',vals:runs.map(PM[1]),fmt:PM[2]}],empty:'No measurements for these runs.'});}

function drawRun(runs){var e=D.evals,r=runs.find(function(x){return x.run_id===P.run;});
  if(!r){el('run-head').innerHTML='<div class="empty">Pick a run in the charts or the runs table.</div>';el('cats').innerHTML='';el('scen').innerHTML='';el('scen-sum').innerHTML='';return;}
  el('run-head').innerHTML='<div class="strip" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px">'
   +[['Run','<a href="'+esc(r.html_url)+'" target="_blank" rel="noopener">'+esc(r.kind)+' ↗</a>'],['Ref',refLink(r)],['Commit',shaLink(r.sha)],['When',longLab(r.created_at.slice(0,10),'day')+', '+esc(r.created_at.slice(11,16))+' UTC'],
     ['Tier',esc(r.tierLabel)],['Agent model',esc(r.agent_model||'—')],['Pass rate','<b class="'+prc(r.passRate)+'">'+pct(r.passRate)+'</b> ('+fmt(r.passed)+'/'+fmt(r.scenarios-r.skipped)+')'],['Judge',esc(r.judge_model||'—')],['Wall time',r.minutes?Math.round(r.minutes)+' min':'—'],['Cost',r.costUsd!=null?'$'+r.costUsd.toFixed(2):'—']].concat(r.tasks?[['Outcome tasks',fmt(r.tasksPassed)+' of '+fmt(r.tasks)+' passed']]:[]).concat(r.measures==='tasks'?[['Measures','tasks (no judged scenarios)']]:[])
     .map(function(x){return '<div style="min-width:0"><div class="kl">'+x[0]+'</div><div class="ks trunc" style="margin-top:3px;color:var(--ink);max-width:none">'+x[1]+'</div></div>';}).join('')+'</div>';
  var c=e.categories||[];
  el('cats').innerHTML=c.length?c.map(function(x){var d=x.prevPassRate==null?null:x.passRate-x.prevPassRate;
    return '<div class="srow" style="grid-template-columns:minmax(120px,1fr) 90px 48px 64px;margin:4px 0"><span class="mono trunc" style="font-size:11.5px" title="'+esc(x.category)+'">'+esc(x.category)+'</span><span class="b"><i style="width:'+(x.passRate*100)+'%;background:'+(x.passRate>=.8?'var(--good)':x.passRate>=.6?'var(--warn)':'var(--bad)')+'"></i></span><span class="p num" title="'+x.passed+' of '+x.scenarios+' passed">'+x.passed+'/'+x.scenarios+'</span><span class="p num '+(d==null?'':d>0?'good':d<0?'bad':'')+'" title="Change from the previous run that measured this category">'+(d==null?'new':d===0?'±0':(d>0?'+':'')+Math.round(d*100)+' pts')+'</span></div>';}).join(''):'<div class="empty">No categories.</div>';
  document.querySelectorAll('[data-sf]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.sf===P.sf));});
  var skip=function(st){return /^SKIPPED/.test(st||'');},all=e.scenarios||[],rows=all.filter(function(s){if(P.sf==='fail')return s.status!=='PASS';if(P.sf==='changed')return s.previous!=null&&!skip(s.status)&&(s.previous==='PASS')!==(s.status==='PASS');return true;});
  var reg=all.filter(function(s){return s.previous==='PASS'&&s.status!=='PASS'&&!skip(s.status);}).length,fix=all.filter(function(s){return s.previous&&s.previous!=='PASS'&&s.status==='PASS';}).length;
  el('scen-sum').innerHTML='<span class="bad">'+fmt(reg)+' regressed</span> · <span class="good">'+fmt(fix)+' fixed</span> against each scenario’s previous run';
  el('scen').innerHTML=rows.length?'<div class="scroll" style="max-height:520px"><table><tr><th>Scenario</th><th>Category</th><th>Result</th><th>Before</th><th class="n">Score</th><th class="n">Time</th><th class="n">Tokens in / out</th><th class="n">Tools</th><th class="n">Tok/s</th><th class="n">Cost</th><th>Root cause</th></tr>'
   +rows.map(function(s){var chg=s.previous==null||skip(s.status)?'':s.previous==='PASS'&&s.status!=='PASS'?' <span class="bad" title="Passed in the previous run">▼</span>':s.previous!=='PASS'&&s.status==='PASS'?' <span class="good" title="Did not pass in the previous run">▲</span>':'';
     return '<tr><td class="trunc mono" style="max-width:250px;font-size:12px" title="'+esc(s.scenario_id)+'">'+esc(s.scenario_id)+'</td><td class="trunc mono" style="max-width:150px;font-size:12px" title="'+esc(s.category)+'">'+esc(s.category)+(s.kind==='task'?' <span class="badge" title="Task-suite result, not counted in the scenario pass rate">task</span>':'')+'</td><td>'+stBadge(s.status)+chg+'</td><td>'+stBadge(s.previous)+'</td>'
      +'<td class="n">'+(s.score==null?'—':s.score.toFixed(1))+'</td><td class="n">'+(s.elapsed_s==null?'—':dur(Math.round(s.elapsed_s)))+'</td><td class="n">'+(s.input_tokens==null?'—':cmp(s.input_tokens)+' / '+cmp(s.output_tokens))+'</td><td class="n">'+(s.tool_calls==null?'—':s.tool_calls)+'</td><td class="n">'+(s.tps==null?'—':s.tps.toFixed(1))+'</td><td class="n">'+(s.cost_usd==null?'—':'$'+s.cost_usd.toFixed(2))+'</td>'
      +'<td class="trunc" style="max-width:220px" title="'+esc(s.root_cause||'')+'">'+esc(s.root_cause||'')+'</td></tr>';}).join('')+'</table></div>':'<div class="empty">No scenarios match this filter.</div>';}

function drawRunsTable(runs){var box=el('runs');if(!runs.length){box.innerHTML='<div class="empty">No runs match these filters.</div>';return;}
  box.innerHTML='<div class="scroll" style="max-height:420px"><table><tr><th>When (UTC)</th><th>Trigger</th><th>Ref</th><th>Commit</th><th>Tier</th><th>Agent model</th><th class="n">Scenarios</th><th class="n">Pass rate</th><th class="n">Judged</th><th class="n">Score</th><th class="n">Infra</th><th class="n">Cost</th><th class="n">Minutes</th><th></th></tr>'
   +runs.slice().reverse().map(function(r){var on=r.run_id===P.run;return '<tr data-run="'+Number(r.run_id)+'" tabindex="0" aria-label="Open run '+esc(short(r))+'" style="cursor:pointer'+(on?';background:var(--accent-soft)':'')+'"><td>'+esc(r.created_at.slice(0,16).replace('T',' '))+'</td><td>'+esc(r.kind)+'</td><td class="trunc" style="max-width:190px">'+refLink(r)+'</td><td>'+shaLink(r.sha)+'</td><td>'+esc(r.tierLabel)+'</td><td class="trunc" style="max-width:170px" title="'+esc(r.agent_model||'')+'">'+esc(r.agent_model||'—')+'</td>'
     +'<td class="n">'+fmt(r.scenarios)+'</td><td class="n"><b class="'+prc(r.passRate)+'">'+pct(r.passRate)+'</b></td><td class="n">'+pct(r.judgedPassRate)+'</td><td class="n">'+(r.avgScore==null?'—':r.avgScore.toFixed(2))+'</td><td class="n">'+(r.infra||'')+'</td><td class="n">'+(r.costUsd==null?'—':'$'+r.costUsd.toFixed(2))+'</td><td class="n">'+(r.minutes?Math.round(r.minutes):'—')+'</td><td><a href="'+esc(r.html_url)+'" target="_blank" rel="noopener" title="Open the run on GitHub">↗</a></td></tr>';}).join('')+'</table></div>';
  box.querySelectorAll('tr[data-run]').forEach(function(tr){var go=function(){selectRun(Number(tr.getAttribute('data-run')));};
    tr.addEventListener('click',function(ev){if(ev.target.closest('a')||ev.target.closest('td.trunc.clipped,td.trunc.wide'))return;go();});
    tr.addEventListener('keydown',function(ev){if(ev.target!==tr)return;if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();go();}});});}

function drawReliability(){var e=D.evals,box=el('rel');
  var rows=(e&&e.reliability||[]).filter(function(x){return x.runs>=2&&(x.passed+x.failed)>0;}).slice(0,25);
  box.innerHTML=rows.length?'<div class="scroll" style="max-height:420px"><table><tr><th>Scenario</th><th>Category</th><th class="n">Runs</th><th class="n">Pass rate</th><th class="n">No answer</th><th>Flaky</th><th>Last result</th><th class="n">Avg score</th><th class="n">Avg time</th></tr>'
   +rows.map(function(x){return '<tr><td class="trunc mono" style="max-width:300px;font-size:12px" title="'+esc(x.scenario_id)+'">'+esc(x.scenario_id)+'</td><td class="mono" style="font-size:12px">'+esc(x.category)+'</td><td class="n">'+x.runs+'</td><td class="n"><span class="pb"><i style="width:'+((x.passRate||0)*100)+'%;background:'+(x.passRate>=.8?'var(--good)':x.passRate>=.5?'var(--warn)':'var(--bad)')+'"></i></span>'+pct(x.passRate,0)+' <span style="color:var(--mute)">('+x.passed+'/'+(x.passed+x.failed)+')</span></td><td class="n">'+(x.notJudged||'')+'</td><td>'+(x.flaky?'<span class="badge" style="color:var(--warn);border-color:var(--warn)">flaky</span>':'')+'</td><td>'+stBadge(x.lastStatus)+'</td><td class="n">'+(x.avgScore==null?'—':x.avgScore.toFixed(1))+'</td><td class="n">'+(x.avgSeconds==null?'—':dur(Math.round(x.avgSeconds)))+'</td></tr>';}).join('')+'</table></div>'
   :'<div class="empty">Needs at least two judged runs of the same scenario in this view.</div>';}

function render(){var runs=filtered();
  // The server already resolved the selected run (the one asked for, or the newest shown) and sent its scenarios.
  if(D.evals&&!runs.some(function(r){return r.run_id===P.run;}))P.run=D.evals.selected;
  ATTN=[];
  setUrl({trig:P.trig!=='main'?P.trig:'',tier:P.tier,model:P.model,suite:P.suite!=='agent'?P.suite:'',run:P.run?String(P.run):'',pm:P.pm!=='tps'?P.pm:'',sf:P.sf!=='all'?P.sf:'',tr:P.tr!=='pass'?P.tr:'',cat:P.cat});
  syncShell();drawFilters();drawKpis(runs);drawCharts(runs);drawRun(runs);drawRunsTable(runs);drawReliability();drawAttention();drawStatus();}
document.querySelectorAll('[data-trig]').forEach(function(b){b.addEventListener('click',function(){P.trig=b.dataset.trig;Z={};refetch();});});
document.querySelectorAll('[data-suite]').forEach(function(b){b.addEventListener('click',function(){P.suite=b.dataset.suite;P.tier='';P.model='';P.cat='';Z={};refetch();});});
document.querySelectorAll('[data-pm]').forEach(function(b){b.addEventListener('click',function(){P.pm=b.dataset.pm;delete Z.perf;render();});});
document.querySelectorAll('[data-tr]').forEach(function(b){b.addEventListener('click',function(){P.tr=b.dataset.tr;render();});});
document.querySelectorAll('[data-sf]').forEach(function(b){b.addEventListener('click',function(){P.sf=b.dataset.sf;render();});});
el('f-tier').addEventListener('change',function(e){P.tier=e.target.value;Z={};refetch();});
el('f-model').addEventListener('change',function(e){P.model=e.target.value;Z={};refetch();});
function jobs(){return [['evals','/api/evals?'+fq()+(P.run?'&run='+P.run:'')],['status','/api/status']];}
boot(jobs,render);
`;

export function renderEvalsPage(): string {
  const body = `
<div class="card" style="padding:12px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px">
${seg("suite", [["agent", "Agent scenarios"], ["swebench", "SWE-bench"]], "Suite")}
${seg("trig", [["main", "Nightly + releases"], ["nightly", "Nightly"], ["release", "Releases"], ["pr", "Pull requests"], ["manual", "Manual"], ["all", "All"]], "Trigger")}
<select id="f-tier" aria-label="Tier"></select><select id="f-model" aria-label="Agent model"></select></div>
<div class="card chips" id="cat-bar" style="padding:10px 16px;margin-bottom:14px"></div>
<div id="ov"><div class="skel"></div></div>
<section class="stack">
${card("trend", "Pass rate over time", "One point per run, one line per tier: compare runs of the same size. Click a point to open that run.", seg("tr", [["pass", "Pass rate"], ["judged", "Judged"]], "Metric"))}
<div class="grid2">${card("outcomes", "Outcomes per run", "How every scenario ended.")}${card("perf", "Speed and cost", "Per run.", seg("pm", [["tps", "Tok/s"], ["ttft", "First token"], ["cost", "Cost"], ["time", "Time"]], "Metric"))}</div>
</section>
<section class="stack"><div class="head"><h2>Selected run</h2></div>
<div class="card" style="padding:16px 18px" id="run-head"></div>
<div class="card"><div class="ch"><span class="t">By category${infoBtn("cats", "By category")}</span><span class="d">Passed out of attempted, weakest first, with the change since the previous run.</span></div><div id="cats" style="padding:6px 18px 14px;columns:3 300px;column-gap:28px"></div></div>
<div class="card"><div class="ch"><span class="t">Scenarios${infoBtn("scen", "Scenarios")}</span><span class="spacer"></span>${seg("sf", [["all", "All"], ["fail", "Not passing"], ["changed", "Changed"]], "Show")}<span class="d" id="scen-sum"></span></div><div id="scen"></div></div>
</section>
<section class="stack"><div class="head"><h2>History</h2></div>
<div class="card"><div class="ch"><span class="t">Runs${infoBtn("runs", "Runs")}</span><span class="d">Newest first. Click a row to open it.</span></div><div id="runs"></div></div>
<div class="card"><div class="ch"><span class="t">Least reliable scenarios${infoBtn("rel", "Least reliable scenarios")}</span><span class="d">Across the runs shown, lowest pass rate first.</span></div><div id="rel"></div></div>
</section>`;
  return shell(
    "evals",
    "Evals",
    "How the flagship agent scores on the CI eval suites, run by run, and which commit or release each result measured. GitHub keeps eval artifacts for 14 days, so history starts when collection began.",
    body,
    EVALS_JS,
  );
}
