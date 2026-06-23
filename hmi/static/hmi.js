"use strict";

let state = null;
let selected = null;
let sortKey = "name", sortDir = 1;
let evFilter = "ALL";
const events = [];
const acked = new Set();
let lastReportSeen = null;
let prevStationOnline = {};
let prevAlarmIds = new Set();

const $ = id => document.getElementById(id);
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmt(v,d){ return (v==null)?"--":Number(v).toFixed(d); }
function nowUTC(){ return new Date().toISOString().slice(11,19); }
function fmtAge(a){ return a==null?"--":(a<1?"now":a<60?a+"s":a<3600?Math.floor(a/60)+"m":Math.floor(a/3600)+"h"); }
function stateLabel(pt){ const v=pt.value; if(v==null)return "--"; if(pt.states&&pt.states[String(v)]!==undefined)return pt.states[String(v)]; return String(v); }
function fmtReport(s){ if(!s) return "--:--:--"; const m=String(s).match(/(\d{2})(\d{2})(\d{2})(?:\.\d+)?Z?$/); return m?`${m[1]}:${m[2]}:${m[3]}`:"--:--:--"; }

function points(){
  if(!state||!state.stations) return [];
  const out=[];
  state.stations.forEach(st=>st.points.forEach(p=>out.push(Object.assign({station:st.id,station_name:st.name,station_online:st.online},p))));
  return out;
}
function overLimit(pt){ if(pt.type!=="real"||pt.value==null)return false; return (pt.hi!=null&&pt.value>pt.hi)||(pt.lo!=null&&pt.value<pt.lo); }
function sev(pt){
  if(pt.quality==="NOTVALID") return "crit";
  if(!pt.fresh) return "off";
  if(overLimit(pt)) return "crit";
  if(pt.quality==="SUSPECT"||pt.quality==="HELD") return "warn";
  return "ok";
}
function stationSev(st){
  if(!st.online) return "off";
  let s="ok";
  st.points.forEach(p=>{ const x=sev(Object.assign({station_online:st.online},p)); if(x==="crit")s="crit"; else if(x==="warn"&&s!=="crit")s="warn"; });
  return s;
}
function valText(pt){ return pt.type==="real" ? (fmt(pt.value,1)+(pt.unit?" "+pt.unit:"")) : stateLabel(pt); }

// ---- top render -----------------------------------------------------------
function render(){
  if(!state) return;
  const pts=points(), stations=state.stations||[];
  const B=!!(state.online&&state.online.B);
  const onlineCount=stations.filter(s=>s.online).length;
  const alarms=buildAlarms(stations,pts);
  const crit=alarms.filter(a=>a.sev==="crit").length, warn=alarms.filter(a=>a.sev==="warn").length;

  // appliance status rail (compact annunciator indicators)
  const allOn=onlineCount===stations.length;
  $("hb-lamp").className="lamp "+(B?(allOn?"run":(onlineCount?"warn":"crit")):"crit");
  setV("hb-link", B?(allOn?"online":(onlineCount?"partial":"no data")):"offline", B?(allOn?"run":"warn"):"crit");
  setV("hb-stations", `${onlineCount}/${stations.length}`, allOn?"run":"warn");
  const scanning=B&&state.report&&state.report.count>0;
  setV("hb-scan", scanning?"running":"hold", scanning?"run":"warn");
  setV("hb-alarms", crit?`${crit} crit / ${warn} warn`:(warn?`0 crit / ${warn} warn`:"no alarms"),
       crit?"crit":(warn?"warn":"off"));
  $("hb-rpt").textContent=fmtReport(state.report&&state.report.last_report_time);

  renderStationFilter(stations);
  renderMimic(stations);
  renderAlarmRail(alarms);
  renderTable();
  renderAlarms(alarms);
  renderDetail(pts);
  detectEvents(stations,alarms);
}
function setV(id,txt,st){ const e=$(id); if(e){ e.textContent=txt; e.className="ai-v"+(st?" "+st:""); } }

