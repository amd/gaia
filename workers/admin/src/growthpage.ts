// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Growth page: how fast the project and its community grow, against targets for a healthy open-source project. */

import { INFO_COMMON, card, infoBtn, shell } from "./page";

/**
 * Targets per 30 days (rates) or per window (shares, counts). `good` and `warn` are the green
 * and amber floors; below `warn` is red. Set for a project of GAIA's size and staffing: each
 * green floor is a little above GAIA's own recent median (2026), not the peers' numbers, which
 * come from far larger communities and are shown beside them in BENCHMARKS for context.
 */
export const TARGETS = {
  starRate: { good: 0.03, warn: 0.015 },
  forkRate: { good: 0.05, warn: 0.025 },
  activeRatio: { good: 1, warn: 0.8 },
  newPer30: { good: 10, warn: 5 },
  retention: { good: 0.25, warn: 0.15 },
  external: { good: 0.2, warn: 0.1 },
  busFactor: { good: 2, warn: 1 },
  /** Downloads in the window against the window before. */
  downloads: { good: 0.05, warn: -0.2 },
} as const;

const pc = (x: number) => `${Math.round(x * 1000) / 10}%`;

/**
 * What healthy, fast-growing AI-agent projects look like, from public GitHub data and star
 * trackers. Update the figures and `BENCHMARKS_AS_OF` together.
 */
export const BENCHMARKS_AS_OF = "October 2026";
export const BENCHMARKS: { key: string; metric: string; target: string; hermes: string; peers: string }[] = [
  { key: "stars", metric: "Star growth a month", target: `${pc(TARGETS.starRate.good)} or more`, hermes: "3–4% (from about 15k stars a week at launch)", peers: "Developer-tool startups average 7.9% a month before Series A; the best about 10%" },
  { key: "ratio", metric: "Forks per star", target: "0.10 to 0.25", hermes: "0.22", peers: "llama.cpp 0.19, crewAI 0.15, OpenHands 0.13, ollama 0.10, Lemonade 0.09" },
  { key: "new", metric: "Newcomers a month", target: `${TARGETS.newPer30.good} or more`, hermes: "About 3,000 (1.2% of its stars)", peers: "Scales with stars; 1% of stars a month is strong" },
  { key: "ext", metric: "Merged PRs from outside the core team", target: `${pc(TARGETS.external.good)} or more`, hermes: "40–58%", peers: "llama.cpp 66%, crewAI 39%, ollama 38%, smolagents 36%, goose 27%, OpenHands 16%, open-webui 4%" },
  { key: "ret", metric: "Newcomers who open another PR within 90 days", target: `${pc(TARGETS.retention.good)} or more`, hermes: "30–34%", peers: "Research: under 20% of newcomers stay long term" },
  { key: "bus", metric: "Bus factor", target: `${TARGETS.busFactor.good} or more`, hermes: "2", peers: "OpenSSF Scorecard asks for contributors from 3 or more organisations" },
  { key: "resp", metric: "Median time to first human reply on an issue", target: "Within a day", hermes: "13 hours", peers: "open-webui 1 h, ollama 3 h, crewAI and smolagents 7 h, llama.cpp 25 h, OpenHands 48 h" },
];

