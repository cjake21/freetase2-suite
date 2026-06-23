"use strict";

// Real-point magnitude above this reads as a high alarm on its card. (Generic
// limit; per-point limits would live in config in a fuller build.)
const HI_LIMIT = 100;

let state = null;
const events = [];                 // {t, k, text}, newest first
const acked = new Set();           // acknowledged alarm ids
let lastReportSeen = null;         // for event-log report detection
let prevAlarmIds = new Set();      // for new-alarm event detection
let prevStationOnline = {};        // for online/offline transition events

// ---- helpers --------------------------------------------------------------

function setText(id, txt) { const e = document.getElementById(id); if (e) e.textContent = txt; }
function setV(id, txt, cls) { const e = document.getElementById(id); if (e) { e.textContent = txt; e.className = "v" + (cls ? " " + cls : ""); } }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function fmt(v, d) { return (v === null || v === undefined) ? "--" : Number(v).toFixed(d); }
function nowUTC() { return new Date().toISOString().slice(11, 19) + "Z"; }
function fmtReport(s) {
  if (!s || s.length < 14) return "--";
  return s.slice(8, 10) + ":" + s.slice(10, 12) + ":" + s.slice(12, 14) + "Z";
}
function reason(c) {
  if (c === null || c === undefined) return "--";
  const p = [];
  if (c & 0x02) p.push("INTEGRITY");
  if (c & 0x04) p.push("CHANGE");
  if (c & 0x01) p.push("INTERVAL");
  return p.length ? p.join("+") : String(c);
}

// state-point text label, e.g. {0:"OPEN",1:"CLOSED"}
function stateLabel(pt) {
  if (pt.value === null || pt.value === undefined) return "--";
  if (pt.states && pt.states[String(pt.value)] !== undefined) return pt.states[String(pt.value)];
  return String(pt.value);
}

// ---- station grid (dynamic) ----------------------------------------------
// Point values are field data delivered by the ingestion gateway over ICCP.
// Controllable points also show command controls: a command is a TASE.2 Block 5
// operate on the point's control object (<name>_ctl), which the gateway reads and
// writes down to the PLC. Command and read-back are different objects, so control
// does not fight the gateway's field updates.

function pointRow(st, pt) {
  const v = pt.value;
  const real = pt.type === "real";
  const over = real && v !== null && v !== undefined && Math.abs(v) > HI_LIMIT;
  const valText = real ? (fmt(v, 1) + (pt.unit ? " " + pt.unit : "")) : stateLabel(pt);
  const valCls = !pt.fresh ? "off" : (over ? "alarm" : (real ? "" : "en"));
  // real TASE.2 quality from the field: GOOD when valid+recent, else the validity
  // (NOTVALID/HELD/SUSPECT) or STALE if the link went silent
  const q = pt.fresh ? "GOOD" : ((pt.quality && pt.quality !== "VALID") ? pt.quality : "STALE");

  let cmd = "";
  if (pt.control) {
    const ops = operateControls(pt);
    if (pt.mode === "sbo" && !pt.armed) {
      // SBO step 1: must select before the operate controls appear
      cmd = `<div class="pt-cmd"><button class="op act" data-select="${esc(pt.name)}">SELECT</button></div>`;
    } else if (pt.mode === "sbo") {
      // SBO step 2: armed, show operate + cancel + countdown
      cmd = `<div class="pt-cmd">${ops}<button class="op" data-cancel="${esc(pt.name)}">CANCEL</button><span class="armed-t">ARMED ${pt.armed}s</span></div>`;
    } else {
      cmd = `<div class="pt-cmd">${ops}</div>`;
    }
  }

  return `
    <div class="pt">
      <div class="pt-id">${esc(pt.label)} <span>${esc(pt.name)}${pt.control ? " &middot; CTL" : ""}</span></div>
      <div class="pt-row">
        <div class="pt-val ${valCls}">${esc(valText)}</div>
        <div class="pt-q ${pt.fresh ? "good" : "stale"}">${q}</div>
      </div>
      ${cmd}
    </div>`;
}

