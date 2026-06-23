"use strict";

let state = null;
let selected = null;            // selected point name
let sortKey = "station", sortDir = 1;
let evFilter = "ALL";
const events = [];              // {t, type, sev, text}
const acked = new Set();
let lastReportSeen = null;
let prevStationOnline = {};
let prevAlarmIds = new Set();

// ---- helpers --------------------------------------------------------------
const $ = id => document.getElementById(id);
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmt(v,d){ return (v==null)?"--":Number(v).toFixed(d); }
function nowUTC(){ return new Date().toISOString().slice(11,19); }
function fmtAge(a){ return a==null?"--":(a<1?"now":a<60?a+"s":a<3600?Math.floor(a/60)+"m":Math.floor(a/3600)+"h"); }
function stateLabel(pt){ const v=pt.value; if(v==null)return "--"; if(pt.states&&pt.states[String(v)]!==undefined)return pt.states[String(v)]; return String(v); }

// flatten stations -> point rows
function points(){
  if(!state||!state.stations) return [];
  const out=[];
  state.stations.forEach(st=>st.points.forEach(p=>out.push(Object.assign({station:st.id,station_name:st.name,station_online:st.online},p))));
  return out;
}

// severity model
function overLimit(pt){
  if(pt.type!=="real"||pt.value==null) return false;
  return (pt.hi!=null&&pt.value>pt.hi)||(pt.lo!=null&&pt.value<pt.lo);
}
function sev(pt){
  if(pt.quality==="NOTVALID") return "crit";
  if(!pt.fresh) return "off";
  if(overLimit(pt)) return "crit";
  if(pt.quality==="SUSPECT"||pt.quality==="HELD") return "warn";
  return "ok";
}
function valText(pt){ return pt.type==="real" ? (fmt(pt.value,1)+(pt.unit?" "+pt.unit:"")) : stateLabel(pt); }

// ---- top render -----------------------------------------------------------
function render(){
  if(!state) return;
  const pts=points();
  const stations=state.stations||[];
  const B=!!(state.online&&state.online.B);
  const onlineCount=stations.filter(s=>s.online).length;

  const alarms=buildAlarms(stations,pts);
  const crit=alarms.filter(a=>a.sev==="crit").length;
  const warn=alarms.filter(a=>a.sev==="warn").length;

  $("sys-sub").innerHTML = `domain ${esc(state.server.domain)} &middot; blt ${esc(state.meta.blt||"--")} &middot; session <span class="port">${esc(state.server.host)}:${state.server.port}</span>`;
  const link = B?(onlineCount===stations.length?"NORMAL":(onlineCount?"DEGRADED":"NO DATA")):"OFFLINE";
  setV("st-link",link, B?(onlineCount===stations.length?"sev-ok":"sev-warn"):"sev-crit");
  setV("st-stations",`${onlineCount}/${stations.length}`, onlineCount===stations.length?"sev-ok":(onlineCount?"sev-warn":"sev-crit"));
  const scanning=B && state.report && state.report.count>0;
  setV("st-scan",scanning?"RUN":"HOLD",scanning?"sev-ok":"sev-warn");
  setV("st-crit",String(crit),"sev-crit"); setV("st-warn",String(warn),"sev-warn");
  $("st-rpt").textContent = fmtReport(state.report&&state.report.last_report_time);

  renderStationFilter(stations);
  renderTable(pts);
  renderDetail(pts);
  renderAlarms(alarms);
  detectEvents(stations,alarms);
}
function setV(id,txt,cls){ const e=$(id); if(e){ e.textContent=txt; e.className="v"+(cls?" "+cls:""); } }
function fmtReport(s){ if(!s) return "--:--:--"; const m=String(s).match(/(\d{2})(\d{2})(\d{2})(?:\.\d+)?Z?$/); return m?`${m[1]}:${m[2]}:${m[3]}`:"--:--:--"; }

