/* NSP Learning Centre - Teacher Workload, KPI & Retention module.
   Presentation layer (report Figure 2.1): renders screens and calls the controllers' JSON API.
   No business rule lives here - every check (QR validity, register lock, weights = 100%, ...) is done by the server. */
'use strict';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const slug = v => String(v ?? '').replace(/[^A-Za-z]+/g, '-').replace(/-$/, '');
const badge = (v, cls) => v ? `<span class="badge b-${cls || slug(v)}">${esc(v)}</span>` : '';
const initials = n => n.split(' ').map(x => x[0]).slice(0, 2).join('');
const fmt = (v, d = 0) => v == null ? '–' : Number(v).toFixed(d);
const sign = v => v == null ? '' : (v > 0 ? '▲ +' : v < 0 ? '▼ ' : '● ') + Math.abs(v).toFixed(1).replace(/^/, v < 0 ? '−' : '');

const S = {me: null, page: null, users: null, inbox: false, timer: null};
const U = {};   // per-page UI state (selection, filters, open forms)

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const r = await fetch(path, {method: opts.method || 'GET',
    headers: {'Content-Type': 'application/json', 'X-User-Id': S.me ? S.me.id : ''},
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  if (opts.raw) { if (!r.ok) throw new Error('Download failed'); return r; }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || 'Request failed');
  return data;
}
const post = (p, b) => api(p, {method: 'POST', body: b || {}});

function toast(text, bad) {
  const d = document.createElement('div');
  d.textContent = text; if (bad) d.className = 'bad';
  $('#toast').appendChild(d); setTimeout(() => d.remove(), bad ? 6000 : 4200);
}
/* run an action, show the server's message on failure, then redraw */
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(typeof ok === 'function' ? ok(r) : ok); await render(); return r; }
  catch (e) { toast(e.message, true); }
}
async function download(path) {
  try {
    const r = await api(path, {raw: true});
    const name = (r.headers.get('Content-Disposition') || '').split('filename="')[1]?.replace('"', '') || 'export.csv';
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ navigation
const ICON = {
  checkin: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM15 15h2v2h-2zM18 18h2v2h-2zM14 18h2M18 14h2',
  register: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  mykpi: 'M4 19V5M4 19h16M8 15l3-4 3 2 5-6',
  feedback: 'M12 17.3l-5.4 3.2 1.4-6.1L3.3 10l6.2-.5L12 3.8l2.5 5.7 6.2.5-4.7 4.4 1.4 6.1z',
  desk: 'M3 7h18v10H3zM7 7V5h10v2M8 12h8',
  risk: 'M12 3l9 16H3zM12 10v4M12 17h.01',
  marketing: 'M3 11l14-6v14L3 13zM7 13v5',
  weights: 'M12 3v18M5 8h14M5 8l-2 6a3 3 0 0 0 4 0zM19 8l-2 6a3 3 0 0 0 4 0z',
  approvals: 'M5 12l4 4L19 6',
  kpi: 'M4 19V5M4 19h16M8 15l3-4 3 2 5-6',
  workload: 'M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  retention: 'M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM21 21v-2a4 4 0 0 0-3-3.9',
  audit: 'M9 4h6l1 2h3v15H5V6h3zM9 12h6M9 16h4',
};
const NAV = {
  Student: [['checkin', 'Check in'], ['feedback', 'Teacher feedback']],
  Teacher: [['checkin', 'Check in / out'], ['register', 'Lesson register'], ['mykpi', 'My KPI']],
  Administrator: [['desk', 'Attendance desk'], ['risk', 'Dropout risk'], ['marketing', 'Marketing spend']],
  'Head Specialist': [['weights', 'KPI weights & ratings'], ['approvals', 'Approvals'], ['kpi', 'Teacher KPI'], ['risk', 'Dropout risk']],
  Director: [['risk', 'Dropout risk'], ['kpi', 'Teacher KPI'], ['workload', 'Workload report'], ['retention', 'Retention & acquisition'], ['audit', 'Audit log']],
};
const icon = k => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${ICON[k] || ''}"/></svg>`;

function go(page) { S.page = page; S.inbox = false; location.hash = page; render(); }
function signIn(id) {
  try { localStorage.setItem('nsp_user', id); } catch (e) {}
  for (const k in U) delete U[k];
  loadMe(id).then(() => go(NAV[S.me.role][0][0]));
}
function signOut() { try { localStorage.removeItem('nsp_user'); } catch (e) {} S.me = null; render(); }
async function loadMe(id) { S.me = {id}; S.me = await api('/api/me'); }
async function resetDemo() {
  if (!confirm('Reset all demo data to the starting state?')) return;
  await post('/api/demo/reset'); for (const k in U) delete U[k];
  if (S.me) await loadMe(S.me.id); toast('Demo data reset.'); render();
}

// ------------------------------------------------------------------ shell
async function render() {
  clearInterval(S.timer); S.timer = null;
  if (!S.me) return renderSignIn();
  S.me = await api('/api/me').catch(() => S.me);
  const nav = NAV[S.me.role];
  if (!nav.some(([k]) => k === S.page)) S.page = nav[0][0];
  let body;
  try { body = await PAGES[S.page](); }
  catch (e) { body = `<div class="card msg bad"><b>Could not load this page</b>${esc(e.message)}</div>`; }
  $('#app').innerHTML = `
  <header>
    <div class="brand"><div class="logo">NSP</div><div><b>NSP Learning Centre</b><small>Teacher Workload, KPI &amp; Retention · EduTizim module</small></div></div>
    <span class="sp"></span>
    <div class="bell" style="position:relative">
      <button class="hbtn" onclick="toggleInbox()" title="Notifications" aria-label="Notifications">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/></svg>
      </button>${S.me.unread ? `<span class="count">${S.me.unread}</span>` : ''}
      ${S.inbox ? await inboxHtml() : ''}
    </div>
    <div class="who"><div class="avatar">${esc(initials(S.me.name))}</div><div><b>${esc(S.me.name)}</b><small>${esc(S.me.detail)}</small></div></div>
    <button class="hbtn" onclick="signOut()">Switch user</button>
  </header>
  <div class="wrap">
    <nav><div class="role">${esc(S.me.role)}</div>
      ${nav.map(([k, l]) => `<button class="${k === S.page ? 'on' : ''}" onclick="go('${k}')">${icon(k)}${esc(l)}</button>`).join('')}
      <div class="foot">Prototype · data stored in SQLite<br><a href="#" onclick="resetDemo();return false">Reset demo data</a></div>
    </nav>
    <main>${body}</main>
  </div>`;
  if (MOUNT[S.page]) MOUNT[S.page]();
}

async function inboxHtml() {
  const items = await api('/api/notifications');
  return `<div class="inbox"><h4>Notifications</h4>${items.length ? items.map(n =>
    `<div class="n ${n.read ? '' : 'unread'}" onclick="openNote('${esc(n.page)}')">${esc(n.message)}<br><small>${esc(n.at)}</small></div>`).join('')
    : '<div class="empty">No notifications yet.</div>'}</div>`;
}
async function toggleInbox() {
  S.inbox = !S.inbox;
  await render();
  if (S.inbox && S.me.unread) { await post('/api/notifications/read'); S.me.unread = 0; }
}
function openNote(page) { S.inbox = false; if (NAV[S.me.role].some(([k]) => k === page)) go(page); else render(); }

async function renderSignIn() {
  const u = S.users || (S.users = await api('/api/users'));
  const card = p => `<button class="person" onclick="signIn(${p.id})"><div class="avatar">${esc(initials(p.name))}</div><div><b>${esc(p.name)}</b><small>${esc(p.detail)}</small></div></button>`;
  $('#app').innerHTML = `<div class="signin"><div class="box">
    <div class="logo">NSP</div><h1>NSP Learning Centre</h1>
    <p class="lead">Teacher Workload, KPI &amp; Retention module for EduTizim — high-fidelity prototype. Sign in as one of the module's users:</p>
    <div class="people">${u.featured.map(card).join('')}</div>
    <div class="card" style="margin-top:16px"><div class="row"><span class="mut">Other teachers:</span>
      ${u.others.map(p => `<button class="btn alt sm" onclick="signIn(${p.id})">${esc(p.name)} · ${esc(p.kind)}</button>`).join('')}
      <span class="sp" style="flex:1"></span><a href="#" class="small" onclick="resetDemo();return false">Reset demo data</a></div></div>
    <p class="mut small">In the real module users sign in with their EduTizim account (NFR-01.2). Each role sees only its own screens (NFR-01.1).</p>
  </div></div>`;
}

const head = (title, sub, right = '') => `<div class="ph"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div><span class="sp"></span>${right}</div>`;
const tile = (v, l, cls = '') => `<div class="tile ${cls}"><b>${v}</b><span>${l}</span></div>`;
const monthSelect = (months, value, onchange) => `<select onchange="${onchange}">${months.map(m => `<option value="${m.value}" ${m.value === value ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}</select>`;
const options = (list, value) => list.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(l)}</option>`; }).join('');
const val = id => (document.getElementById(id) || {}).value;