const GROWTH_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'g-stars':['Star growth','Stars added in the window over the stars there were when it began, scaled to 30 days so any range compares. Green: __STAR_GOOD__ a month or more. Amber: __STAR_WARN__ to __STAR_GOOD__. Red: below __STAR_WARN__.'],
'g-forks':['Fork growth','Forks added in the window over the forks there were when it began, per 30 days. Forks are people preparing to change the code, so they lead contributions. Green: __FORK_GOOD__ a month or more. Amber: __FORK_WARN__ to __FORK_GOOD__. Red: below.'],
'g-active':['Active contributors','People (not bots) who committed to the default branch or opened a pull request in the window, against the window before. Green: as many or more. Amber: at least 80% as many. Red: fewer.'],
'g-new':['New contributors','People whose first commit or pull request to the project falls in the window, scaled to 30 days for the colour. CHAOSS tracks this as the clearest sign a community is growing. Green: __NEW_GOOD__ or more a month. Amber: __NEW_WARN__ or more. Red: fewer.'],
'g-ret':['Newcomer retention','Of the people whose first pull request was 90 to 180 days ago, the share who opened another within 90 days of it. It does not follow the range: everyone counted has had the full 90 days. Hermes Agent keeps 30 to 34%; research finds under 20% of newcomers stay. Green: __RET_GOOD__ or more. Amber: __RET_WARN__ or more. Red: below.'],
'g-ext':['Outside contributions','Share of merged pull requests opened by people outside the core team. The core team is the maintainer accounts configured for this dashboard plus GitHub organisation members and collaborators; bots are left out of both counts. Green: __EXT_GOOD__ or more. Amber: __EXT_WARN__ or more. Red: below.'],
'g-bus':['Bus factor','Fewest people who together made at least half of the commits in the window (CHAOSS: contributor absence factor). A bus factor of 1 means one person leaving would halve the work. One person with two accounts counts as two, so read 2 with care. A small core team usually sits at 1 or 2, so 1 is amber, not red. Green: __BUS_GOOD__ or more. Amber: __BUS_WARN__.'],
'd-installs':['GitHub installs','Installer, app and package downloads from GitHub releases in the window, against the window before. Update checks by the desktop app are left out. Counted from nightly snapshots of GitHub’s running totals, which began on __SNAP__, so the comparison appears once a full earlier window has been collected. Downloads swing with release cycles, so only a fall of more than 20% is red. Green: up 5% or more. Amber: down up to 20%, or up less than 5%.'],
'd-pypi':['PyPI downloads','Downloads of the Python package, as PyPI reports them a day late, so the window ends on the last reported day. Includes mirrors and CI installs. Green: up 5% or more. Amber: down up to 20%, or up less than 5%. Red: down more than 20%.'],
'd-npm':['npm downloads','Downloads of the npm package, as npm reports them. Too few to score.'],
'dl':['Downloads per period','GitHub installs, PyPI and npm per period. GitHub installs start when daily snapshots began. Click a legend entry to hide a source, for example PyPI to see the smaller ones.'],
'rate':['Growth rate','Stars and forks added in each period over the total when the period began. The dashed line is the star target (__STAR_GOOD__ a month) converted to the period length; forks aim higher, at __FORK_GOOD__. Click a legend entry to hide it.'],
'contrib':['Contributors per period','Active: people who committed or opened a pull request in the period. New: those doing so for the first time. Bots are left out.'],
'retention':['Newcomers who came back','People whose first pull request fell in each period, split by whether they opened another within 90 days. Periods less than 90 days old stay blank until their newcomers have had the full time.'],
'bench':['Against healthy projects','GAIA’s targets are set from its own recent history, a little above its median; the peers are far larger communities, shown for context rather than as goals. Peer figures come from the GitHub API and public star trackers, __ASOF__; GAIA’s are live, for the selected range. Time to first reply is not collected yet.'],
'outside':['Merged pull requests by who opened them','Core team against everyone else, per period. A growing outside share means the project no longer depends on its maintainers alone.']};
var T=__TARGETS__;
var MINE=__MAINTAINERS__;
var BENCH=__BENCH__;
function rule(t,hi){return {good:t.good,warn:t.warn,higher:hi!==false};}
function perPeriod(m,g){return Math.pow(1+m,(g==='week'?7:30)/30)-1;}
function inRange(){var g=D.growth;if(!g)return [];var s=S.range==='all'?'0000':(g.gran==='week'?weekKey(sinceDay()):sinceDay().slice(0,7)+'-01');return g.periods.filter(function(p){return p.start>=s;});}
function drawKpis(){var g=D.growth;if(!g){el('ov').innerHTML='<div class="card empty">Could not load growth data.</div>';return;}
  var c=g.current,p=g.previous,rt=rangeText(),h='',short=g.days<7,rl=function(v,t){return short?'':level(v,rule(t));},was=function(v,f){return v!=null?' · was '+f(v):'';};
  h+=kpi('g-stars','Star growth',pct(c.starRate30),'a month · +'+fmt(c.stars)+' stars · '+rt+was(p&&p.starRate30,function(v){return pct(v)+'/mo';}),rl(c.starRate30,T.starRate),'stars grew '+pct(c.starRate30)+' a month (target '+pct(T.starRate.good,0)+')');
  h+=kpi('g-forks','Fork growth',pct(c.forkRate30),'a month · +'+fmt(c.forks)+' forks · '+rt+was(p&&p.forkRate30,function(v){return pct(v)+'/mo';}),rl(c.forkRate30,T.forkRate),'forks grew '+pct(c.forkRate30)+' a month (target '+pct(T.forkRate.good,0)+')');
  var ar=p&&p.active?c.active/p.active:null;
  h+=kpi('g-active','Contributors',fmt(c.active),(p?'were '+fmt(p.active)+' · ':'')+rt,level(ar,rule(T.activeRatio)),ar==null?'':fmt(c.active)+' active against '+fmt(p.active)+' in the window before');
  var n30=c.newContributors*30/g.days;
  h+=kpi('g-new','Newcomers',fmt(c.newContributors),'first commit or PR · '+rt+(p?' · were '+fmt(p.newContributors):''),rl(n30,T.newPer30),Math.round(n30)+' new contributors a month (target '+T.newPer30.good+')');
  var nc=g.newcomers;
  h+=kpi('g-ret','Retention',pct(nc.rate,0),nc.rate==null?'no newcomers 3 to 6 months ago':fmt(nc.returned)+' of '+fmt(nc.cohort)+' newcomers from 3–6 months ago came back',level(nc.rate,rule(T.retention)),nc.rate==null?'':pct(nc.rate,0)+' of newcomers opened another PR within 90 days');
  h+=kpi('g-ext','Outside PRs',pct(c.externalShare,0),'of '+fmt(c.prsMerged)+' merged PRs'+(MINE.length?'':' · no maintainer list set'),level(c.externalShare,rule(T.external)),pct(c.externalShare,0)+' of merged PRs came from outside the core team');
  h+=kpi('g-bus','Bus factor',c.busFactor==null?'—':fmt(c.busFactor),c.busFactor==null?'no commits in the window':'people made half the commits',level(c.busFactor,rule(T.busFactor)),c.busFactor==null?'':fmt(c.busFactor)+' people made half the commits');
  el('ov').innerHTML='<div class="strip">'+h+'</div>'+'';}