// the operate controls themselves (discrete state buttons or a setpoint box)
function operateControls(pt) {
  if (pt.control === "setpoint") {
    return `<input type="number" step="0.01" id="sp-${esc(pt.name)}" placeholder="SETPOINT ${esc(pt.unit)}">
      <button class="op act" data-cmd="${esc(pt.name)}" data-sp="1">SEND</button>`;
  }
  const states = pt.states || { "0": "OFF", "1": "ON" };
  return Object.keys(states).map(k =>
    `<button class="op ${k === "1" ? "grn" : "red"}" data-cmd="${esc(pt.name)}" data-val="${esc(k)}">${esc(states[k])}</button>`
  ).join("");
}

function stationCard(st) {
  const cls = st.online ? "on" : "off";
  const badge = st.online ? "ONLINE" : "OFFLINE";
  return `
    <section class="station ${cls}">
      <div class="station-head">
        <span class="station-name">${esc(st.name)} <span class="station-id">${esc(st.id)}</span></span>
        <span class="station-badge ${cls}">${badge}</span>
      </div>
      <div class="station-points">
        ${st.points.map(pt => pointRow(st, pt)).join("")}
      </div>
    </section>`;
}

function renderGrid() {
  const host = document.getElementById("station-grid");
  // preserve a focused setpoint input across re-render
  const active = document.activeElement;
  const keepId = active && active.id && active.id.startsWith("sp-") ? active.id : null;
  const keepVal = keepId ? active.value : null;

  host.innerHTML = (state.stations || []).map(stationCard).join("");

  if (keepId) {
    const el = document.getElementById(keepId);
    if (el) { el.value = keepVal; el.focus(); }
  }
  wireGridControls();
}

