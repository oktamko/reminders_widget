// The Reminders window. All state comes from Python (window.pywebview.api);
// every action calls the API and re-renders from the state it returns.
'use strict';
const FALLBACK = ['#85B7EB', '#F0997B', '#97C459', '#AFA9EC', '#5DCAA5', '#ED93B1', '#FAC775', '#B4B2A9'];
const MSG_READONLY = "This reminder can't be changed on PC yet.";
const MSG_REPEATING = 'Complete repeating reminders on iPhone.';
const MSG_ALERT = 'The alert keeps its old time. Change it on iPhone.';
const $ = id => document.getElementById(id);
const g = $('g'), pk = $('pk');
let S = null, api = null, popId = null, newIds = new Set(), detailId = null, menuId = null, orig = null;
const nodes = new Map(), pending = new Map();
const rank = p => ({1: 0, 5: 1, 9: 2}[p] ?? 3);
const marks = p => ({1: '!!!', 5: '!!', 9: '!'}[p] || '·');
const cycle = p => ({0: 9, 9: 5, 5: 1, 1: 0}[p] ?? 0);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

function colour(id) {
  const i = S.lists.findIndex(l => l.id === id);
  return (S.lists[i] && S.lists[i].color) || FALLBACK[(i < 0 ? 0 : i) % FALLBACK.length];
}
function listName(id) { const l = S.lists.find(l => l.id === id); return l ? l.name : ''; }
function byId(id) { return S.reminders.find(r => r.id === id); }

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('show'), 2600);
}
function guard(r, what) {
  if (r.readonly) { toast(MSG_READONLY); return false; }
  if (what === 'complete' && r.recurring) { toast(MSG_REPEATING); return false; }
  return true;
}
async function call(name, ...args) {
  try {
    const out = await api[name](...args);
    if (out && out.ok === false) { toast(out.message || "That change couldn't be saved."); render(S); return out; }
    if (out && out.state) render(out.state);
    return out;
  } catch (e) { toast("That change couldn't be saved."); render(S); return null; }
}

// ---- list picker ----------------------------------------------------------
pk.onmouseenter = () => g.classList.add('open');
pk.onmouseleave = () => g.classList.remove('open');
function renderPicker() {
  pk.style.setProperty('--h', Math.min(34 + S.lists.length * 30 + 8, 380) + 40 + 'px');
  document.querySelectorAll('#modes button').forEach(b => b.classList.toggle('on', b.dataset.mode === (S.mode || 'desktop')));
  const o = $('opts'); o.innerHTML = '';
  S.lists.forEach((l, n) => {
    const s = document.createElement('div'); s.className = 'opt'; s.style.transitionDelay = (0.05 + n * 0.025) + 's';
    const on = S.selected.includes(l.id);
    s.innerHTML = `<span class="dot" style="background:${colour(l.id)}"></span>${esc(l.name)}` +
      `<span class="ck" style="opacity:${on ? 1 : 0};transform:scale(${on ? 1 : .3})">✓</span>`;
    s.onclick = () => {
      const sel = new Set(S.selected);
      if (sel.has(l.id) && sel.size > 1) sel.delete(l.id); else sel.add(l.id);
      call('select', [...sel]);
    };
    o.appendChild(s);
  });
}

// ---- rows -----------------------------------------------------------------
function startEdit(r, node) {
  if (!guard(r)) return;
  const t = node.querySelector('.t');
  if (t.querySelector('input')) return;
  t.innerHTML = '<input aria-label="Title">';
  const inp = t.querySelector('input'); inp.value = r.title; inp.focus(); inp.select();
  let done = false;
  const finish = save => {
    if (done) return; done = true;
    const v = inp.value.trim();
    t.textContent = r.title || '(No title)';
    if (save && v && v !== r.title.trim()) { t.textContent = v; call('rename', r.id, v); } else render(S);
  };
  inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); };
  inp.onblur = () => finish(true);
}
function complete(r, node) {
  if (!guard(r, 'complete')) return;
  const ring = node.querySelector('.ring');
  if (pending.has(r.id)) {
    clearTimeout(pending.get(r.id)); pending.delete(r.id);
    ring.classList.remove('on'); ring.style.background = ''; ring.innerHTML = ''; return;
  }
  ring.classList.add('on'); ring.style.background = colour(r.list);
  ring.innerHTML = '<span style="font-size:11px;color:#141826">✓</span>';
  pending.set(r.id, setTimeout(() => {
    pending.delete(r.id); node.style.opacity = 0; node.style.transform = 'translateX(14px)';
    call('complete', r.id, true);
  }, 1500));
}
function setPriority(r, p) { if (!guard(r)) return; popId = r.id; call('set_priority', r.id, p); }