// ---- communication / mimic bus --------------------------------------------
function renderMimic(stations){
  const gwOn=!!(state.online&&state.online.B);
  const srv=state.server||{};
  let h=`<div class="bus-gw"><span class="lamp ${gwOn?"run":"crit"}"></span>
    <div><div class="gw-t">ICCP GATEWAY</div><div class="gw-s">${esc(srv.domain||"")} &middot; ${esc(srv.host||"")}:${esc(srv.port||"")}</div></div></div>
    <div class="bus-lead"></div>
    <div class="bus-track"><div class="trunk"></div>`;
  h+=stations.map(st=>{
    const s=stationSev(st);
    const tapcls=st.online?(s==="crit"?"crit":s==="warn"?"warn":"on"):"off";
    const lamp=s==="crit"?"crit":s==="warn"?"warn":st.online?"run":"";
    const stxt=st.online?(s==="crit"?"ALARM":s==="warn"?"DEGRADED":"ONLINE"):"OFFLINE";
    const col=s==="crit"?"var(--crit)":s==="warn"?"var(--warn)":st.online?"var(--run)":"var(--stop)";
    return `<div class="tap ${tapcls}"><span class="dot ${lamp}"></span><span class="leg"></span>
      <div class="bnode"><div class="bn-id">${esc(st.id.toUpperCase())}</div><div class="bn-nm">${esc(st.name)}</div>
      <div class="bn-st" style="color:${col}">${stxt}</div></div></div>`;
  }).join("");
  h+=`</div>`;
  $("mimic").innerHTML=h;
}

// ---- active alarm rail (priority banner) ----------------------------------
function renderAlarmRail(alarms){
  const host=$("alarmrail");
  if(!alarms.length){ host.innerHTML='<div class="arail-none">No active alarms</div>'; return; }
  host.innerHTML=alarms.slice().sort((a,b)=>(a.sev==="crit"?0:1)-(b.sev==="crit"?0:1)).slice(0,4).map(a=>
    `<div class="arail ${a.sev}"><div class="ab"></div>
      <div class="asev">${a.sev.toUpperCase()}</div>
      <div class="acond">${esc(a.cond)}</div>
      <div class="aval">${esc(a.val||"")}</div></div>`).join("");
}

// ---- grouped point table --------------------------------------------------
function renderStationFilter(stations){
  const sel=$("flt-station");
  const want=["<option value=''>all stations</option>"].concat(stations.map(s=>`<option value="${esc(s.id)}">${esc(s.id)}</option>`)).join("");
  if(sel.dataset.sig!==want){ const cur=sel.value; sel.innerHTML=want; sel.value=cur; sel.dataset.sig=want; }
}
function matchFilters(p){
  const q=$("flt-search").value.trim().toLowerCase();
  const stf=$("flt-station").value, qf=$("flt-quality").value, ctlf=$("flt-ctl").checked;
  if(stf&&p.station!==stf) return false;
  if(ctlf&&!p.control) return false;
  if(qf==="good"&&sev(p)!=="ok") return false;
  if(qf==="bad"&&sev(p)==="ok") return false;
  if(q&&!((p.name+" "+p.label+" "+p.station).toLowerCase().includes(q))) return false;
  return true;
}
function renderTable(){
  const body=$("pts-body");
  const stations=state.stations||[];
  let total=0, shown=0, html="";
  stations.forEach(st=>{
    let rows=st.points.map(p=>Object.assign({station:st.id,station_name:st.name,station_online:st.online},p)).filter(matchFilters);
    total+=st.points.length;
    if(!rows.length) return;
    rows.sort((a,b)=>{ let x=a[sortKey],y=b[sortKey]; if(sortKey==="value"||sortKey==="age"){x=x==null?-1e18:x;y=y==null?-1e18:y;} else {x=String(x==null?"":x).toLowerCase();y=String(y==null?"":y).toLowerCase();} return (x<y?-1:x>y?1:0)*sortDir; });
    shown+=rows.length;
    const ss=stationSev(st);
    html+=`<tr class="grp ${st.online?"":"off"}"><td colspan="7">${esc(st.id.toUpperCase())} &middot; ${esc(st.name)}<span class="gstate">${st.online?(ss==="crit"?"ALARM":ss==="warn"?"DEGRADED":"online"):"offline"}</span></td></tr>`;
    html+=rows.map(p=>{
      const s=sev(p);
      const vcls=s==="crit"?"crit":s==="off"?"off":(p.type==="state"&&p.fresh?"ok":"");
      const ctl=p.control?`<span class="p-ctl">${p.mode==="sbo"?"SBO":(p.control==="setpoint"?"SETPT":"DIR")}</span>`:'<span class="p-age">--</span>';
      return `<tr class="pt s-${s} ${p.name===selected?"sel":""}" data-pt="${esc(p.name)}">
        <td class="gut"></td>
        <td class="p-name">${esc(p.name)}</td>
        <td class="p-desc">${esc(p.label)}</td>
        <td class="num"><span class="p-val ${vcls}">${esc(valText(p))}</span></td>
        <td><span class="p-q ${s}">${esc(p.quality||(p.fresh?"GOOD":"STALE"))}</span></td>
        <td>${ctl}</td>
        <td class="num p-age">${fmtAge(p.age)}</td></tr>`;
    }).join("");
  });
  $("pt-count").textContent=`${shown}/${total}`;
  body.innerHTML=html||'<tr class="none-row"><td colspan="7">no points match filter</td></tr>';
  body.querySelectorAll("tr.pt").forEach(tr=>tr.onclick=()=>{ selected=tr.dataset.pt; render(); });
}