async function control(body) {
  try {
    await fetch("/api/control", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) { /* feed will resync */ }
}

function wireGridControls() {
  document.querySelectorAll("#station-grid button[data-cmd]").forEach(btn => {
    btn.addEventListener("click", () => {
      const item = btn.dataset.cmd;
      if (btn.dataset.sp) {
        const inp = document.getElementById(`sp-${item}`);
        if (inp && inp.value !== "") {
          control({ action: "command", item, value: parseFloat(inp.value) });
          pushEvent("CMD", `SETPOINT ${item} = ${parseFloat(inp.value)}`);
          inp.value = "";
        }
      } else {
        const val = parseInt(btn.dataset.val, 10);
        control({ action: "command", item, value: val });
        pushEvent("CMD", `OPERATE ${item} = ${val}`);
      }
    });
  });
  document.querySelectorAll("#station-grid button[data-select]").forEach(btn => {
    btn.addEventListener("click", () => {
      control({ action: "select", item: btn.dataset.select });
      pushEvent("CMD", `SELECT ${btn.dataset.select}`);
    });
  });
  document.querySelectorAll("#station-grid button[data-cancel]").forEach(btn => {
    btn.addEventListener("click", () => {
      control({ action: "cancel", item: btn.dataset.cancel });
      pushEvent("CMD", `CANCEL ${btn.dataset.cancel}`);
    });
  });
}

// ---- top-level render -----------------------------------------------------

function render() {
  if (!state) return;
  const stations = state.stations || [];
  const B = !!(state.online && state.online.B);
  const onlineCount = stations.filter(s => s.online).length;
  const total = stations.length;
  const upd = fmtReport(state.report && state.report.last_report_time);

  setText("sys-sub", `DOMAIN ${state.server.domain}  |  BLT ${state.meta.blt || "--"}  |  SES ${state.server.host}:${state.server.port}`);
  const linkState = B ? (onlineCount === total ? "NORMAL" : (onlineCount > 0 ? "DEGRADED" : "STALE")) : "OFFLINE";
  setV("st-link", linkState, B ? (onlineCount === total ? "st-ok" : "st-warn") : "st-bad");
  setV("st-stations", `${onlineCount}/${total}`, onlineCount === total ? "st-ok" : (onlineCount ? "st-warn" : "st-bad"));
  const scanning = B && state.report && state.report.count > 0;
  setV("st-scan", scanning ? "RUN" : "HOLD", scanning ? "st-ok" : "st-warn");
  setV("st-upd", upd, null);

  setText("grid-sub", `${onlineCount}/${total} ONLINE`);

  renderGrid();

  // reference drawer object model
  setText("i-version", state.meta.version || "--");
  setText("i-features", state.meta.features || "--");
  setText("i-blt", state.meta.blt || "--");
  setText("i-next", state.meta.next_ts || "--");
  setText("i-dataset", state.meta.dataset || "--");
  setText("i-ts", state.meta.transferset || "--");

  const alarms = buildAlarms(stations);
  renderAlarms(alarms);
  detectEvents(stations, alarms);
}

// ---- alarms ---------------------------------------------------------------

function buildAlarms(stations) {
  const a = [];
  stations.forEach(st => {
    if (!st.online) a.push({ id: "COM-" + st.id, cond: "STATION COMMS LOST", val: st.name, lim: "NO DATA" });
    st.points.forEach(pt => {
      const v = pt.value;
      if (pt.type === "real" && pt.fresh && v !== null && v !== undefined && Math.abs(v) > HI_LIMIT)
        a.push({ id: "HI-" + pt.name, cond: "HIGH " + pt.label, val: fmt(v, 1) + " " + pt.unit, lim: "LIMIT " + HI_LIMIT });
    });
  });
  return a;
}

function renderAlarms(alarms) {
  const ids = new Set(alarms.map(a => a.id));
  [...acked].forEach(id => { if (!ids.has(id)) acked.delete(id); });

  const host = document.getElementById("alarm-rows");
  if (!alarms.length) {
    host.innerHTML = '<div class="alarm-none">NO ACTIVE ALARMS</div>';
  } else {
    host.innerHTML = alarms.map(a => {
      const ackd = acked.has(a.id);
      return `<div class="alarm-row active">
        <span class="id">${esc(a.id)}</span>
        <span class="cond">${esc(a.cond)}</span>
        <span>${esc(a.val)}</span>
        <span>${esc(a.lim)}</span>
        <span class="ack ${ackd ? "ackd" : ""}">${ackd ? "ACK" : "UNACK"}</span>
      </div>`;
    }).join("");
  }
  const unack = alarms.filter(a => !acked.has(a.id)).length;
  setV("st-alm", String(alarms.length), unack ? "st-bad" : (alarms.length ? "st-warn" : null));
}

// ---- event log ------------------------------------------------------------

function pushEvent(kind, text) {
  events.unshift({ t: nowUTC(), k: kind, text });
  if (events.length > 24) events.pop();
  renderEvents();
}

function renderEvents() {
  const host = document.getElementById("event-rows");
  if (!events.length) { host.innerHTML = '<div class="event-none">NO EVENTS</div>'; return; }
  host.innerHTML = events.map(e =>
    `<div class="event-row"><span class="et">${e.t}</span><span class="ek ${e.k.toLowerCase()}">${e.k}</span><span>${esc(e.text)}</span></div>`
  ).join("");
}

function detectEvents(stations, alarms) {
  const rt = state.report && state.report.last_report_time;
  if (rt && rt !== lastReportSeen) {
    lastReportSeen = rt;
    pushEvent("RX", `REPORT ${reason(state.report.cond)}  (${stations.reduce((n, s) => n + s.points.length, 0)} pts)`);
  }
  stations.forEach(st => {
    if (prevStationOnline[st.id] !== undefined && prevStationOnline[st.id] !== st.online)
      pushEvent(st.online ? "RX" : "ALM", `${st.name} ${st.online ? "COMMS RESTORED" : "COMMS LOST"}`);
    prevStationOnline[st.id] = st.online;
  });
  const ids = new Set(alarms.map(a => a.id));
  alarms.forEach(a => { if (!prevAlarmIds.has(a.id)) pushEvent("ALM", `${a.id} ${a.cond} ${a.val}`); });
  prevAlarmIds = ids;
}

// ---- boot -----------------------------------------------------------------

function init() {
  renderEvents();
  document.getElementById("ackbtn").addEventListener("click", () => {
    document.querySelectorAll(".alarm-row .id").forEach(e => acked.add(e.textContent));
    render();
  });
  setText("st-clock", nowUTC());
  setInterval(() => setText("st-clock", nowUTC()), 1000);

  fetch("/api/state").then(r => r.json()).then(s => { state = s; render(); });

  const es = new EventSource("/api/events");
  es.onmessage = ev => { try { state = JSON.parse(ev.data); render(); } catch (e) {} };
  es.onerror = () => { setV("st-link", "OFFLINE", "st-bad"); };
}

document.addEventListener("DOMContentLoaded", init);
