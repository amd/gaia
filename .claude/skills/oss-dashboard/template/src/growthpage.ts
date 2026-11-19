/** The Growth page: how fast the project and its community grow, against targets for a healthy open-source project. */

import { BENCHMARKS, BENCHMARKS_AS_OF, PACKAGES, PEER_NAME, PROJECT, TARGETS } from "./config";
import { INFO_COMMON, card, esc, infoBtn, jsonForScript, shell } from "./page";

const pc = (x: number) => `${Math.round(x * 1000) / 10}%`;

const GROWTH_JS = String.raw`
var INFO={` + INFO_COMMON + String.raw`
'g-stars':['Star growth','Stars added in the window over the stars there were when it began, scaled to 30 days so any range compares. Green: __STAR_GOOD__ a month or more. Amber: __STAR_WARN__ to __STAR_GOOD__. Red: below __STAR_WARN__.'],
'g-forks':['Fork growth','Forks added in the window over the forks there were when it began, per 30 days. Forks are people preparing to change the code, so they lead contributions. Green: __FORK_GOOD__ a month or more. Amber: __FORK_WARN__ to __FORK_GOOD__. Red: below.'],
'g-active':['Active contributors','People (not bots) who committed to the default branch or opened a pull request in the window, against the window before. Green: as many or more. Amber: at least 80% as many. Red: fewer.'],
'g-new':['New contributors','People whose first commit or pull request to the project falls in the window, scaled to 30 days for the colour. CHAOSS tracks this as the clearest sign a community is growing. Green: __NEW_GOOD__ or more a month. Amber: __NEW_WARN__ or more. Red: fewer.'],
'g-ret':['Newcomer retention','Of the people whose first pull request was 90 to 180 days ago, the share who opened another within 90 days of it. It does not follow the range: everyone counted has had the full 90 days. Research finds under 20% of newcomers stay long term. Green: __RET_GOOD__ or more. Amber: __RET_WARN__ or more. Red: below.'],
'g-ext':['Outside contributions','Share of merged pull requests opened by people outside the core team. The core team is the maintainer accounts configured for this dashboard plus GitHub organisation members and collaborators; bots are left out of both counts. Green: __EXT_GOOD__ or more. Amber: __EXT_WARN__ or more. Red: below.'],
'g-bus':['Bus factor','How many people the project depends on: the fewest people who together made at least half of the commits in the window. The name comes from asking how many people would have to be hit by a bus before work stalls (CHAOSS calls it contributor absence factor). 1 means a single person writes most of the code, so losing them would halve the work. Accounts joined with + in the maintainer list count as one person; any other second account counts separately. A small core team usually sits at 1 or 2, so 1 is amber, not red. Green: __BUS_GOOD__ or more. Amber: __BUS_WARN__.'],
'g-pkg':['Package download growth','Registry downloads of every configured package in the window against the window before, both ending on the last day the registries have reported (they publish a day or two late). Includes CI and automated installs, so read it as a trend, not a user count. Absolute numbers are on the Users page. Coloured only for ranges of 28 days or more, since one spike day decides a shorter comparison. Green: flat or up, or down less than 10%. Amber: down 10 to 30%. Red: down more than 30%.'],
'g-inst':['Install growth','GitHub release installs (update checks left out) in the window against the window before. GitHub keeps only running totals, so daily installs exist from the first nightly snapshot on; the comparison appears once two full windows are collected. Same colours as package downloads.'],
'rate':['Stars and forks','Added in each period as a share of the total when the period began. The dashed line is the star target (__STAR_GOOD__ a month) converted to the period length; forks aim higher, at __FORK_GOOD__. Click a legend entry to hide it.'],
'momentum':['Momentum','Each period against the average of the four before it, so a release spike or a quiet week stands out without week-to-week noise. Above the zero line is faster than the recent pace. The period in progress is left out. Active contributors are people who committed or opened a PR; downloads are registry packages and GitHub installs.'],
'retention':['Newcomers who came back','People whose first pull request fell in each period, split by whether they opened another within 90 days. Periods less than 90 days old stay blank until their newcomers have had the full time.'],
'outside':['Outside share of merged PRs','Per period, the share of merged pull requests opened by people outside the core team, bots left out. The dashed line is the target (__EXT_GOOD__).'],
'bench':['Against other projects','Targets are set from this project’s own recent history, a little above its median; peers are shown for context rather than as goals. Peer figures were looked up __ASOF__; this project’s are live, for the selected range.']};
var T=__TARGETS__;
var MINE=__MAINTAINERS__,CORE=__CORE__;
var BENCH=__BENCH__,PEER=__PEER__,NAME=__NAME__,PKGS=__PKGS__;
function rule(t,hi){return {good:t.good,warn:t.warn,higher:hi!==false};}
function perPeriod(m,g){return Math.pow(1+m,(g==='week'?7:30)/30)-1;}
function startKey(g){return S.range==='all'?'0000':(g.gran==='week'?weekKey(sinceDay()):sinceDay().slice(0,7)+'-01');}
function nowKey(g){return g.gran==='week'?weekKey(today()):today().slice(0,7)+'-01';}
function inRange(){var g=D.growth;if(!g)return [];var s=startKey(g);return g.periods.filter(function(p){return p.start>=s;});}
/* Why figures from issues and PRs, or commits, are blank (never collected, with the last error); '' when collected. */
function missing(k){var n=D.growth&&D.growth.notCollected;return n&&n[k]?n[k].reason:'';}
function signed(x){return x==null?'—':(x>=0?'+':'')+pct(x,Math.abs(x)<.1?1:0);}
function drawKpis(){var g=D.growth;if(!g){el('ov').innerHTML='<div class="card empty">Could not load growth data.</div>';el('hv').innerHTML='';return;}
  var through=new Date(Date.parse(g.from)+(g.days-1)*864e5).toISOString().slice(0,10),mo=g.days>=30;
  var c=g.current,p=g.previous,rt=S.range==='all'?'all time':g.days+' days to '+shortLab(through,'day'),h='',short=g.days<7,rl=function(v,t){return short?'':level(v,rule(t));},was=function(v,f){return v!=null?' · was '+f(v)+' in the '+g.days+' days before':'';};
  if(short){el('ov').innerHTML='<div class="card empty">Growth rates need at least 7 days. Pick 7d or longer.</div>';el('hv').innerHTML='';return;}
  var sw=starWhy(g.starHistory),fc=g.forkCheck,forkOff=fc&&fc.github!=null&&fc.listed>fc.github*1.1,
    forkJump=!forkOff&&p&&p.forkGrowth>0&&c.forkGrowth!=null&&c.forkGrowth>p.forkGrowth*5;
  if(c.starGrowth==null)h+=kpi('g-stars','Star growth','—',sw||'no star history for this window yet','','');
  else h+=kpi('g-stars','Star growth',mo?pct(c.starRate30):pct(c.starGrowth),(mo?'a month · ':'in '+g.days+' days · ')+'+'+fmt(c.stars)+' stars · '+rt+(mo?was(p&&p.starRate30,function(v){return pct(v)+'/mo';}):was(p&&p.starGrowth,pct)),mo?rl(c.starRate30,T.starRate):'','stars grew '+pct(c.starRate30)+' a month (target '+pct(T.starRate.good,0)+')');
  h+=kpi('g-forks','Fork growth',mo?pct(c.forkRate30):pct(c.forkGrowth),(mo?'a month · ':'in '+g.days+' days · ')+'+'+fmt(c.forks)+' forks · '+rt+(mo?was(p&&p.forkRate30,function(v){return pct(v)+'/mo';}):was(p&&p.forkGrowth,pct))
    +(forkOff?' · GitHub’s dated fork list holds '+fmt(fc.listed)+' forks against its count of '+fmt(fc.github)+', so this rate is not coloured':'')+(forkJump?' · over 5× the window before, so not coloured: check the new forks for a spam wave':''),mo&&!forkOff&&!forkJump?rl(c.forkRate30,T.forkRate):'','forks grew '+pct(c.forkRate30)+' a month (target '+pct(T.forkRate.good,0)+')');
  var noPeople=[missing('issues'),missing('commits')].filter(Boolean).map(esc).join(' ');
  if(noPeople)h+=kpi('g-active','Contributor growth','—',noPeople,'','')+kpi('g-new','Newcomers','—',noPeople,'','');
  else{var ac=p&&p.active?c.active/p.active-1:null;
  h+=kpi('g-active','Contributor growth',signed(ac),fmt(c.active)+' active'+(p?', were '+fmt(p.active):'')+' · '+rt+(enough(p&&Math.min(p.active,c.active),MIN_PEOPLE)?'':' · too few people to colour'),enough(p&&Math.min(p.active,c.active),MIN_PEOPLE)?level(ac,{good:T.activeRatio.good-1,warn:T.activeRatio.warn-1}):'',ac==null?'':'active contributors '+signed(ac)+' against the window before');
  var n30=c.newContributors*30/g.days;
  h+=kpi('g-new','Newcomers',fmt(mo?Math.round(n30):c.newContributors),(mo?'a month · '+fmt(c.newContributors)+' first-time contributors · ':'first-time contributors · ')+rt+(p?', were '+fmt(p.newContributors):''),mo?rl(n30,T.newPer30):'',Math.round(n30)+' new contributors a month (target '+T.newPer30.good+')');}
  var d=g.downloads;
  if(d){var dl=function(key,label,w){var ok=w.growth!=null,lv=ok&&g.days>=MIN_TREND_DAYS?level(w.growth,rule(T.downloads)):'';
      // A decline inside the green band is not good news: no colour rather than a place under "Going well".
      if(lv==='good'&&w.growth<0)lv='';
      return kpi(key,label,signed(w.growth),ok?fmt(w.total)+' vs '+fmt(w.previous)+' · to '+shortLab(w.through,'day'):w.previous===0?fmt(w.total)+', none in the window before':w.first?'collecting since '+shortLab(w.first,'day')+'; needs two nightly snapshots and two full windows':'no data yet',lv,ok?label+' '+signed(w.growth)+' against the window before':'');};
    // Release-file installs are noise next to registry downloads for a library: show them only when they matter.
    var showInst=d.installs.first&&!(PKGS&&d.packages.total>0&&d.installs.total<d.packages.total*0.01);
    h+=(PKGS?dl('g-pkg','Package growth',d.packages):'')+(showInst?dl('g-inst','Install growth',d.installs):'');}
  el('ov').innerHTML='<div class="strip">'+h+'</div>';
  var nc=g.newcomers,hh='',noItems=missing('issues'),noCommits=missing('commits');
  if(noItems)hh+=kpi('g-ret','Newcomer retention','—',esc(noItems),'','')+kpi('g-ext','Outside PRs','—',esc(noItems),'','');
  else{hh+=kpi('g-ret','Newcomer retention',pct(nc.rate,0),nc.rate==null?'no newcomers 3 to 6 months ago':fmt(nc.returned)+' of '+fmt(nc.cohort)+' newcomers from 3–6 months ago came back',enough(nc.cohort,MIN_PEOPLE)?level(nc.rate,rule(T.retention)):'',nc.rate==null?'':pct(nc.rate,0)+' of newcomers opened another PR within 90 days');
  hh+=kpi('g-ext','Outside PRs',pct(c.externalShare,0),'of '+fmt(c.prsMerged)+' merged PRs · '+rt+(MINE.length?'':' · <a href="'+BASE+'/developers">set the core team</a>'),enough(c.prsMerged,MIN_ITEMS)?level(c.externalShare,rule(T.external)):'',pct(c.externalShare,0)+' of merged PRs came from outside the core team');}
  if(noCommits)hh+=kpi('g-bus','Bus factor','—',esc(noCommits),'','');
  else hh+=kpi('g-bus','Bus factor',c.busFactor==null?'—':fmt(c.busFactor),c.busFactor==null?'no commits in the window':(c.busFactor===1?'person':'people')+' made half the commits · '+rt,enough(c.active,MIN_PEOPLE)?level(c.busFactor,rule(T.busFactor)):'',c.busFactor==null?'':fmt(c.busFactor)+(c.busFactor===1?' person made':' people made')+' half the commits');
  el('hv').innerHTML='<div class="strip">'+hh+'</div>';}
/* Each period against the mean of the up-to-four before it (at least two); null for the period in progress. */
function trail(keys,val,now){var out=[];for(var i=0;i<keys.length;i++){var v=val(keys[i]);if(v==null||keys[i]===now){out.push(null);continue;}
  var prev=[];for(var j=i-1;j>=0&&prev.length<4;j--){var x=val(keys[j]);if(x!=null)prev.push(x);}
  var avg=prev.length>=2?prev.reduce(function(a,b){return a+b;},0)/prev.length:null;out.push(avg?v/avg-1:null);}return out;}
/* A momentum series is drawn only with four or more points: fewer show as isolated dots that read as a trend. */
function momentumShown(x){return x.vals.filter(function(y){return y!=null;}).length>=4;}
function drawCharts(){var g=D.growth,rows=inRange();
  if(!g||!rows.length){['rate','momentum','retention','outside'].forEach(function(id){emptyBox(id,g?'No data for this range yet.':'Could not load growth data.');});return;}
  var K=rows.map(function(r){return r.start;}),gr=g.gran,v=function(f){return rows.map(f);},now=nowKey(g);
  var part=K[K.length-1]===now?'The last '+gr+' is still in progress, so it reads low.':'';
  var sv=v(function(r){return r.starGrowth;}),noStars=sv.every(function(x){return x==null;});
  chart('rate',{keys:K,gran:gr,type:'line',title:'Stars and forks',h:240,note:[part,noStars?starWhy(g.starHistory):''].filter(Boolean).join(' '),yfmt:function(x){return pct(x,x<.1?1:0);},target:noStars?null:perPeriod(T.starRate.good,gr),targetLabel:'star target',
    series:(noStars?[]:[{name:'Stars',color:'var(--s1)',vals:sv,fmt:function(x){return pct(x,2);}}]).concat([{name:'Forks',color:'var(--s3)',vals:v(function(r){return r.forkGrowth;}),fmt:function(x){return pct(x,2);}}])});
  var all=g.periods.map(function(r){return r.start;}),act={},py={},ins={};g.periods.forEach(function(r){act[r.start]=r.active;});
  g.downloadPeriods.forEach(function(r){py[r.start]=r.packages;ins[r.start]=r.installs;});
  var pick2=function(m){var t=trail(all,function(k){return m[k]==null?null:m[k];},now),o={};all.forEach(function(k,i){o[k]=t[i];});return K.map(function(k){return o[k]==null?null:o[k];});};
  var se=[{name:'Active contributors',color:'var(--s1)',vals:pick2(act)},{name:'Package downloads',color:'var(--s4)',vals:pick2(py)},{name:'GitHub installs',color:'var(--s2)',vals:pick2(ins)}].filter(momentumShown);
  var mins=[0];se.forEach(function(x){x.vals.forEach(function(y){if(y!=null)mins.push(y);});x.fmt=signed;});
  if(!se.length&&missing('issues')+missing('commits'))emptyBox('momentum',esc([missing('issues'),missing('commits')].filter(Boolean).join(' ')));
  else if(!se.length)emptyBox('momentum','Momentum needs at least four earlier periods to compare with; pick a longer range.');
  else chart('momentum',{keys:K,gran:gr,type:'line',title:'Momentum',h:240,ymin:Math.floor(Math.min.apply(null,mins)*4)/4,yfmt:signed,target:0,targetLabel:'recent pace',series:se});
  if(missing('issues')){emptyBox('retention',esc(missing('issues')));emptyBox('outside',esc(missing('issues')));return;}
  var waiting=rows.filter(function(r){return r.newcomerCohort==null;}).length;
  if(waiting===rows.length)emptyBox('retention','Newcomers in this range have not had 90 days yet. Pick 1y or All to see earlier groups.');
  else chart('retention',{partial:false,keys:K,gran:gr,type:'bar',stacked:true,title:'Newcomers who came back',h:220,note:waiting?'The last '+waiting+' '+gr+(waiting>1?'s are':' is')+' blank: their newcomers have not had 90 days yet.':'',
    series:[{name:'Came back',color:'var(--s3)',vals:v(function(r){return r.newcomerReturned;})},{name:'Did not',color:'var(--neutral)',vals:v(function(r){return r.newcomerCohort==null?null:r.newcomerCohort-r.newcomerReturned;})}]});
  chart('outside',{partial:false,keys:K,gran:gr,type:'line',title:'Outside share of merged PRs',h:220,ymin:0,ymax:1,yfmt:function(x){return pct(x,0);},target:T.external.good,targetLabel:'target',
    series:[{name:'Outside share',color:'var(--s2)',vals:v(function(r){return r.prsMerged?r.externalMerged/r.prsMerged:null;}),fmt:function(x){return pct(x,0);}}]});}
function drawBench(){var g=D.growth,box=el('bench');if(!box)return;if(!g){box.innerHTML='';return;}var c=g.current,last=g.periods[g.periods.length-1],n30=c.newContributors==null?null:c.newContributors*30/g.days;
  var wk=g.days<7?'pick 7d or longer':null;
  var mine={stars:g.days<30?'pick 30d or longer':pct(c.starRate30),ratio:last&&last.starsTotal?(last.forksTotal/last.starsTotal).toFixed(2):'—',new:g.days<30?'pick 30d or longer':n30==null?'—':fmt(Math.round(n30))+(last&&last.starsTotal?' ('+pct(n30/last.starsTotal)+' of its stars)':''),
    ext:wk||pct(c.externalShare,0),ret:g.newcomers.empty?'—':pct(g.newcomers.rate,0),bus:wk||(c.busFactor==null?'—':fmt(c.busFactor))};
  box.innerHTML='<div class="scroll" style="max-height:none"><table><tr><th>Metric</th><th>'+esc(NAME)+'</th><th>Target</th>'+(PEER?'<th>'+esc(PEER)+'</th>':'')+'<th>Others</th></tr>'+BENCH.map(function(b){
    return '<tr><td>'+esc(b.metric)+'</td><td class="num">'+esc(mine[b.key])+'</td><td>'+esc(b.target)+'</td>'+(PEER?'<td>'+esc(b.peer)+'</td>':'')+'<td style="white-space:normal">'+esc(b.others)+'</td></tr>';}).join('')+'</table></div>';}
function render(){ATTN=[];setUrl({});syncShell();drawKpis();drawCharts();drawBench();drawAttention();drawStatus();}
function jobs(){return [['growth','/api/growth?core='+encodeURIComponent(CORE)],['status','/api/status']];}
boot(jobs,render);
`;