// ---- control bay (instrument detail) --------------------------------------
function renderDetail(pts){
  const host=$("detail");
  const p=pts.find(x=>x.name===selected);
  if(!p){ host.innerHTML='<div class="empty">No point selected</div>'; return; }
  const s=sev(p);
  const f=(k,v)=>`<div class="frow"><div class="fk">${k}</div><div class="fv">${v}</div></div>`;
  let h=`<div class="iface"><span class="if-name">${esc(p.name)}<br><span style="color:var(--text-dim);font-weight:400;font-size:12px">${esc(p.label)}</span></span>
    <span class="if-val ${s==="crit"?"crit":s==="off"?"off":"ok"}">${esc(valText(p))}</span></div>`;
  h+=f("Station",`${esc(p.station)} &middot; ${esc(p.station_name)}`);
  h+=f("Quality",`<span class="p-q ${s}">${esc(p.quality||"--")}</span>`);
  h+=f("Comms",p.station_online?'<span class="p-q ok">ONLINE</span>':'<span class="p-q off">OFFLINE</span>');
  h+=f("Time tag",`<span class="timestamp">${p.ts?new Date(p.ts*1000).toISOString().replace("T"," ").slice(0,19):"--"}</span>`);
  h+=f("Age",fmtAge(p.age));
  if(p.hi!=null) h+=f("Hi limit",`<span class="timestamp">${p.hi}</span>`);
  h+=f("Control",p.control?`${esc(p.control)} / ${esc(p.mode)}`:"none (read only)");
  if(p.control){
    const armed=p.armed&&p.armed>0;
    h+=`<div class="cmdmod"><div class="cmd-hd"><span>Operator Command</span><span class="arm">${armed?("ARMED "+p.armed+"s"):""}</span></div>
        <div class="cmd-row">${commandControls(p)}</div></div>`;
  }
  // Live reports re-render this panel ~1/s. Preserve an in-progress setpoint
  // entry (value, focus, caret) across the rebuild so a report landing between
  // the operator typing and pressing Send does not wipe the field.
  const prev=$("sp-in");
  const keep=prev?{v:prev.value,f:document.activeElement===prev,s:prev.selectionStart,e:prev.selectionEnd}:null;
  host.innerHTML=h;
  const now=$("sp-in");
  if(now&&keep){ now.value=keep.v; if(keep.f){ now.focus(); try{now.setSelectionRange(keep.s,keep.e);}catch(_){} } }
  wireDetail(p);
}
function commandControls(p){
  const ops = p.control==="setpoint"
    ? `<input type="number" step="0.01" id="sp-in" class="inp sp" placeholder="setpoint ${esc(p.unit)}"><button class="opbtn go" data-act="cmd" data-sp="1">Send</button>`
    : Object.keys(p.states||{"0":"OFF","1":"ON"}).map(k=>`<button class="opbtn ${k==="1"?"go":"stop"}" data-act="cmd" data-val="${esc(k)}">${esc((p.states||{})[k]||k)}</button>`).join("");
  if(p.mode==="sbo"&&!p.armed) return `<button class="opbtn arm" data-act="select">Select</button><span style="color:var(--text-dim);font-size:12px">interlock: select before operate</span>`;
  if(p.mode==="sbo") return `${ops}<button class="opbtn" data-act="cancel">Cancel</button>`;
  return ops;
}
function wireDetail(p){
  $("detail").querySelectorAll("button[data-act]").forEach(b=>b.onclick=()=>{
    const act=b.dataset.act;
    if(act==="select"){ control({action:"select",item:p.name}); pushEvent("CMD","cmd",`SELECT ${p.name}`); }
    else if(act==="cancel"){ control({action:"cancel",item:p.name}); pushEvent("CMD","cmd",`CANCEL ${p.name}`); }
    else if(act==="cmd"){ let val; if(b.dataset.sp){ const inp=$("sp-in"); if(!inp||inp.value==="")return; val=parseFloat(inp.value); } else val=parseInt(b.dataset.val,10);
      control({action:"command",item:p.name,value:val}); pushEvent("CMD","cmd",`OPERATE ${p.name} = ${val}`); }
  });
}