function makeRow(id) {
  const node = document.createElement('div'); node.className = 'row'; node.tabIndex = 0; node.dataset.id = id;
  node.innerHTML = '<button class="ring" aria-label="Complete"></button><div class="txt"><div class="t"></div>' +
    '<div class="meta"></div></div><span class="pr" title="Priority"></span><button class="more" aria-label="Details">›</button>';
  node.querySelector('.ring').onclick = () => complete(byId(id), node);
  node.querySelector('.pr').onclick = () => { const r = byId(id); setPriority(r, cycle(r.priority)); };
  node.querySelector('.t').ondblclick = () => startEdit(byId(id), node);
  node.querySelector('.more').onclick = () => openDetail(id);
  node.oncontextmenu = e => { e.preventDefault(); openMenu(id, e.clientX, e.clientY); };
  node.onkeydown = e => {
    if (e.target !== node) return;
    const map = {'1': 1, '2': 5, '3': 9, '0': 0};
    if (e.key in map) { e.preventDefault(); setPriority(byId(id), map[e.key]); }
    if (e.key === 'Enter') openDetail(id);
  };
  return node;
}
function fillRow(node, r) {
  const ring = node.querySelector('.ring');
  ring.className = 'ring' + (r.readonly || r.recurring ? ' lock' : '') + (pending.has(r.id) ? ' on' : '');
  ring.style.borderColor = colour(r.list);
  if (!pending.has(r.id)) { ring.style.background = ''; ring.innerHTML = ''; }
  const t = node.querySelector('.t');
  if (!t.querySelector('input')) t.textContent = r.title || '(No title)';
  const multi = S.selected.length > 1;
  node.querySelector('.meta').innerHTML =
    (multi ? `<span style="display:inline-flex;align-items:center;gap:4px"><span class="dot" style="width:7px;height:7px;background:${colour(r.list)}"></span>${esc(listName(r.list))}</span>` : '') +
    (r.due_label ? `<span class="${r.overdue ? 'late' : ''}">${esc(r.due_label)}</span>` : '') +
    (r.notes ? '<span title="Notes">▤</span>' : '') + (r.recurring ? '<span title="Repeats">↻</span>' : '') +
    (r.readonly ? '<span title="Read-only on PC">🔒</span>' : '');
  const pr = node.querySelector('.pr'); pr.textContent = marks(r.priority);
  pr.classList.remove('pop'); if (popId === r.id) { void pr.offsetWidth; pr.classList.add('pop'); }
}
function renderRows() {
  const box = $('list'), first = new Map();
  nodes.forEach((n, k) => { if (n.isConnected) first.set(k, n.getBoundingClientRect().top); });
  const items = S.reminders;
  box.querySelectorAll('.empty').forEach(e => e.remove());
  const keep = new Set(items.map(r => r.id));
  const focused = document.activeElement;
  nodes.forEach((n, k) => { if (!keep.has(k)) { n.remove(); nodes.delete(k); } });
  items.forEach((r, i) => {
    let node = nodes.get(r.id);
    const isNew = !node && first.size > 0;
    if (!node) { node = makeRow(r.id); nodes.set(r.id, node); }
    node.style.opacity = ''; node.style.transform = '';
    fillRow(node, r);
    if (box.children[i] !== node) box.insertBefore(node, box.children[i] || null);
    if (isNew || newIds.has(r.id)) { node.classList.remove('new'); void node.offsetWidth; node.classList.add('new'); }
  });
  if (focused && focused.isConnected && document.activeElement !== focused) focused.focus({preventScroll: true});
  if (!items.length && S.signed_in === false) {
    box.insertAdjacentHTML('beforeend', '<div class="empty">Sign in to iCloud to see your reminders.<br><br><button class="signin">Sign in</button></div>');
    box.querySelector('.empty button').onclick = () => call('sign_in');
  } else if (!items.length) box.insertAdjacentHTML('beforeend', '<div class="empty">Nothing here. Add a reminder below.</div>');
  items.forEach(r => {
    const node = nodes.get(r.id); if (!first.has(r.id)) return;
    const dy = first.get(r.id) - node.getBoundingClientRect().top; if (!dy) return;
    node.style.transition = 'none'; node.style.transform = `translateY(${dy}px)`;
    requestAnimationFrame(() => { node.style.transition = 'transform .5s cubic-bezier(.2,.9,.3,1.1),opacity .4s'; node.style.transform = ''; });
  });
  popId = null; newIds.clear();
}