function drawDownloads(){var g=D.growth,box=el('dl-ov');if(!g){box.innerHTML='';emptyBox('dl','Could not load growth data.');return;}
  var d=g.downloads,rt=rangeText(),h='';
  if(d){var one=function(key,label,w,score){var ch=w.growth==null?'':(w.growth>=0?'+':'')+pct(w.growth,0)+' vs the window before';
      return kpi(key,label,fmt(w.total),(ch||(w.through?'no earlier window collected yet':'no data yet'))+(w.through?' · through '+longLab(w.through,'day'):''),score&&w.previous?level(w.growth,rule(T.downloads)):'',ch?label+' '+ch:'');};
    h=one('d-installs','GitHub installs',d.installs,true)+one('d-pypi','PyPI',d.pypi,true)+one('d-npm','npm',d.npm,false);}
  box.innerHTML=h?'<div class="strip">'+h+'</div>':'';
  var s=S.range==='all'?'0000':(g.gran==='week'?weekKey(sinceDay()):sinceDay().slice(0,7)+'-01'),rows=g.downloadPeriods.filter(function(r){return r.start>=s;});
  if(!rows.length){emptyBox('dl','No downloads recorded in this range yet.');return;}
  var K=rows.map(function(r){return r.start;}),v=function(k){return rows.map(function(r){return r[k];});};
  chart('dl',{keys:K,gran:g.gran,type:'bar',stacked:true,title:'Downloads',h:240,note:K[K.length-1]===(g.gran==='week'?weekKey(today()):today().slice(0,7)+'-01')?'The last '+g.gran+' is still in progress, so it reads low.':'',
    series:[{name:'PyPI',color:'var(--s4)',vals:v('pypi')},{name:'GitHub installs',color:'var(--s1)',vals:v('installs')},{name:'npm',color:'var(--s5)',vals:v('npm')}]});}
function drawCharts(){var g=D.growth,rows=inRange();
  if(!g||!rows.length){['rate','contrib','retention','outside'].forEach(function(id){emptyBox(id,g?'No data for this range yet.':'Could not load growth data.');});return;}
  var K=rows.map(function(r){return r.start;}),gr=g.gran,v=function(f){return rows.map(f);};
  var part=K[K.length-1]===(gr==='week'?weekKey(today()):today().slice(0,7)+'-01')?'The last '+gr+' is still in progress, so it reads low.':'';
  chart('rate',{keys:K,gran:gr,type:'line',title:'Growth rate',h:240,note:part,yfmt:function(x){return pct(x,x<.1?1:0);},target:perPeriod(T.starRate.good,gr),targetLabel:'star target',
    series:[{name:'Stars',color:'var(--s1)',vals:v(function(r){return r.starGrowth;}),fmt:function(x){return pct(x,2);}},{name:'Forks',color:'var(--s3)',vals:v(function(r){return r.forkGrowth;}),fmt:function(x){return pct(x,2);}}]});
  chart('contrib',{keys:K,gran:gr,type:'bar',title:'Contributors',h:240,note:part,
    series:[{name:'Active',color:'var(--s1)',vals:v(function(r){return r.active;})},{name:'New',color:'var(--s2)',vals:v(function(r){return r.newContributors;})}]});
  var waiting=rows.filter(function(r){return r.newcomerCohort==null;}).length;
  if(waiting===rows.length)emptyBox('retention','Newcomers in this range have not had 90 days yet. Pick 1y or All to see earlier groups.');
  else chart('retention',{keys:K,gran:gr,type:'bar',stacked:true,title:'Newcomers who came back',h:220,note:waiting?'The last '+waiting+' '+gr+(waiting>1?'s are':' is')+' blank: their newcomers have not had 90 days yet.':'',
    series:[{name:'Came back',color:'var(--s3)',vals:v(function(r){return r.newcomerReturned;})},{name:'Did not',color:'var(--neutral)',vals:v(function(r){return r.newcomerCohort==null?null:r.newcomerCohort-r.newcomerReturned;})}]});
  chart('outside',{keys:K,gran:gr,type:'bar',stacked:true,title:'Merged pull requests',h:220,note:part,
    series:[{name:'Outside',color:'var(--s2)',vals:v(function(r){return r.externalMerged;})},{name:'Core team',color:'var(--s1)',vals:v(function(r){return r.prsMerged-r.externalMerged;})}]});}