export function renderGrowthPage(people: string[][] = []): string {
  const js = GROWTH_JS.replace("__TARGETS__", () => jsonForScript(TARGETS))
    .replace("__MAINTAINERS__", () => jsonForScript(people.flat()))
    .replace("__CORE__", () => jsonForScript(people.map((g) => g.join("+")).join(",")))
    .replace("__BENCH__", () => jsonForScript(BENCHMARKS))
    .replace("__PEER__", () => jsonForScript(PEER_NAME))
    .replace("__NAME__", () => jsonForScript(PROJECT.name))
    .replace("__PKGS__", String(PACKAGES.npm.length + PACKAGES.pypi.length + PACKAGES.crates.length > 0))
    .replace("__ASOF__", () => (BENCHMARKS_AS_OF || "by hand").replace(/['<\\]/g, ""))
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
  const body = `<style>#ov .strip,#hv .strip{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}</style>
<div id="ov"><div class="skel"></div></div>
<section class="stack"><div class="head"><h2>Rate of change</h2></div>
<div class="grid2">${card("rate", "Stars and forks", "Added per period, as a share of the total.")}${card("momentum", "Momentum", "Each period against the average of the four before.")}</div>
</section>
<section class="stack"><div class="head"><h2>Community health</h2></div>
<div id="hv"></div>
<div class="grid2">${card("retention", "Newcomers who came back", "First-time PR authors who opened another within 90 days.")}${card("outside", "Outside share of merged PRs", "Per period, against the target.")}</div>
</section>
${BENCHMARKS.length ? `<section class="stack"><div class="head"><h2>Benchmarks</h2></div>
<div class="card"><div class="ch"><span class="t">Against other projects${infoBtn("bench", "Against other projects")}</span><span class="d">${esc(PROJECT.name)} in the selected range beside the peers in config.ts.</span></div><div id="bench"></div></div>
</section>` : ""}`;
  return shell(
    "growth",
    "Growth",
    "Is the project growing, and growing healthily? Rates of change only; absolute counts are on the Users and Developers pages.",
    body,
    js,
  );
}