// ------------------------------------------------------------------ charts (inline SVG)
function lineChart(points, h = 150) {
  if (!points.length) return '';
  const w = 520, pad = 30, vs = points.map(p => p.overall), lo = Math.max(0, Math.floor((Math.min(...vs) - 6) / 5) * 5), hi = Math.min(100, Math.ceil((Math.max(...vs) + 4) / 5) * 5);
  const x = i => pad + i * (w - pad * 2) / Math.max(1, points.length - 1), y = v => h - 24 - (v - lo) / (hi - lo || 1) * (h - 44);
  const grid = [lo, (lo + hi) / 2, hi].map(v => `<line class="grid" x1="${pad}" x2="${w - pad}" y1="${y(v)}" y2="${y(v)}"/><text x="2" y="${y(v) + 4}">${Math.round(v)}</text>`).join('');
  const path = points.map((p, i) => (i ? 'L' : 'M') + x(i) + ' ' + y(p.overall)).join(' ');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="KPI trend">${grid}<path class="ln" d="${path}"/>
    ${points.map((p, i) => `<circle class="dot ${p.label === 'Provisional' ? 'prov' : ''}" cx="${x(i)}" cy="${y(p.overall)}" r="4.5"><title>${p.month}: ${p.overall} (${p.label})</title></circle>
    <text x="${x(i)}" y="${h - 6}" text-anchor="middle">${esc(p.month)}</text><text x="${x(i)}" y="${y(p.overall) - 9}" text-anchor="middle">${fmt(p.overall, 0)}</text>`).join('')}</svg>`;
}
function barChart(items, key, h = 150, suffix = '%') {
  const w = 520, pad = 24, max = Math.max(5, ...items.map(i => i[key])), bw = (w - pad * 2) / items.length;
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img">${items.map((it, i) => {
    const bh = (it[key] / max) * (h - 46), x = pad + i * bw + bw * .2, y = h - 22 - bh;
    return `<rect class="b ${i === items.length - 1 ? 'cur' : ''}" x="${x}" y="${y}" width="${bw * .6}" height="${Math.max(1, bh)}" rx="4"><title>${it.month}: ${it[key]}${suffix}</title></rect>
      <text x="${x + bw * .3}" y="${y - 6}" text-anchor="middle">${it[key]}${suffix}</text><text x="${x + bw * .3}" y="${h - 6}" text-anchor="middle">${esc(it.month)}</text>`;
  }).join('')}</svg>`;
}

// ------------------------------------------------------------------ pages
const PAGES = {}, MOUNT = {};