function drawBench(){var g=D.growth,box=el('bench');if(!g){box.innerHTML='';return;}var c=g.current,last=g.periods[g.periods.length-1],n30=c.newContributors*30/g.days;
  var mine={stars:pct(c.starRate30),ratio:last&&last.starsTotal?(last.forksTotal/last.starsTotal).toFixed(2):'—',new:fmt(Math.round(n30))+(last&&last.starsTotal?' ('+pct(n30/last.starsTotal)+' of its stars)':''),
    ext:pct(c.externalShare,0),ret:pct(g.newcomers.rate,0),bus:c.busFactor==null?'—':fmt(c.busFactor),resp:'Not collected yet'};
  box.innerHTML='<div class="scroll" style="max-height:none"><table><tr><th>Metric</th><th>GAIA</th><th>Target</th><th>Hermes Agent</th><th>Peers</th></tr>'+BENCH.map(function(b){
    return '<tr><td>'+esc(b.metric)+'</td><td class="num">'+esc(mine[b.key])+'</td><td>'+esc(b.target)+'</td><td>'+esc(b.hermes)+'</td><td style="white-space:normal">'+esc(b.peers)+'</td></tr>';}).join('')+'</table></div>';}
function render(){ATTN=[];setUrl({});syncShell();drawKpis();drawDownloads();drawCharts();drawBench();drawAttention();drawStatus();}
function jobs(){return [['growth','/api/growth?core='+encodeURIComponent(MINE.join(','))],['status','/api/status']];}
boot(jobs,render);
`;


export function renderGrowthPage(maintainers: string[] = []): string {
  const js = GROWTH_JS.replace("__TARGETS__", JSON.stringify(TARGETS))
    .replace("__MAINTAINERS__", JSON.stringify(maintainers))
    .replace("__BENCH__", JSON.stringify(BENCHMARKS))
    .replace("__ASOF__", BENCHMARKS_AS_OF)
    .replace("__SNAP__", "the first collection")
    .replace(/__STAR_GOOD__/g, pc(TARGETS.starRate.good))
    .replace(/__STAR_WARN__/g, pc(TARGETS.starRate.warn))
    .replace(/__FORK_GOOD__/g, pc(TARGETS.forkRate.good))
    .replace(/__FORK_WARN__/g, pc(TARGETS.forkRate.warn))
    .replace(/__NEW_GOOD__/g, String(TARGETS.newPer30.good))
    .replace(/__NEW_WARN__/g, String(TARGETS.newPer30.warn))
    .replace(/__RET_GOOD__/g, pc(TARGETS.retention.good))
    .replace(/__RET_WARN__/g, pc(TARGETS.retention.warn))
    .replace(/__EXT_GOOD__/g, pc(TARGETS.external.good))
    .replace(/__EXT_WARN__/g, pc(TARGETS.external.warn))
    .replace(/__BUS_GOOD__/g, String(TARGETS.busFactor.good))
    .replace(/__BUS_WARN__/g, String(TARGETS.busFactor.warn));
  const body = `<style>#ov .strip,#dl-ov .strip{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}</style>
<div id="ov"><div class="skel"></div></div>
<section class="stack"><div class="head"><h2>Downloads</h2></div>
<div id="dl-ov"></div>
${card("dl", "Downloads per period", "GitHub installs, PyPI and npm.")}
</section>
<section class="stack"><div class="head"><h2>Rate of change</h2></div>
${card("rate", "Growth rate", "Stars and forks added per period, as a share of the total.")}
<div class="grid2">${card("contrib", "Contributors per period", "Active and first-time contributors.")}${card("retention", "Newcomers who came back", "First-time PR authors who opened another within 90 days.")}</div>
${card("outside", "Merged pull requests by who opened them", "Core team against outside contributors.")}
</section>
<section class="stack"><div class="head"><h2>Benchmarks</h2></div>
<div class="card"><div class="ch"><span class="t">Against healthy projects${infoBtn("bench", "Against healthy projects")}</span><span class="d">GAIA in the selected range beside fast-growing AI-agent projects.</span></div><div id="bench"></div></div>
</section>`;
  return shell(
    "growth",
    "Growth",
    "Is the project growing, and growing healthily? Rates of change for stars, forks and contributors, scored against targets for a healthy open-source project.",
    body,
    js,
  );
}