// ---- header, banner, status ---------------------------------------------------
function swapText(el, txt) {
  el._target = txt;                       // the last requested text wins
  if (el.textContent === txt) { el.style.opacity = 1; return; }
  el.style.opacity = 0;
  setTimeout(() => { if (el._target === txt) { el.textContent = txt; el.style.opacity = 1; } }, 160);
}
function renderHeader() {
  swapText($('ttl'), S.title);
  swapText($('sortl'), S.sort === 'priority' ? 'By priority' : 'iCloud order');
  $('sortb').classList.toggle('sw', S.sort !== 'priority');
  $('cnt').textContent = S.reminders.length;
  const st = $('status'); st.className = 'status ' + S.status + (S.syncing ? ' syncing' : '');
  st.title = {ok: 'Synced', offline: 'Offline — changes wait', session_expired: 'Sign in needed',
              transient: 'iCloud is busy', error: 'Sync problem', stopped: 'Sync stopped'}[S.status] || S.status;
  const b = S.banner, el = $('banner');
  el.className = 'banner' + (b ? ' show ' + (b.tone || 'info') : '');
  $('bannerText').textContent = b ? b.text : '';
  const act = $('bannerAction');
  act.hidden = !(b && b.action === 'sign_in');
  act.textContent = 'Sign in';
}
$('bannerAction').onclick = () => call('sign_in');
document.querySelectorAll('#modes button').forEach(b => b.onclick = () => call('set_mode', b.dataset.mode));
$('sortb').onclick = () => call('set_sort', S.sort === 'priority' ? 'icloud' : 'priority');
$('add').onkeydown = async e => {
  if (e.key !== 'Enter' || !e.target.value.trim()) return;
  const input = e.target, title = input.value.trim(); input.value = '';
  const before = new Set(S.reminders.map(r => r.id));
  const out = await call('add', title);
  if (!out || out.ok === false) { if (!input.value) input.value = title; return; }
  if (out.state) out.state.reminders.forEach(r => { if (!before.has(r.id)) newIds.add(r.id); });
};

// ---- detail panel ----------------------------------------------------------
function openDetail(id) {
  const r = byId(id); if (!r) return;
  detailId = id;
  orig = {title: r.title, notes: r.notes};   // what the fields showed when opened
  $('dTitle').value = r.title; $('dNotes').value = r.notes;
  $('dAllDay').checked = r.all_day;
  $('dDue').type = r.all_day ? 'date' : 'datetime-local';
  $('dDue').value = r.due || '';
  $('dList').textContent = listName(r.list);
  for (const k of ['dTitle', 'dDue', 'dAllDay', 'dNotes']) $(k).disabled = r.readonly;
  $('detail').classList.add('show'); $('detail').setAttribute('aria-hidden', 'false'); $('detail').inert = false;
}
function flushDetail() {
  if (!detailId || !orig) return;
  const notes = $('dNotes').value, title = $('dTitle').value;
  if (notes !== orig.notes) { orig.notes = notes; call('set_notes', detailId, notes); }
  if (title !== orig.title && title.trim()) { orig.title = title; call('rename', detailId, title.trim()); }
}
function closeDetail() { flushDetail(); detailId = null; orig = null; $('detail').classList.remove('show'); $('detail').setAttribute('aria-hidden', 'true'); $('detail').inert = true; }
$('dClose').onclick = closeDetail;
function sendDue() {
  const allDay = $('dAllDay').checked, v = $('dDue').value;
  const r = byId(detailId);
  call('set_due', detailId, v ? (allDay ? v.slice(0, 10) : v) : null, allDay)
    .then(out => { if (out && out.ok !== false && r && r.alarm) toast(MSG_ALERT); });   // probe round 4
}
$('dDue').onchange = () => { if (detailId) sendDue(); };
$('dAllDay').onchange = () => {
  if (!detailId) return;
  const v = $('dDue').value, allDay = $('dAllDay').checked;
  $('dDue').type = allDay ? 'date' : 'datetime-local';
  $('dDue').value = v ? (allDay ? v.slice(0, 10) : (v.length === 10 ? v + 'T09:00' : v)) : '';
  sendDue();
};
$('dNotes').onblur = () => flushDetail();
$('dTitle').onkeydown = e => { if (e.key === 'Enter') $('dTitle').blur(); };
$('dTitle').onblur = () => flushDetail();

// ---- context menu ----------------------------------------------------------
function openMenu(id, x, y) {
  menuId = id; const m = $('menu'); m.classList.add('show');
  const box = g.getBoundingClientRect();
  m.style.left = Math.min(x - box.left, box.width - 170) + 'px';
  m.style.top = Math.min(y - box.top, box.height - m.offsetHeight - 8) + 'px';
}
document.addEventListener('click', e => { if (!e.target.closest('#menu')) $('menu').classList.remove('show'); });
$('menu').onclick = e => {
  const btn = e.target.closest('button'); if (!btn || !menuId) return;
  const r = byId(menuId); $('menu').classList.remove('show');
  if (btn.dataset.p !== undefined) return setPriority(r, Number(btn.dataset.p));
  if (btn.dataset.a === 'rename') return startEdit(r, nodes.get(r.id));
  openDetail(r.id);
  if (btn.dataset.a === 'notes') $('dNotes').focus(); else $('dDue').focus();
};
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeDetail(); $('menu').classList.remove('show'); } });