// ---- points table ---------------------------------------------------------
function renderStationFilter(stations){
  const sel=$("flt-station");
  const want=["<option value=''>station: all</option>"].concat(stations.map(s=>`<option value="${esc(s.id)}">${esc(s.id)}</option>`)).join("");
  if(sel.dataset.sig!==want){ const cur=sel.value; sel.innerHTML=want; sel.value=cur; sel.dataset.sig=want; }
}
function applyFilters(pts){
  const q=$("flt-search").value.trim().toLowerCase();
  const stf=$("flt-station").value, qf=$("flt-quality").value, ctlf=$("flt-ctl").checked;
  let rows=pts.filter(p=>{
    if(stf && p.station!==stf) return false;
    if(ctlf && !p.control) return false;
    if(qf==="good" && sev(p)!=="ok") return false;
    if(qf==="bad" && sev(p)==="ok") return false;
    if(q && !((p.name+" "+p.label+" "+p.station).toLowerCase().includes(q))) return false;
    return true;
  });
  rows.sort((a,b)=>{
    let x=a[sortKey],y=b[sortKey];
    if(sortKey==="value"||sortKey==="age"){ x=x==null?-1e18:x; y=y==null?-1e18:y; }
    else { x=String(x==null?"":x).toLowerCase(); y=String(y==null?"":y).toLowerCase(); }
    return (x<y?-1:x>y?1:0)*sortDir;
  });
  return rows;
}
function renderTable(pts){
  const rows=applyFilters(pts);
  $("pt-count").textContent=`${rows.length} / ${pts.length} points`;
  const body=$("pts-body");
  if(!rows.length){ body.innerHTML='<tr class="none-row"><td colspan="7">no points match filter</td></tr>'; return; }
  body.innerHTML=rows.map(p=>{
    const s=sev(p);
    const rc=s==="crit"?"row-crit":s==="warn"?"row-warn":s==="off"?"row-off":"";
    const vcls=s==="crit"?"crit":s==="off"?"off":(p.type==="state"&&p.fresh?"ok":"");
    const qtxt=esc(p.quality||(p.fresh?"GOOD":"STALE"));
    const ctl=p.control?`<span class="ctl-tag">${p.mode==="sbo"?"SBO":(p.control==="setpoint"?"SETPT":"DIRECT")}</span>`:'<span class="c-dim">--</span>';
    return `<tr data-pt="${esc(p.name)}" class="${p.name===selected?"sel ":""}${rc}">
      <td class="c-dim">${esc(p.station)}</td>
      <td class="c-pt">${esc(p.name)}</td>
      <td class="c-dim">${esc(p.label)}</td>
      <td class="num"><span class="val ${vcls}">${esc(valText(p))}</span></td>
      <td><span class="q ${s}">${qtxt}</span></td>
      <td>${ctl}</td>
      <td class="num c-dim">${fmtAge(p.age)}</td>
    </tr>`;
  }).join("");
  body.querySelectorAll("tr[data-pt]").forEach(tr=>tr.onclick=()=>{ selected=tr.dataset.pt; render(); });
}