// ---- alarms list (right panel) --------------------------------------------
function buildAlarms(stations,pts){
  const a=[];
  stations.forEach(st=>{ if(!st.online) a.push({sev:"warn",id:"COM-"+st.id,cond:"STATION COMMS LOST",val:st.name}); });
  pts.forEach(p=>{
    if(p.quality==="NOTVALID") a.push({sev:"warn",id:"INV-"+p.name,cond:"NOT VALID "+p.label,val:p.station});
    if(p.fresh&&p.hi!=null&&p.value!=null&&p.value>p.hi) a.push({sev:"crit",id:"HI-"+p.name,cond:"HIGH "+p.label,val:fmt(p.value,1)+" "+p.unit+" > "+p.hi});
    if(p.fresh&&p.lo!=null&&p.value!=null&&p.value<p.lo) a.push({sev:"crit",id:"LO-"+p.name,cond:"LOW "+p.label,val:fmt(p.value,1)+" "+p.unit+" < "+p.lo});
  });
  return a;
}
function renderAlarms(alarms){
  const ids=new Set(alarms.map(a=>a.id)); [...acked].forEach(id=>{ if(!ids.has(id)) acked.delete(id); });
  const host=$("alarm-body");
  if(!alarms.length){ host.innerHTML='<div class="alarm-none">No active alarms</div>'; return; }
  host.innerHTML=alarms.slice().sort((a,b)=>(a.sev==="crit"?0:1)-(b.sev==="crit"?0:1)).map(a=>{
    const ackd=acked.has(a.id);
    return `<div class="al ${a.sev}"><div class="alr"></div><div class="al-b">
      <div class="al-cond"><span class="sv">${a.sev.toUpperCase()}</span>${esc(a.cond)}</div>
      <div class="al-meta">${esc(a.id)} &middot; ${esc(a.val||"")}</div></div>
      <div class="al-ack ${ackd?"ackd":""}">${ackd?"ACK":"UNACK"}</div></div>`;
  }).join("");
}

// ---- event recorder -------------------------------------------------------
function pushEvent(type,sevc,text){ events.unshift({t:nowUTC(),type,sev:sevc,text}); if(events.length>200)events.pop(); renderEvents(); }
function renderEvents(){
  const host=$("event-body");
  const rows=events.filter(e=>evFilter==="ALL"||e.type===evFilter);
  if(!rows.length){ host.innerHTML='<div class="recline"><span class="rr"></span><span class="rt"></span><span class="rty"></span><span class="rm" style="color:var(--text-dim)">no events</span></div>'; return; }
  host.innerHTML=rows.slice(0,140).map(e=>`<div class="recline ${e.sev==="crit"?"crit":e.sev==="warn"?"warn":e.sev==="rx"?"rx":""}">
    <span class="rr"></span><span class="rt">${e.t}</span><span class="rty">${e.type}</span><span class="rm">${esc(e.text)}</span></div>`).join("");
}
function detectEvents(stations,alarms){
  const rt=state.report&&state.report.last_report_time;
  if(rt&&rt!==lastReportSeen){ lastReportSeen=rt; pushEvent("RX","rx",`Block 2 report received (${points().length} points, cond ${state.report.cond})`); }
  stations.forEach(st=>{ if(prevStationOnline[st.id]!==undefined&&prevStationOnline[st.id]!==st.online) pushEvent("SYS",st.online?"sys":"warn",`${st.id} comms ${st.online?"restored":"lost"}`); prevStationOnline[st.id]=st.online; });
  const ids=new Set(alarms.map(a=>a.id));
  alarms.forEach(a=>{ if(!prevAlarmIds.has(a.id)) pushEvent("ALM",a.sev,`${a.id} ${a.cond} ${a.val||""}`); });
  prevAlarmIds=ids;
}

// ---- control + boot -------------------------------------------------------
async function control(body){ try{ await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); }catch(e){} }
function init(){
  $("hb-clock").textContent=nowUTC(); setInterval(()=>$("hb-clock").textContent=nowUTC(),1000);
  ["flt-search","flt-station","flt-quality","flt-ctl"].forEach(id=>$(id).addEventListener("input",renderTable));
  document.querySelectorAll("th[data-sort]").forEach(th=>th.onclick=()=>{ const k=th.dataset.sort; if(sortKey===k)sortDir*=-1; else{sortKey=k;sortDir=1;} renderTable(); });
  $("ackbtn").onclick=()=>{ buildAlarms(state.stations||[],points()).forEach(a=>acked.add(a.id)); render(); };
  $("evfilters").querySelectorAll(".lf").forEach(c=>c.onclick=()=>{ evFilter=c.dataset.ev; $("evfilters").querySelectorAll(".lf").forEach(x=>x.classList.toggle("on",x===c)); renderEvents(); });
  fetch("/api/state").then(r=>r.json()).then(s=>{state=s;render();});
  const es=new EventSource("/api/events");
  es.onmessage=ev=>{ try{ state=JSON.parse(ev.data); render(); }catch(e){} };
  es.onerror=()=>{ const e=$("hb-link"); if(e){e.textContent="offline";e.className="ai-v crit";} $("hb-lamp").className="lamp crit"; };
}
document.addEventListener("DOMContentLoaded",init);