// ---- state -----------------------------------------------------------------
function render(state) {
  if (!state || state.ok === false) return;
  if (S && S.version !== undefined && state.version !== undefined && state.version < S.version) return;
  S = state;
  renderPicker(); renderHeader(); renderRows();
  if (detailId && !byId(detailId)) closeDetail();
}
window.addEventListener('rw:state', e => render(e.detail));
$('detail').inert = true;
async function init() { api = window.pywebview.api; render(await api.state()); }
if (window.pywebview && window.pywebview.api) init(); else window.addEventListener('pywebviewready', init);

// ---- drag the desktop widget from any spot that is not a control ------------
const NO_DRAG = 'button,input,textarea,select,a,label,.row,.pn,.menu,.detail,.banner,.add,.toast,[contenteditable]';
let dragFrom = null;
g.addEventListener('mousedown', e => {
  if (e.button !== 0 || !S || (S.mode || 'window') !== 'desktop' || e.target.closest(NO_DRAG)) return;
  e.preventDefault();                       // no text selection while moving
  dragFrom = [e.clientX, e.clientY];        // the grab point inside the window stays under the pointer
});
window.addEventListener('mousemove', e => {
  if (!dragFrom) return;
  if (!(e.buttons & 1)) { dragFrom = null; return; }
  window.pywebview._jsApiCallback('pywebviewMoveWindow', [e.screenX - dragFrom[0], e.screenY - dragFrom[1]], 'move');
});
window.addEventListener('mouseup', () => { dragFrom = null; });

// ---- glass from the wallpaper (desktop mode) ---------------------------------
let glass = null, lastPos = null;
function placeWall() {
  if (!glass || !glass.wall) return;
  const r = g.getBoundingClientRect();
  const x = glass.origin[0] - window.screenX - (r.left + g.clientLeft);
  const y = glass.origin[1] - window.screenY - (r.top + g.clientTop);
  const key = x + ',' + y;
  if (key !== lastPos) {
    lastPos = key; glass.wall.style.transform = `translate3d(${x}px,${y}px,0)`;
    setTone(areaLuminance(-x, -y, g.clientWidth, g.clientHeight));
  }
}
// Mean luminance of the wallpaper cells under the card (wall coordinates, CSS px).
function areaLuminance(x, y, w, h) {
  const G = glass && glass.grid;
  if (!G || !G.values || !G.values.length) return null;
  const cl = v => Math.max(0, Math.min(v, G.cols - 1)), rl = v => Math.max(0, Math.min(v, G.rows - 1));
  const c0 = cl(Math.floor(x / G.cell)), c1 = cl(Math.floor((x + w - 1) / G.cell));
  const r0 = rl(Math.floor(y / G.cell)), r1 = rl(Math.floor((y + h - 1) / G.cell));
  let sum = 0, n = 0;
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) { sum += G.values[r * G.cols + c]; n++; }
  return n ? sum / n : null;
}
// Light tokens (dark text) on a light wallpaper; a small dead band stops flicker while dragging.
let tone = null;
function setTone(l) {
  if (l == null) return;
  const light = tone === 'light' ? l > 0.52 : tone === 'dark' ? l > 0.58 : l > 0.55;
  tone = light ? 'light' : 'dark';
  document.body.classList.toggle('light', light);
  document.body.classList.toggle('dark', !light);
}
function wallLoop() { placeWall(); if (glass && glass.wall) requestAnimationFrame(wallLoop); }
window.addEventListener('rw:glass', e => {
  const d = e.detail || {};
  tone = null;
  setTone(d.luminance ?? 0);
  document.body.classList.add('glass');
  if (d.background) document.body.style.setProperty('--deskbg', d.background);
  if (glass && glass.wall) glass.wall.remove();
  glass = {origin: d.origin || [0, 0], wall: null, grid: d.url ? d.grid : null};
  lastPos = null;
  if (d.url) {
    const w = document.createElement('div');
    w.className = 'wall';
    w.style.width = d.width + 'px'; w.style.height = d.height + 'px';
    w.style.backgroundImage = `url("${d.url}")`;
    g.insertBefore(w, g.firstChild);
    glass.wall = w;
    placeWall(); requestAnimationFrame(wallLoop);
  }
});