// ---- detail / evidence panel ---------------------------------------------
function renderDetail(pts){
  const host=$("detail");
  const p=pts.find(x=>x.name===selected);
  if(!p){ host.innerHTML='<div class="empty">No point selected</div>'; return; }
  const s=sev(p);
  const f=(k,v)=>`<div class="field"><div class="fk">${k}</div><div class="fv">${v}</div></div>`;
  let html=`<div class="detail-title">${esc(p.name)}</div>`;
  html+=f("Station",`${esc(p.station)} &middot; ${esc(p.station_name)}`);
  html+=f("Description",esc(p.label));
  html+=f("Type",esc(p.type));
  html+=f("Value",`<span class="val ${s==="crit"?"crit":s==="off"?"off":"ok"}">${esc(valText(p))}</span>`);
  html+=f("Quality",`<span class="q ${s}">${esc(p.quality||"--")}</span>`);
  html+=f("Station comms",p.station_online?'<span class="q ok">ONLINE</span>':'<span class="q off">OFFLINE</span>');
  html+=f("Time tag",`<span class="mono">${p.ts?new Date(p.ts*1000).toISOString().replace("T"," ").slice(0,19):"--"}</span>`);
  html+=f("Age",fmtAge(p.age));
  html+=f("Control",p.control?`${esc(p.control)} / ${esc(p.mode)}`:"none (read only)");
  if(p.control){ html+=`<div class="detail-sec">Operator Control</div>${controlPanel(p)}`; }
  host.innerHTML=html;
  wireDetail(p);
}
function controlPanel(p){
  const ops = p.control==="setpoint"
    ? `<input type="number" step="0.01" id="sp-in" class="inp" placeholder="setpoint ${esc(p.unit)}" style="flex:0 1 120px">
       <button class="op go" data-act="cmd" data-sp="1">SEND</button>`
    : Object.keys(p.states||{"0":"OFF","1":"ON"}).map(k=>`<button class="op ${k==="1"?"go":"stop"}" data-act="cmd" data-val="${esc(k)}">${esc((p.states||{})[k]||k)}</button>`).join("");
  if(p.mode==="sbo" && !p.armed) return `<div class="ctl-row"><button class="op arm" data-act="select">SELECT</button><span class="col-dim">select before operate</span></div>`;
  if(p.mode==="sbo") return `<div class="ctl-row">${ops}<button class="op" data-act="cancel">CANCEL</button><span class="armed">ARMED ${p.armed}s</span></div>`;
  return `<div class="ctl-row">${ops}</div>`;
}
function wireDetail(p){
  $("detail").querySelectorAll("button[data-act]").forEach(b=>b.onclick=()=>{
    const act=b.dataset.act;
    if(act==="select"){ control({action:"select",item:p.name}); pushEvent("CMD","cmd",`SELECT ${p.name}`); }
    else if(act==="cancel"){ control({action:"cancel",item:p.name}); pushEvent("CMD","cmd",`CANCEL ${p.name}`); }
    else if(act==="cmd"){
      let val;
      if(b.dataset.sp){ const inp=$("sp-in"); if(!inp||inp.value==="")return; val=parseFloat(inp.value); }
      else val=parseInt(b.dataset.val,10);
      control({action:"command",item:p.name,value:val}); pushEvent("CMD","cmd",`OPERATE ${p.name} = ${val}`);
    }
  });
}

// ---- alarms ---------------------------------------------------------------
function buildAlarms(stations,pts){
  const a=[];
  stations.forEach(st=>{ if(!st.online) a.push({sev:"warn",id:"COM-"+st.id,cond:"STATION COMMS LOST",val:st.name}); });
  pts.forEach(p=>{
    if(p.quality==="NOTVALID") a.push({sev:"warn",id:"INV-"+p.name,cond:"POINT NOT VALID",val:p.label});
    if(p.fresh&&p.hi!=null&&p.value!=null&&p.value>p.hi) a.push({sev:"crit",id:"HI-"+p.name,cond:"HIGH "+p.label,val:fmt(p.value,1)+" "+p.unit+" > "+p.hi});
    if(p.fresh&&p.lo!=null&&p.value!=null&&p.value<p.lo) a.push({sev:"crit",id:"LO-"+p.name,cond:"LOW "+p.label,val:fmt(p.value,1)+" "+p.unit+" < "+p.lo});
  });
  return a;
}
function renderAlarms(alarms){
  const ids=new Set(alarms.map(a=>a.id)); [...acked].forEach(id=>{ if(!ids.has(id)) acked.delete(id); });
  const body=$("alarm-body");
  if(!alarms.length){ body.innerHTML='<tr class="none-row"><td colspan="5">no active alarms</td></tr>'; return; }
  body.innerHTML=alarms.slice().sort((a,b)=>(a.sev==="crit"?0:1)-(b.sev==="crit"?0:1)).map(a=>{
    const ackd=acked.has(a.id);
    return `<tr class="alm-${a.sev}">
      <td><span class="sevcell ${a.sev}">${a.sev.toUpperCase()}</span></td>
      <td class="c-dim">${esc(a.id)}</td><td>${esc(a.cond)}</td>
      <td class="c-dim">${esc(a.val||"")}</td>
      <td class="${ackd?"c-dim":"sev-warn"}">${ackd?"ACK":"UNACK"}</td></tr>`;
  }).join("");
}