/* ---------- check in / out (UC-01) ---------- */
PAGES.checkin = async () => {
  const d = await api('/api/attendance/today');
  const st = U.ci || (U.ci = {idx: 0, fails: 0, msg: ''});
  if (!d.lessons.length) return head('Check in', d.date) + '<div class="card empty">You have no lessons scheduled today.</div>';
  st.idx = Math.min(st.idx, d.lessons.length - 1);
  const l = d.lessons[st.idx];
  st.room = l.room_id;
  const t0 = l.start.split(':').map(Number), m0 = t0[0] * 60 + t0[1];
  const hm = m => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
  const e1 = l.end.split(':').map(Number);
  st.tin = st.tin && st.lesson === l.lesson_id ? st.tin : hm(m0 - (d.is_teacher ? 8 : 4));
  st.tout = st.tout && st.lesson === l.lesson_id ? st.tout : hm(e1[0] * 60 + e1[1] + 12);
  st.lesson = l.lesson_id;
  const open = l.session && l.session.flag === 'Open';
  const status = l.check_in ? `<div class="msg ok" style="margin-top:10px"><b>Checked in at ${esc(l.check_in.time)} · ${esc(l.check_in.status)}</b>${l.check_in.method === 'Manual' ? 'Confirmed manually by the administrator' : 'Recorded by QR'}${l.session ? ` · Work session: ${esc(l.session.flag)}` : ''}${l.session && l.session.check_out ? `<br>Checked out ${esc(l.session.check_out)} · worked ${Math.floor(l.session.worked / 60)} h ${l.session.worked % 60} min · overtime ${l.session.overtime} min` : ''}</div>` : '';
  return head(d.is_teacher ? 'Check in / out' : 'Check in', `${esc(d.date)} · scan the code on the classroom screen with the EduTizim app`) + `
  <div class="devices">
    <div class="phone"><div class="screen">
      <div class="bar-top"><span>EduTizim</span><span class="small">${esc(S.me.name.split(' ')[0])}</span></div>
      <div class="body stack">
        ${d.lessons.length > 1 ? `<div class="field"><label style="margin-top:0">Today's lessons</label><select onchange="U.ci.idx=+this.value;U.ci.msg='';render()">${d.lessons.map((x, i) => `<option value="${i}" ${i === st.idx ? 'selected' : ''}>${esc(x.title)}</option>`).join('')}</select></div>` : ''}
        <div class="lessoncard"><span class="mut small">${l.support ? 'Support session' : 'Lesson'} · ${esc(l.room)}</span><b>${esc(l.title.split(',')[0])}</b><span class="mut">${esc(l.start)} – ${esc(l.end)}</span>${status}</div>
        <div class="field"><label>Simulated phone time (check-in)</label><input id="tin" type="time" value="${st.tin}" onchange="U.ci.tin=this.value"></div>
        <button class="btn bigbtn" onclick="scan(false)">Scan room code</button>
        <button class="btn alt sm" onclick="scan(true)" title="Simulates a photo of the code taken 2.5 minutes ago">Test: scan an old photo of the code</button>
        ${d.is_teacher ? `<div class="field"><label>Simulated phone time (check-out)</label><input id="tout" type="time" value="${st.tout}" onchange="U.ci.tout=this.value"></div>
        <button class="btn bigbtn alt" onclick="scanOut()" ${open ? '' : 'disabled'}>Scan to check out</button>` : ''}
        ${st.msg}
      </div></div></div>
    <div>
      <div class="tablet">
        <div class="mut small" style="color:#9fb3ae">Classroom tablet · ${esc(l.room)}</div>
        <canvas id="qr" width="29" height="29"></canvas>
        <div class="room">${esc(l.room)}</div>
        <div class="ring">Code changes in <b id="cd">–</b> s</div>
        <div class="tok" id="tokv"></div>
      </div>
      <div class="card" style="margin-top:16px;max-width:420px"><h3>How this works</h3>
        <ul class="clean small mut"><li>The code holds only a signed room id and a time stamp (NFR-01.3) and is replaced every 60 s, so a photo of an old code is rejected (FR-01.1).</li>
        <li>Arriving more than 10 minutes after the start is saved as Late (FR-01.3).</li>
        ${d.is_teacher ? '<li>Check-in opens a work session; check-out closes it and calculates hours and overtime (FR-01.5).</li>' : '<li>Your scan fills in “Present” in your teacher’s register (UC-02).</li>'}
        <li>After 3 failed scans, the administrator can confirm attendance by hand (FR-01.7).</li></ul></div>
    </div>
  </div>`;
};
MOUNT.checkin = () => {
  const tick = async () => {
    const c = U.ci; if (!c || !$('#cd')) return;
    if (!c.token || c.token.room !== c.room || c.left <= 0) {
      const t = await api(`/api/rooms/${c.room}/token`).catch(() => null); if (!t) return;
      c.token = {room: c.room, value: t.value}; c.left = t.seconds_left; drawQR(t.value);
    } else if (!$('#qr').dataset.v) drawQR(c.token.value);
    c.left--; $('#cd').textContent = Math.max(0, c.left + 1); $('#tokv').textContent = c.token.value;
  };
  if (U.ci) { U.ci.token = null; tick(); S.timer = setInterval(tick, 1000); }
};
function drawQR(value) {
  const c = $('#qr'); if (!c) return; c.dataset.v = value;
  const n = 29, x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, n, n); x.fillStyle = '#111';
  let h = 2166136261; for (const ch of value) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
  const rnd = () => (h = (Math.imul(h, 1664525) + 1013904223) >>> 0) / 4294967296;
  const finder = (r, q) => r >= 0 && r < 7 && q >= 0 && q < 7;
  const fin = (i, j) => [[0, 0], [0, n - 7], [n - 7, 0]].find(([a, b]) => finder(i - a, j - b));
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const f = fin(i, j);
    if (f) { const r = i - f[0], q = j - f[1]; if (r === 0 || r === 6 || q === 0 || q === 6 || (r > 1 && r < 5 && q > 1 && q < 5)) x.fillRect(j, i, 1, 1); }
    else if (!(i < 8 && j < 8) && !(i < 8 && j > n - 9) && !(i > n - 9 && j < 8) && rnd() > .5) x.fillRect(j, i, 1, 1);
  }
}
async function scan(old) {
  const c = U.ci;
  try {
    const token = old ? (await api(`/api/rooms/${c.room}/old-token`)).value : c.token.value;
    const r = await post('/api/attendance/check-in', {token, time: val('tin')});
    c.fails = 0;
    c.msg = r.duplicate ? `<div class="msg warn"><b>Already checked in</b>Your check-in at ${esc(r.time)} (${esc(r.status)}) is saved. No duplicate record was created.</div>`
      : `<div class="msg ${r.late ? 'warn' : 'ok'}"><b>Checked in · ${esc(r.status)}</b>${esc(r.lesson)}${r.teacher ? '<br>A work session is now open.' : '<br>You are marked Present in the lesson register.'}</div>`;
  } catch (e) {
    c.fails++;
    c.msg = `<div class="msg bad"><b>Scan rejected</b>${esc(e.message)}<br><small>Failed attempts: ${c.fails}/3</small>${c.fails >= 3 ? '<br><b style="margin-top:6px">Please ask the administrator to confirm your attendance manually.</b>' : ''}</div>`;
  }
  render();
}
async function scanOut() {
  const c = U.ci;
  try {
    const r = await post('/api/attendance/check-out', {token: c.token.value, time: val('tout')});
    const h = m => `${Math.floor(m / 60)} h ${m % 60} min`;
    c.msg = `<div class="msg ${r.flag === 'Left early' ? 'warn' : 'ok'}"><b>${r.flag === 'Left early' ? 'Checked out early' : 'Checked out'} at ${esc(r.time)}</b>Worked ${h(r.worked)}.${r.overtime ? ` Overtime: ${r.overtime} min.` : ''}${r.early ? ` Shortfall: ${r.early} min.` : ''}</div>`;
  } catch (e) { c.msg = `<div class="msg bad"><b>Check-out rejected</b>${esc(e.message)}</div>`; }
  render();
}

/* ---------- lesson register (UC-02) ---------- */
const ST = [['Present', 'P'], ['Late', 'L'], ['Absent', 'A'], ['Absent - excused', 'E']];
PAGES.register = async () => {
  const st = U.reg || (U.reg = {});
  const d = await api('/api/register' + (st.lesson ? `?lesson_id=${st.lesson}` : ''));
  st.id = d.register_id;
  const counts = {}; d.rows.forEach(r => counts[r.status || 'No status'] = (counts[r.status || 'No status'] || 0) + 1);
  const missing = counts['No status'] || 0;
  const rows = d.rows.map(r => {
    const scan = r.scanned ? `<span class="badge b-ok">QR ${esc(r.scan_time)}</span>` : r.override ? `<span class="badge b-warn" title="Marked present without a scan - visible to the Administrator (FR-02.4)">Teacher override</span>` : `<span class="badge b-mut">Not scanned</span>`;
    const seg = `<div class="seg">${ST.map(([v, k]) => `<button class="${r.status === v ? 'on ' + k : ''}" ${d.locked ? 'disabled' : ''} onclick="${k === 'E' ? `U.reg.excuse=${r.student_id};render()` : `setStatus(${r.student_id},'${v}')`}">${v === 'Absent - excused' ? 'Excused' : v}</button>`).join('')}</div>`;
    let extra = '';
    if (!d.locked && st.excuse === r.student_id) extra = `<div class="inline row"><input id="exr" placeholder="Reason, e.g. illness reported by parent" style="flex:1" value="${esc(r.reason)}"><button class="btn sm" onclick="setStatus(${r.student_id},'Absent - excused',val('exr'))">Save</button><button class="btn alt sm" onclick="U.reg.excuse=null;render()">Cancel</button></div>`;
    else if (r.status === 'Absent - excused' && r.reason) extra = `<div class="small mut">Reason: ${esc(r.reason)}</div>`;
    if (d.locked && st.corr === r.student_id) extra = `<div class="inline row"><select id="cst">${options(d.statuses.filter(s => s !== r.status), '')}</select><input id="crs" placeholder="Why does it need correcting?" style="flex:1"><button class="btn sm" onclick="requestCorrection(${r.student_id})">Send to Head Specialist</button><button class="btn alt sm" onclick="U.reg.corr=null;render()">Cancel</button></div>`;
    const right = d.locked ? (r.pending_correction ? badge('Correction pending → ' + r.pending_correction, 'Pending') : `<button class="btn alt sm" onclick="U.reg.corr=${r.student_id};render()">Request correction</button>`) : '';
    return `<tr class="${st.tried && !r.status && !d.locked ? 'hl' : ''}"><td><b>${esc(r.name)}</b>${extra}</td><td>${scan}</td><td>${seg}</td>
      <td class="n">${r.absence_run >= 2 ? `<span class="badge b-${r.absence_run >= 3 ? 'bad' : 'warn'}">${r.absence_run}</span>` : r.absence_run}</td><td>${right}</td></tr>`;
  }).join('');
  return head('Lesson register', `${esc(d.lesson)} · students who scanned are already filled in (FR-02.1)`,
    d.lessons.length > 1 ? `<select onchange="U.reg.lesson=+this.value;render()">${d.lessons.map(l => `<option value="${l.id}" ${l.id === d.lesson_id ? 'selected' : ''}>${esc(l.title)}</option>`).join('')}</select>` : '') + `
  <div class="row" style="margin-bottom:12px">${['Present', 'Late', 'Absent', 'Absent - excused', 'No status'].filter(k => counts[k]).map(k => badge(`${k}: ${counts[k]}`, k === 'No status' ? 'mut' : slug(k))).join(' ')}</div>
  <div class="card"><div class="tablewrap"><table><tr><th>Student</th><th>Scan</th><th>Status</th><th class="n" title="Consecutive unexcused absences before this lesson">Absence run</th><th></th></tr>${rows}</table></div>
  ${d.locked ? `<div class="msg ok"><b>Register submitted and locked at ${esc(d.submitted_at)}${d.late_submission ? ' (late submission)' : ''}</b>Absence runs are updated and the register was sent back to EduTizim. Changes now need Head Specialist approval (FR-02.7).</div>`
    : `<div class="row" style="margin-top:14px"><button class="btn" onclick="submitRegister()">Submit register</button>${missing && st.tried ? `<span class="badge b-bad">${missing} student(s) still have no status</span>` : `<span class="mut small">${missing} without a status. Tip: Aziza Nurmatova already has 2 absences in a row.</span>`}</div>`}
  ${st.alerts ? st.alerts : ''}</div>`;
};
async function setStatus(sid, status, reason) {
  try { await post(`/api/register/${U.reg.id}/status`, {student_id: sid, status, reason: reason || ''}); U.reg.excuse = null; render(); }
  catch (e) { toast(e.message, true); }
}
async function submitRegister() {
  U.reg.tried = true;
  try {
    const r = await post(`/api/register/${U.reg.id}/submit`);
    U.reg.alerts = r.alerts.length ? `<div class="msg bad"><b>Dropout alert raised for ${esc(r.alerts.join(', '))}</b>Third absence in a row (FR-05.2). The Administrator and Head Specialist have been notified.</div>` : '';
    r.alerts.forEach(n => toast(`Dropout alert raised for ${n} - sent to Administrator and Head Specialist.`));
    render();
  } catch (e) { toast(e.message, true); render(); }
}
function requestCorrection(sid) {
  act(() => post(`/api/register/${U.reg.id}/correction`, {student_id: sid, status: val('cst'), reason: val('crs')})
    .then(r => { U.reg.corr = null; return r; }), 'Correction sent to the Head Specialist for approval.');
}

/* ---------- KPI detail (shared by My KPI and Teacher KPI) ---------- */
function kpiDetail(d, own) {
  const bars = d.breakdown.map(b => `<tr><td>${esc(b.label)}</td><td style="width:42%"><div class="row" style="flex-wrap:nowrap">${b.counts ? `<div class="bar"><i style="width:${b.value}%"></i></div><span class="num" style="width:34px;text-align:right">${fmt(b.value)}</span>` : `<span class="mut small">${b.applicable ? 'Not entered yet' : 'Not applicable / too little data'}</span>`}</div></td>
    <td class="n">${b.counts ? b.effective + '%' : '<s class="mut">' + b.weight + '%</s>'}</td><td class="n">${b.counts ? fmt(b.value * b.effective / 100, 1) : '–'}</td></tr>`).join('');
  return `<div class="grid g2">
    <div class="card"><h3>${esc(d.name)} <small>${esc(d.role)} teacher · ${esc(d.subject)} · ${esc(d.month)}</small></h3>
      <div class="row" style="margin-bottom:8px"><span style="font-size:34px;font-weight:700" class="num">${fmt(d.overall, 1)}</span>${badge(d.label)}${d.locked ? '<span class="badge b-mut">Locked snapshot</span>' : ''}</div>
      <table><tr><th>Indicator</th><th>Score (0–100)</th><th class="n">Weight</th><th class="n">Points</th></tr>${bars}</table>
      <p class="small mut">Each indicator is scored 0–100, multiplied by its weight and summed (FR-04.5). Indicators without data are left out and their weight is shared by the others (“Partial”). ${d.label === 'Provisional' ? 'Provisional: the month is still running or the Head Specialist rating is missing, so nothing is saved yet (FR-04.6).' : ''}</p>
    </div>
    <div>
      <div class="card"><h3>6-month trend <small>dashed point = provisional</small></h3>${lineChart(d.trend)}</div>
      <div class="card"><h3>Retention per group <small>${esc(d.month)}</small></h3><table><tr><th>Group</th><th class="n">Active at start</th><th class="n">Left</th><th class="n">Retention</th></tr>
        ${d.groups.map(g => `<tr><td>${esc(g.name)}</td><td class="n">${g.active_start}</td><td class="n">${g.left}</td><td class="n">${g.retention}%</td></tr>`).join('')}</table></div>
      <div class="card"><h3>Student feedback <small>anonymous (FR-04.4)</small></h3>
        <p>${d.feedback.count} responses${d.feedback.average ? ` · average <b>${d.feedback.average}</b> / 5` : ''}${d.feedback.count < 5 ? ' · fewer than 5, so the indicator is left out' : ''}</p>
        ${d.feedback.comments.length ? `<ul class="clean small">${d.feedback.comments.map(c => `<li>“${esc(c)}”</li>`).join('')}</ul>` : ''}</div>
      <div class="card"><h3>Professional achievements</h3>${d.achievements.length ? `<table>${d.achievements.map(a => `<tr><td>${esc(a.title)}<br><span class="small mut">${esc(a.kind)} · ${esc(a.on)}</span></td><td class="n">+${a.points}</td><td>${a.verified ? badge('Verified', 'ok') : badge('Waiting for Head Specialist', 'Pending')}</td></tr>`).join('')}</table>` : '<p class="mut">None recorded.</p>'}
        ${own ? `<div class="inline"><b class="small">Add an achievement (FR-04.3)</b><div class="grid g2" style="gap:8px"><div class="field"><label>Achievement</label><input id="ach_t" placeholder="e.g. IELTS 8.0 (Academic)"></div>
          <div class="field"><label>Type</label><select id="ach_k">${options(d.achievement_kinds, '')}</select></div></div>
          <div class="field"><label>Proof</label><input id="ach_p" type="file" accept=".pdf,.jpg,.png"></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" onclick="addAchievement()">Submit for verification</button><span class="small mut">Counts only after the Head Specialist verifies it.</span></div></div>` : ''}
      </div>
    </div></div>`;
}
function addAchievement() {
  const f = document.getElementById('ach_p'); const proof = f && f.files[0] ? f.files[0].name : '';
  act(() => post('/api/achievements', {title: val('ach_t'), kind: val('ach_k'), proof}), 'Achievement sent to the Head Specialist for verification.');
}
PAGES.mykpi = async () => {
  const st = U.mykpi || (U.mykpi = {});
  const d = await api('/api/kpi/me' + (st.month ? `?month=${st.month}` : ''));
  return head('My KPI', 'Only you, the Head Specialist and the Director can see this (NFR-01.1)') + kpiDetail(d, true);
};

/* ---------- teacher feedback (student) ---------- */
PAGES.feedback = async () => {
  const d = await api('/api/feedback');
  const st = U.fb || (U.fb = {});
  return head('Teacher feedback', `${esc(d.month)} · one rating per teacher each month. Your name is never shown to the teacher (FR-04.4).`) + `<div class="grid g2">` +
    d.teachers.map(t => `<div class="card"><h3>${esc(t.name)} <small>${esc(t.role)}</small></h3>
      ${t.given ? `<div class="msg ok"><b>Thank you — you rated ${t.given} / 5 this month.</b>You can rate again next month.</div>` : `
      <div class="stars row">${[1, 2, 3, 4, 5].map(n => `<button class="${st[t.id] === n ? 'on' : ''}" onclick="U.fb[${t.id}]=${n};render()" aria-label="${n} of 5">${n}</button>`).join('')}<span class="mut small">1 = very poor · 5 = excellent</span></div>
      <label>Comment (optional)</label><textarea id="fbc${t.id}" rows="2" style="width:100%" placeholder="What works well? What could be better?"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn" onclick="sendFeedback(${t.id})">Send feedback</button></div>`}</div>`).join('') + '</div>';
};
function sendFeedback(tid) {
  act(() => post('/api/feedback', {teacher_id: tid, rating: U.fb[tid], comment: val('fbc' + tid)}), 'Feedback sent anonymously. Thank you!');
}

/* ---------- administrator: attendance desk ---------- */
PAGES.desk = async () => {
  const d = await api('/api/attendance/desk');
  return head('Attendance desk', 'Manual confirmation is only for failed scans or no phone / internet (FR-01.7). Every entry is audited.') + `
  <div class="grid g2">
    <div class="card"><h3>Confirm attendance manually</h3>
      <div class="field"><label>Person</label><select id="mp">${options(d.people.map(p => [p.id, `${p.name} — ${p.role}`]), '')}</select></div>
      <div class="grid g2" style="gap:10px"><div class="field"><label>Arrival time</label><input id="mt" type="time" value="15:02"></div>
      <div class="field"><label>Reason (required)</label><input id="mr" placeholder="e.g. phone battery dead"></div></div>
      <div class="row" style="margin-top:12px"><button class="btn" onclick="manual()">Save as Manual</button></div></div>
    <div class="card"><h3>Missing check-outs</h3><p class="small mut" style="margin:-4px 0 8px">Auto-closed 60 min after the lesson (FR-01.6) and left out of worked hours until confirmed (FR-03.3).</p>
      ${d.pending_sessions.length ? `<table><tr><th>Teacher</th><th>Lesson</th><th>Real check-out</th><th></th></tr>${d.pending_sessions.map(p => `<tr><td><b>${esc(p.teacher)}</b><br><span class="small mut">${esc(p.date)} · in ${esc(p.check_in)}</span></td><td class="small">${esc(p.lesson)}</td>
        <td><input id="co${p.id}" type="time" value="${esc(p.planned_end)}" style="width:110px"><input id="cr${p.id}" placeholder="Reason" style="width:150px;margin-top:4px"></td><td><button class="btn sm" onclick="confirmSession(${p.id})">Confirm</button></td></tr>`).join('')}</table>` : '<div class="empty">Nothing to confirm.</div>'}</div>
  </div>
  <div class="card"><h3>Today's attendance log</h3>${d.log.length ? `<div class="tablewrap"><table><tr><th>Time</th><th>Person</th><th>Lesson</th><th>Method</th><th>Status</th></tr>
    ${d.log.map(l => `<tr><td class="num">${esc(l.time)}</td><td>${esc(l.name)} <span class="small mut">${esc(l.role)}</span></td><td class="small">${esc(l.lesson)}</td><td>${l.method === 'Manual' ? badge('Manual', 'warn') + `<br><span class="small mut">${esc(l.reason)}</span>` : badge('QR', 'ok')}</td><td>${badge(l.status, l.status.startsWith('Late') ? 'warn' : 'ok')}</td></tr>`).join('')}</table></div>` : '<div class="empty">No check-ins yet today.</div>'}</div>
  ${d.overrides.length ? `<div class="card"><h3>Teacher overrides today <small>marked present without a scan (FR-02.4)</small></h3><table>${d.overrides.map(o => `<tr><td>${esc(o.student)}</td><td class="small">${esc(o.lesson)}</td><td>${badge(o.status)}</td></tr>`).join('')}</table></div>` : ''}`;
};
function manual() { act(() => post('/api/attendance/manual', {user_id: val('mp'), time: val('mt'), reason: val('mr')}), r => `Saved as Manual: ${r.name} (${r.status}).`); }
function confirmSession(id) { act(() => post(`/api/attendance/sessions/${id}/confirm`, {check_out: val('co' + id), reason: val('cr' + id)}), 'Work session confirmed. Hours now count.'); }