// ---- event log ------------------------------------------------------------
function pushEvent(type,sev,text){ events.unshift({t:nowUTC(),type,sev,text}); if(events.length>200)events.pop(); renderEvents(); }
function renderEvents(){
  const body=$("event-body");
  const rows=events.filter(e=>evFilter==="ALL"||e.type===evFilter);
  if(!rows.length){ body.innerHTML='<tr class="none-row"><td colspan="4">no events</td></tr>'; return; }
  body.innerHTML=rows.slice(0,120).map(e=>`<tr>
    <td class="c-dim raw">${e.t}</td>
    <td class="${e.sev==="crit"?"sev-crit":e.sev==="warn"?"sev-warn":"sev-off"}" style="font-weight:700">${e.sev.toUpperCase()}</td>
    <td><span class="evtype ${e.type.toLowerCase()}">${e.type}</span></td>
    <td class="raw">${esc(e.text)}</td></tr>`).join("");
}
function detectEvents(stations,alarms){
  const rt=state.report&&state.report.last_report_time;
  if(rt&&rt!==lastReportSeen){ lastReportSeen=rt; const n=points().length; pushEvent("RX","rx",`Block 2 report received (${n} points, cond ${state.report.cond})`); }
  stations.forEach(st=>{ if(prevStationOnline[st.id]!==undefined && prevStationOnline[st.id]!==st.online) pushEvent("SYS",st.online?"sys":"warn",`${st.id} comms ${st.online?"restored":"lost"}`); prevStationOnline[st.id]=st.online; });
  const ids=new Set(alarms.map(a=>a.id));
  alarms.forEach(a=>{ if(!prevAlarmIds.has(a.id)) pushEvent("ALM",a.sev,`${a.id} ${a.cond} ${a.val||""}`); });
  prevAlarmIds=ids;
}

// ---- control + boot -------------------------------------------------------
async function control(body){ try{ await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); }catch(e){} }

function init(){
  $("st-clock").textContent=nowUTC(); setInterval(()=>$("st-clock").textContent=nowUTC(),1000);
  ["flt-search","flt-station","flt-quality","flt-ctl"].forEach(id=>$(id).addEventListener("input",()=>renderTable(points())));
  document.querySelectorAll("th[data-sort]").forEach(th=>th.onclick=()=>{ const k=th.dataset.sort; if(sortKey===k)sortDir*=-1; else{sortKey=k;sortDir=1;} renderTable(points()); });
  $("ackbtn").onclick=()=>{ buildAlarms(state.stations||[],points()).forEach(a=>acked.add(a.id)); render(); };
  $("evfilters").querySelectorAll(".chip").forEach(c=>c.onclick=()=>{ evFilter=c.dataset.ev; $("evfilters").querySelectorAll(".chip").forEach(x=>x.classList.toggle("on",x===c)); renderEvents(); });

  fetch("/api/state").then(r=>r.json()).then(s=>{state=s;render();});
  const es=new EventSource("/api/events");
  es.onmessage=ev=>{ try{ state=JSON.parse(ev.data); render(); }catch(e){} };
  es.onerror=()=>{ setV("st-link","OFFLINE","sev-crit"); };
}
document.addEventListener("DOMContentLoaded",init);