/* ---------- dropout risk (UC-03) ---------- */
PAGES.risk = async () => {
  const st = U.risk || (U.risk = {subject: '', level: ''});
  const d = await api(`/api/risk?subject=${encodeURIComponent(st.subject)}&level=${encodeURIComponent(st.level)}`);
  const rows = d.rows.map(r => `<tr class="click ${st.sel === r.id ? 'sel' : ''}" onclick="U.risk.sel=${r.id};U.risk.form=null;render()">
    <td><b>${esc(r.name)}</b><br><span class="small mut">${esc(r.subject)} · ${esc(r.group)}</span></td><td class="n">${r.absences}</td>
    <td><div class="riskcell">${r.level === 'Not enough data' ? badge(r.level) : `<div class="bar ${r.level}"><i style="width:${r.score}%"></i></div>${badge(r.level)}<span class="num small" style="width:22px">${r.score}</span>`}</div></td>
    <td>${r.alert ? badge(r.alert) : ''}${r.overdue ? ' ' + badge('Overdue', 'bad') : ''}</td></tr>`).join('');
  let detail = '<div class="card empty">Select a student to see the risk factors and assign a follow-up.</div>';
  if (st.sel) detail = await riskDetail(st.sel, d).catch(e => `<div class="card msg bad">${esc(e.message)}</div>`);
  return head('Dropout risk dashboard', `Score uses absences in a row, negative feedback, recorded circumstances and unpaid tuition (FR-05.1)`,
    `<button class="btn alt" onclick="download('/api/risk/export')">Export CSV</button>`) + `
  <div class="grid g5" style="margin-bottom:16px">${tile(d.counts.High, 'High risk', d.counts.High ? 'bad' : '')}${tile(d.counts.Medium, 'Medium risk', d.counts.Medium ? 'warn' : '')}
    ${tile(d.open_alerts, 'Open alerts')}${tile(d.overdue, 'Overdue follow-ups', d.overdue ? 'bad' : '')}${tile(d.dropout.rate + '%', `Dropout rate, ${esc(d.month)} (${d.dropout.left} of ${d.dropout.active_start})`)}</div>
  ${d.last_sync ? `<p class="small mut" style="margin:-6px 0 12px">EduTizim data last imported ${esc(d.last_sync)} (daily export, NFR-05.3)</p>` : ''}
  <div class="split"><div class="card">
    <div class="row" style="margin-bottom:10px"><select onchange="U.risk.subject=this.value;render()"><option value="">All subjects</option>${options(d.subjects, st.subject)}</select>
      <select onchange="U.risk.level=this.value;render()"><option value="">All risk levels</option>${options(d.levels, st.level)}</select><span class="mut small">${d.rows.length} active students</span></div>
    <div class="tablewrap" style="max-height:620px;overflow:auto"><table><tr><th>Student</th><th class="n">Absence run</th><th>Risk</th><th>Alert</th></tr>${rows || '<tr><td colspan="4" class="empty">No students match the filter.</td></tr>'}</table></div>
  </div><div>${detail}</div></div>`;
};
async function riskDetail(id, dash) {
  const d = await api(`/api/risk/${id}`), f = U.risk.form, a = d.alert;
  const followForm = dash.can_follow_up && d.active ? `
    <div class="inline"><b class="small">Assign follow-up (FR-05.4)</b>
      <div class="field"><label>Action</label><input id="fu_d" value="Call parent"></div>
      <div class="grid g2" style="gap:8px"><div class="field"><label>Responsible</label><select id="fu_a">${options(d.assignees.map(x => [x.id, x.name]), '')}</select></div>
      <div class="field"><label>Due date</label><input id="fu_due" type="date" min="${d.today}" value="${d.default_due}"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn" onclick="followUp(${d.id})">Assign follow-up</button>
        ${a && a.open ? `<button class="btn alt" onclick="closeAlert(${d.id},'returned')">Student returned</button>` : ''}
        <button class="btn bad" onclick="U.risk.form='left';render()">Student left…</button></div>
      ${f === 'left' ? `<div class="row" style="margin-top:10px"><select id="wd_r"><option value="">Reason for leaving…</option>${options(d.withdrawal_reasons, '')}</select><input id="wd_n" placeholder="Note (optional)" style="flex:1"><button class="btn bad sm" onclick="closeAlert(${d.id},'withdrawn')">Record withdrawal</button></div>` : ''}
    </div>` : '';
  const circForm = dash.can_record && d.active ? `<div class="inline"><b class="small">Record a circumstance reported by the student or parent</b>
      <div class="row" style="margin-top:6px"><select id="ci_c">${options(d.circumstance_categories, 'Financial')}</select><input id="ci_n" placeholder="e.g. Parent reported job loss" style="flex:1"><button class="btn sm" onclick="recordCircumstance(${d.id})">Save</button></div></div>` : '';
  return `<div class="card"><h3>${esc(d.name)} <small>${esc(d.group)} · ${esc(d.level_of_study)} · enrolled ${esc(d.enrolled)}</small></h3>
    <div class="row">${badge(d.level)}${d.level !== 'Not enough data' ? `<span class="num"><b>${d.score}</b> / 100</span>` : ''}${d.active ? '' : badge('Withdrawn', 'mut')}</div>
    <h4 style="margin:14px 0 4px">Risk factors</h4><ul class="clean">${d.factors.map(x => `<li>${esc(x)}</li>`).join('') || '<li class="mut">No risk factors</li>'}</ul>
    <dl class="kv small"><dt>Absences in a row</dt><dd>${d.absences}</dd><dt>Feedback this month</dt><dd>${d.feedback.given} of ${d.feedback.teachers} teachers rated${d.feedback.low ? `, <b>${d.feedback.low} low (1–2)</b>` : ''}</dd>
      <dt>Tuition</dt><dd>${d.debt ? '<b>Unpaid balance</b> (EduTizim)' : 'Paid up'}</dd>${d.circumstances.map(c => `<dt>${esc(c.category)} · ${esc(c.on)}</dt><dd>${esc(c.note)}</dd>`).join('')}</dl>
    ${a ? `<h4 style="margin:14px 0 4px">Alert ${badge(a.status)} <span class="small mut" style="font-weight:400">raised ${esc(a.raised)}</span></h4>
      <ul class="clean small">${a.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      ${a.follow_ups.length ? `<table class="small"><tr><th>Follow-up</th><th>Responsible</th><th>Due</th></tr>${a.follow_ups.map(x => `<tr class="${x.overdue ? 'hl' : ''}"><td>${esc(x.description)}</td><td>${esc(x.assignee)}</td><td>${esc(x.due)} ${x.overdue ? badge('Overdue', 'bad') : x.done ? badge('Done', 'ok') : ''}</td></tr>`).join('')}</table>` : ''}
      ${a.note ? `<p class="small mut">Closed: ${esc(a.note)}</p>` : ''}` : '<p class="mut small">No alert has been raised for this student.</p>'}
    ${followForm}${circForm}</div>`;
}
function followUp(id) { act(() => post(`/api/risk/${id}/follow-up`, {description: val('fu_d'), assignee_id: val('fu_a'), due: val('fu_due')}), 'Follow-up assigned. The responsible person has been notified.'); }
function closeAlert(id, outcome) {
  act(() => post(`/api/risk/${id}/close`, {outcome, reason: val('wd_r') || '', note: val('wd_n') || ''}).then(r => { U.risk.form = null; return r; }),
    outcome === 'returned' ? 'Alert resolved: student returned.' : 'Withdrawal recorded; the alert is closed and the dropout rate updated.');
}
function recordCircumstance(id) { act(() => post(`/api/risk/${id}/circumstance`, {category: val('ci_c'), note: val('ci_n')}), 'Circumstance recorded and the risk score recalculated.'); }

/* ---------- teacher KPI dashboard (UC-04) ---------- */
PAGES.kpi = async () => {
  const st = U.kpi || (U.kpi = {month: '', subject: '', kind: ''});
  const d = await api(`/api/kpi?month=${st.month}&subject=${encodeURIComponent(st.subject)}&kind=${st.kind}`);
  st.month = d.month;
  const short = {results: 'Results', attendance: 'Attend.', punctuality: 'Punct.', retention: 'Retent.', feedback: 'Feedb.', rating: 'Rating', achievements: 'Achiev.'};
  const rows = d.rows.map((r, i) => `<tr class="click ${st.sel === r.id ? 'sel' : ''}" onclick="U.kpi.sel=${r.id};U.kpi.scroll=true;render()"><td>${i + 1}</td><td><b>${esc(r.name)}</b><br><span class="small mut">${esc(r.subject)}</span></td><td>${esc(r.role)}</td>
    ${d.keys.map(k => `<td class="n">${r.values[k] == null ? '<span class="mut">–</span>' : r.values[k]}</td>`).join('')}<td class="n"><b style="font-size:15px">${fmt(r.overall, 1)}</b></td>
    <td class="n small" style="color:${r.change > 0 ? 'var(--ok)' : r.change < 0 ? 'var(--bad)' : 'var(--mut)'}">${r.change == null ? '' : sign(r.change)}</td><td>${badge(r.label)}</td></tr>`).join('');
  const detail = st.sel ? '<div id="kdetail"></div>' + kpiDetail(await api(`/api/kpi/teacher/${st.sel}?month=${d.month}`), false) : '';
  return head('Teacher KPI dashboard', 'Seven indicators, each scored 0–100 and weighted (FR-04.5). Closed months are locked snapshots; weight changes never rewrite them.',
    `<button class="btn alt" onclick="download('/api/kpi/export?month=${d.month}')">Export CSV</button>`) + `
  <div class="card"><div class="row" style="margin-bottom:10px">${monthSelect(d.months, d.month, "U.kpi.month=this.value;render()")}
    <select onchange="U.kpi.subject=this.value;render()"><option value="">All subjects</option>${options(d.subjects, st.subject)}</select>
    <select onchange="U.kpi.kind=this.value;render()"><option value="">Main and support</option>${options(['Main', 'Support'], st.kind)}</select>
    <span class="mut small">Click a teacher for the weighted breakdown and trend.</span></div>
  <div class="tablewrap"><table><tr><th>#</th><th>Teacher</th><th>Role</th>${d.keys.map(k => `<th class="n" title="${esc(d.labels[k])}">${short[k]}</th>`).join('')}<th class="n">KPI</th><th class="n">vs last month</th><th>Status</th></tr>${rows}</table></div></div>${detail}`;
};

MOUNT.kpi = () => { if (U.kpi && U.kpi.scroll) { U.kpi.scroll = false; const e = $('#kdetail'); if (e) e.scrollIntoView({behavior: 'smooth'}); } };

/* ---------- KPI weights & ratings (Head Specialist) ---------- */
PAGES.weights = async () => {
  const d = await api('/api/kpi/weights');
  const st = U.w || (U.w = {});
  if (!st.values) st.values = Object.assign({}, d.scheduled || d.current);
  const total = d.keys.reduce((a, k) => a + (+st.values[k] || 0), 0);
  return head('KPI weights & monthly ratings', 'Weights must total 100% (FR-04.1). New weights apply from next month, so closed months never change.') + `
  <div class="grid g2"><div class="card"><h3>Indicator weights <small>${d.scheduled ? `scheduled from ${esc(d.next_month)}` : `in effect for ${esc(d.current_month)}`}</small></h3>
    <table><tr><th>Indicator</th><th class="n">${esc(d.current_month)}</th><th class="n">From ${esc(d.next_month)}</th></tr>
    ${d.keys.map(k => `<tr><td>${esc(d.labels[k])}</td><td class="n mut">${d.current[k]}%</td><td class="n"><input type="number" min="0" max="100" value="${st.values[k]}" style="width:76px;text-align:right" oninput="U.w.values['${k}']=+this.value;wTotal()"></td></tr>`).join('')}
    <tr><td><b>Total</b></td><td class="n mut">100%</td><td class="n"><span id="wsum" class="badge ${total === 100 ? 'b-ok' : 'b-bad'}">${total}%</span></td></tr></table>
    <div class="row" style="margin-top:12px"><button class="btn" onclick="saveWeights()">Save weights for ${esc(d.next_month)}</button><button class="btn alt" onclick="U.w.values=null;render()">Undo changes</button></div></div>
  <div class="card"><h3>Monthly rating, ${esc(d.current_month)} <small>0–10 (FR-04.2)</small></h3>
    <table>${d.ratings.map(r => `<tr><td><b>${esc(r.name)}</b><br><span class="small mut">${esc(r.role)} teacher</span></td>
      <td><input type="number" step="0.5" min="0" max="10" value="${r.score ?? ''}" placeholder="not rated" style="width:100px" onchange="rate(${r.id},this.value)"></td>
      <td>${r.score == null ? badge('KPI provisional', 'warn') : badge('Rated', 'ok')}</td></tr>`).join('')}</table>
    <p class="small mut">Until a teacher is rated, their KPI stays “Provisional” (FR-04.6).</p></div></div>`;
};
function wTotal() { const t = Object.values(U.w.values).reduce((a, v) => a + (+v || 0), 0), e = $('#wsum'); e.textContent = t + '%'; e.className = 'badge ' + (t === 100 ? 'b-ok' : 'b-bad'); }
function saveWeights() { act(() => post('/api/kpi/weights', {weights: U.w.values}), 'Weights saved. They apply from next month.'); }
function rate(id, v) { act(() => post('/api/kpi/ratings', {teacher_id: id, score: v}), 'Rating saved.'); }

/* ---------- approvals (Head Specialist) ---------- */
PAGES.approvals = async () => {
  const d = await api('/api/corrections');
  return head('Approvals', 'Locked registers change only with your approval; both values are kept in the audit log (FR-02.7).') + `
  <div class="card"><h3>Register correction requests</h3>${d.corrections.length ? `<table><tr><th>Student</th><th>Lesson</th><th>Change</th><th>Reason</th><th></th></tr>
    ${d.corrections.map(c => `<tr><td><b>${esc(c.student)}</b><br><span class="small mut">by ${esc(c.teacher)}, ${esc(c.requested)}</span></td><td class="small">${esc(c.lesson)}</td><td>${badge(c.old)} → ${badge(c.new)}</td><td class="small">${esc(c.reason)}</td>
    <td><div class="row"><button class="btn sm" onclick="decide(${c.id},true)">Approve</button><button class="btn bad sm" onclick="decide(${c.id},false)">Reject</button></div></td></tr>`).join('')}</table>` : '<div class="empty">No requests waiting.</div>'}</div>
  <div class="card"><h3>Achievements to verify <small>FR-04.3</small></h3>${d.achievements.length ? `<table><tr><th>Teacher</th><th>Achievement</th><th>Proof</th><th class="n">Points</th><th></th></tr>
    ${d.achievements.map(a => `<tr><td><b>${esc(a.teacher)}</b><br><span class="small mut">submitted ${esc(a.submitted)}</span></td><td>${esc(a.title)}<br><span class="small mut">${esc(a.kind)}</span></td><td class="small">📎 ${esc(a.proof)}</td><td class="n">+${a.points}</td>
    <td><button class="btn sm" onclick="verify(${a.id})">Verify</button></td></tr>`).join('')}</table>` : '<div class="empty">Nothing to verify.</div>'}</div>`;
};
function decide(id, approve) { act(() => post(`/api/corrections/${id}`, {approve}), approve ? 'Correction approved and logged.' : 'Correction rejected.'); }
function verify(id) { act(() => post(`/api/achievements/${id}/verify`), 'Achievement verified; it now counts in the KPI.'); }

/* ---------- workload report (Director) ---------- */
PAGES.workload = async () => {
  const st = U.wl || (U.wl = {month: ''});
  const d = await api('/api/workload' + (st.month ? `?month=${st.month}` : ''));
  const sum = k => d.rows.reduce((a, r) => a + r[k], 0);
  return head('Workload report', 'Hours come from QR check-in / check-out, not Excel. Planned hours come from the EduTizim timetable (FR-03).',
    monthSelect(d.months, d.month, "U.wl.month=this.value;render()")) + `
  <div class="grid g5" style="margin-bottom:16px">${tile(fmt(sum('planned_h'), 1), 'Planned hours')}${tile(fmt(sum('worked_h'), 1), 'Worked hours')}${tile(fmt(sum('overtime_h'), 1), 'Overtime hours')}
    ${tile(sum('late'), 'Late arrivals', sum('late') ? 'warn' : '')}${tile(sum('missing'), 'Missing check-outs', sum('missing') ? 'bad' : '')}</div>
  <div class="card"><div class="tablewrap"><table><tr><th>Teacher</th><th>Groups</th><th class="n">Lessons</th><th class="n">Support sessions</th><th class="n">Planned h</th><th class="n">Worked h</th><th class="n">Worked − planned</th><th class="n">Overtime h</th><th class="n">Late</th><th class="n">Left early</th><th class="n">Missing check-out</th></tr>
  ${d.rows.map(r => `<tr><td><b>${esc(r.name)}</b><br><span class="small mut">${esc(r.role)} · ${esc(r.subject)}</span></td><td class="small">${esc(r.groups)}</td><td class="n">${r.lessons}</td><td class="n">${r.support_sessions}</td>
    <td class="n">${fmt(r.planned_h, 1)}</td><td class="n">${fmt(r.worked_h, 1)}</td><td class="n" style="color:${r.difference < 0 ? 'var(--bad)' : 'var(--ok)'}">${r.difference > 0 ? '+' : ''}${fmt(r.difference, 1)}</td><td class="n">${fmt(r.overtime_h, 1)}</td>
    <td class="n">${r.late ? badge(r.late, 'warn') : 0}</td><td class="n">${r.left_early || 0}</td><td class="n">${r.missing ? badge(r.missing, 'bad') : 0}</td></tr>`).join('')}</table></div>
  <p class="small mut">Sessions with a missing check-out are left out of worked hours until the Administrator confirms them (FR-03.3).</p></div>`;
};

/* ---------- retention & acquisition (Director) ---------- */
PAGES.retention = async () => {
  const st = U.ret || (U.ret = {month: ''});
  const d = await api('/api/retention' + (st.month ? `?month=${st.month}` : ''));
  const t = d.total;
  return head('Retention & acquisition', 'Retention per teacher, group and subject (FR-05.6) and cost of acquiring a student by subject (FR-05.7)',
    monthSelect(d.months, st.month || d.months[0].value, "U.ret.month=this.value;render()")) + `
  <div class="grid g5" style="margin-bottom:16px">${tile(t.active_start, 'Active at start of month')}${tile(t.left, 'Left this month', t.left ? 'warn' : '')}${tile(t.rate + '%', 'Dropout rate')}${tile(t.retention + '%', 'Retention')}
    ${tile(d.acquisition.filter(a => a.cost != null).map(a => '$' + a.cost).join(' / ') || '–', 'Cost per new student')}</div>
  <div class="grid g2">
    <div class="card"><h3>Dropout rate, last 6 months</h3>${barChart(d.trend, 'rate')}</div>
    <div class="card"><h3>Dropout rate by subject <small>${esc(d.month)}</small></h3><table><tr><th>Subject</th><th class="n">Active at start</th><th class="n">Left</th><th class="n">Dropout rate</th></tr>
      ${d.subjects.map(s => `<tr><td>${esc(s.subject)}</td><td class="n">${s.active_start}</td><td class="n">${s.left}</td><td class="n">${s.rate}%</td></tr>`).join('')}</table>
      <h4 style="margin:16px 0 6px">Why students left (6 months)</h4><div class="row">${Object.entries(d.reasons).map(([k, v]) => badge(`${k}: ${v}`, 'mut')).join(' ') || '<span class="mut">No withdrawals</span>'}</div></div>
    <div class="card"><h3>Retention per teacher <small>${esc(d.month)}</small></h3><table><tr><th>Teacher</th><th class="n">Students at start</th><th class="n">Left</th><th class="n">Retention</th></tr>
      ${d.teachers.map(r => `<tr><td>${esc(r.name)} <span class="small mut">${esc(r.role)}</span></td><td class="n">${r.active_start}</td><td class="n">${r.left}</td><td class="n">${r.retention}%</td></tr>`).join('')}</table></div>
    <div class="card"><h3>Acquisition cost per new student <small>${esc(d.month)} · USD</small></h3><table><tr><th>Subject</th><th class="n">Marketing spend</th><th class="n">New students</th><th class="n">Cost per student</th></tr>
      ${d.acquisition.map(a => `<tr><td>${esc(a.subject)}</td><td class="n">$${fmt(a.spend)}</td><td class="n">${a.new}</td><td class="n">${a.cost == null ? badge('No enrolments', 'mut') : '<b>$' + a.cost + '</b>'}</td></tr>`).join('')}</table>
      <p class="small mut">Spend is entered monthly by the Administrator; new students come from EduTizim enrolment dates.</p></div>
  </div>`;
};

/* ---------- marketing spend (Administrator) ---------- */
PAGES.marketing = async () => {
  const d = await api('/api/marketing');
  const cur = d.history.map(h => h.values[h.values.length - 1]);
  return head('Marketing spend', `Enter this month's spend per subject (${esc(d.month)}). It feeds the Director's acquisition cost dashboard (FR-05.7).`) + `
  <div class="grid g2"><div class="card"><h3>${esc(d.month)} <small>USD</small></h3>
    ${d.history.map((h, i) => `<div class="row" style="margin-bottom:8px"><span style="width:100px">${esc(h.subject)}</span><input id="mk${i}" type="number" min="0" value="${cur[i]}" style="width:140px"><button class="btn sm" onclick="saveSpend('${h.subject}','mk${i}')">Save</button></div>`).join('')}
    <h4 style="margin:16px 0 6px">Resulting cost per new student</h4><table>${d.acquisition.map(a => `<tr><td>${esc(a.subject)}</td><td class="n">${a.new} new</td><td class="n">${a.cost == null ? badge('No enrolments', 'mut') : '$' + a.cost}</td></tr>`).join('')}</table></div>
  <div class="card"><h3>Last 6 months</h3><table><tr><th>Subject</th>${d.months.map(m => `<th class="n">${esc(m)}</th>`).join('')}</tr>
    ${d.history.map(h => `<tr><td>${esc(h.subject)}</td>${h.values.map(v => `<td class="n">${fmt(v)}</td>`).join('')}</tr>`).join('')}</table></div></div>`;
};
function saveSpend(subject, id) { act(() => post('/api/marketing', {subject, amount: val(id)}), `${subject} spend saved.`); }

/* ---------- audit log (Director) ---------- */
PAGES.audit = async () => {
  const d = await api('/api/audit');
  return head('Audit log', 'Every change to attendance, registers, ratings, weights, alerts and withdrawals: who, what, when, old and new value (NFR-01.4)') + `
  <div class="card"><div class="tablewrap"><table><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Old value</th><th>New value</th></tr>
  ${d.map(e => `<tr><td class="num small" style="white-space:nowrap">${esc(e.at)}</td><td>${esc(e.user)}</td><td><b>${esc(e.action)}</b></td><td class="small">${esc(e.entity)}</td><td class="small mut">${esc(e.old)}</td><td class="small">${esc(e.new)}</td></tr>`).join('')}</table></div></div>`;
};

// ------------------------------------------------------------------ start
(async function start() {
  const params = new URLSearchParams(location.search);
  let id = params.get('as');
  if (!id) { try { id = localStorage.getItem('nsp_user'); } catch (e) {} }
  S.page = location.hash.slice(1) || null;
  const sel = +params.get('sel');           // deep link to a detail panel, e.g. ?as=9&sel=124#risk
  if (sel && S.page === 'risk') U.risk = {subject: '', level: '', sel};
  if (sel && S.page === 'kpi') U.kpi = {month: '', subject: '', kind: '', sel};
  if (id) { try { await loadMe(id); } catch (e) { S.me = null; } }
  render();
})();
window.addEventListener('hashchange', () => { const p = location.hash.slice(1); if (p && p !== S.page && S.me) { S.page = p; render(); } });
