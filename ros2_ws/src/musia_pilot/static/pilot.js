// Page de pilotage MUSIA : joystick, arrêt d'urgence, carte (toucher = aller à), points, tête.
// Tout passe par l'API HTTP de pilot_node (web.py) ; la caméra vient de web_video_server.
'use strict';

const $ = (id) => document.getElementById(id);
let state = null;          // dernier /api/state
let mapImg = null;         // image de la carte
let mapVersion = -1;
let mapLoading = false;
let points = [];
let target = null;         // dernière destination envoyée {x, y}

// --- API -------------------------------------------------------------------------------
async function api(method, path, body) {
  const res = await fetch('/api/' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function say(text) { $('message').textContent = text || ''; }
const act = (fn) => (...a) => fn(...a).then(() => say(''), (e) => say(e.message));

// --- état ------------------------------------------------------------------------------
const NAV = {
  repos: ['au repos', ''], en_route: ['en route', 'warn'], annulation: ['annulation…', 'warn'],
  arrive: ['arrivé', 'ok'], annule: ['annulé', ''], echec: ['échec : chemin introuvable', 'bad'],
  inconnu: ['navigation ?', ''],
};

function pill(el, text, cls) { el.textContent = text; el.className = 'pill ' + (cls || ''); }

async function poll() {
  try {
    state = await api('GET', 'state');
    pill($('link'), 'connecté', 'ok');
  } catch (e) {
    state = null;
    pill($('link'), 'hors ligne', 'bad');
    draw();
    return;
  }
  const [navText, navCls] = NAV[state.nav] || NAV.inconnu;
  pill($('nav'), navText, navCls);
  if (state.nav === 'arrive' || state.nav === 'annule') target = null;

  const b = state.battery;
  if (b) pill($('battery'), `batterie ${b.percent ?? '?'} % · ${b.voltage} V`,
    b.percent == null ? '' : b.percent < 20 ? 'bad' : b.percent < 40 ? 'warn' : 'ok');
  const base = state.base;
  if (!base) pill($('base'), 'carte moteur —', 'bad');
  else if (!base.connected) pill($('base'), 'carte moteur déconnectée', 'bad');
  else if (base.watchdog) pill($('base'), 'moteurs à l\'arrêt (chien de garde)', 'warn');
  else pill($('base'), 'carte moteur OK', 'ok');

  const stop = $('estop');
  stop.classList.toggle('on', state.estop);
  stop.textContent = state.estop ? 'ARRÊT ACTIF — toucher pour relâcher' : 'STOP';
  $('pad').classList.toggle('disabled', state.estop);

  if (state.map && state.map.version !== mapVersion) loadMap(state.map.version);
  draw();
}

function loadMap(version) {
  if (mapLoading) return;
  mapLoading = true;
  const img = new Image();
  img.onload = () => { mapImg = img; mapVersion = version; mapLoading = false; draw(); };
  img.onerror = () => { mapLoading = false; };
  img.src = 'api/map.png?v=' + version;
}

// --- carte -----------------------------------------------------------------------------
const canvas = $('map');
const ctx = canvas.getContext('2d');
let view = null;   // {scale, ox, oy} : pixel écran = ox + scale * pixel carte

function worldToScreen(x, y) {
  const m = state.map;
  const px = (x - m.origin_x) / m.resolution;
  const py = m.height - (y - m.origin_y) / m.resolution;
  return [view.ox + px * view.scale, view.oy + py * view.scale];
}

function screenToWorld(sx, sy) {
  const m = state.map;
  const px = (sx - view.ox) / view.scale;
  const py = (sy - view.oy) / view.scale;
  return [m.origin_x + px * m.resolution, m.origin_y + (m.height - py) * m.resolution];
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr; canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, w, h);
  view = null;
  if (!state || !state.map || !mapImg) {
    ctx.fillStyle = '#8b96a1';
    ctx.font = '14px system-ui';
    ctx.fillText(state ? 'en attente de la carte (lidar, slam)…' : 'robot injoignable', 16, 28);
    return;
  }
  const m = state.map;
  const scale = Math.min(w / m.width, h / m.height);
  view = { scale, ox: (w - m.width * scale) / 2, oy: (h - m.height * scale) / 2 };
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapImg, view.ox, view.oy, m.width * scale, m.height * scale);

  const px = Math.max(3, 0.05 / m.resolution * scale);   // 5 cm, au moins 3 px
  ctx.font = '12px system-ui';
  for (const p of points) {
    const [sx, sy] = worldToScreen(p.x, p.y);
    ctx.fillStyle = '#3d8bfd';
    ctx.beginPath(); ctx.arc(sx, sy, px, 0, 2 * Math.PI); ctx.fill();
    ctx.fillStyle = '#e8ecef';
    ctx.fillText(p.name, sx + px + 3, sy + 4);
  }
  if (target) {
    const [sx, sy] = worldToScreen(target.x, target.y);
    ctx.strokeStyle = '#e0a028'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(sx, sy, px * 2, 0, 2 * Math.PI); ctx.stroke();
  }
  if (state.pose) {
    const [sx, sy] = worldToScreen(state.pose.x, state.pose.y);
    const r = Math.max(8, 0.13 / m.resolution * scale);  // ~ demi-longueur du robot
    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(-state.pose.yaw);   // y écran vers le bas
    ctx.fillStyle = '#2fb36d';
    ctx.beginPath(); ctx.moveTo(r, 0); ctx.lineTo(-r * 0.7, r * 0.6); ctx.lineTo(-r * 0.7, -r * 0.6);
    ctx.closePath(); ctx.fill();
    ctx.restore();
  }
}

canvas.addEventListener('click', act(async (ev) => {
  if (!view || !state || state.estop) return;
  const rect = canvas.getBoundingClientRect();
  const [x, y] = screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
  await api('POST', 'goal', { x, y });
  target = { x, y };
  draw();
}));
window.addEventListener('resize', draw);

// --- joystick --------------------------------------------------------------------------
const pad = $('pad'), knob = $('knob');
let stick = null;          // {x, z} entre -1 et 1, null = relâché
let driveBusy = false;

function speed() { return $('speed').value / 100; }
$('speed').addEventListener('input', () => { $('speed-v').textContent = $('speed').value + ' %'; });

function setStick(clientX, clientY) {
  const r = pad.getBoundingClientRect();
  const R = r.width / 2;
  let dx = clientX - (r.left + R), dy = clientY - (r.top + R);
  const d = Math.hypot(dx, dy), max = R - 30;
  if (d > max) { dx *= max / d; dy *= max / d; }
  knob.style.transform = `translate(${dx}px, ${dy}px)`;
  // haut = avancer ; droite = tourner à droite (z négatif)
  stick = { x: -dy / max, z: -dx / max };
}

function release() {
  if (!stick) return;
  stick = null;
  knob.style.transform = '';
  sendDrive(0, 0);
}

function sendDrive(x, z) {
  if (driveBusy && (x || z)) return;      // pas de file d'attente : on saute un envoi
  driveBusy = true;
  api('POST', 'drive', { x, z }).catch((e) => say(e.message)).finally(() => { driveBusy = false; });
}

pad.addEventListener('pointerdown', (ev) => {
  if (state && state.estop) return;
  pad.setPointerCapture(ev.pointerId);
  setStick(ev.clientX, ev.clientY);
});
pad.addEventListener('pointermove', (ev) => { if (stick) setStick(ev.clientX, ev.clientY); });
pad.addEventListener('pointerup', release);
pad.addEventListener('pointercancel', release);

// clavier (flèches) sur ordinateur
const keys = new Set();
const KEYS = { ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1 };
window.addEventListener('keydown', (ev) => {
  if (!KEYS[ev.key] || ev.target.tagName === 'INPUT') return;
  ev.preventDefault(); keys.add(ev.key);
});
window.addEventListener('keyup', (ev) => {
  if (!keys.delete(ev.key)) return;
  if (!keys.size) sendDrive(0, 0);
});
window.addEventListener('blur', () => { if (keys.size) { keys.clear(); sendDrive(0, 0); } release(); });

// 10 envois par seconde tant qu'on commande ; le robot s'arrête seul 0,5 s après le dernier
setInterval(() => {
  let cmd = stick;
  if (!cmd && keys.size) {
    cmd = { x: (keys.has('ArrowUp') ? 1 : 0) - (keys.has('ArrowDown') ? 1 : 0),
            z: (keys.has('ArrowLeft') ? 1 : 0) - (keys.has('ArrowRight') ? 1 : 0) };
  }
  if (cmd) sendDrive(cmd.x * speed(), cmd.z * speed());
}, 100);

// --- boutons ---------------------------------------------------------------------------
$('estop').addEventListener('click', act(async () => {
  const on = !(state && state.estop);
  release();
  await api('POST', 'estop', { on });
  if (on) target = null;
  await poll();
}));

$('cancel').addEventListener('click', act(async () => {
  await api('POST', 'cancel');
  target = null;
  draw();
}));

// --- points ----------------------------------------------------------------------------
async function loadPoints() {
  points = (await api('GET', 'points')).points;
  const ul = $('points');
  ul.replaceChildren();
  if (!points.length) {
    const li = document.createElement('li');
    li.innerHTML = '<small>aucun point : conduisez le robot puis « Enregistrer ici »</small>';
    ul.append(li);
  }
  for (const p of points) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = p.name;
    const go = document.createElement('button');
    go.textContent = 'Aller';
    go.onclick = act(async () => {
      await api('POST', `points/${encodeURIComponent(p.name)}/go`);
      target = { x: p.x, y: p.y };
      draw();
    });
    const del = document.createElement('button');
    del.textContent = '×';
    del.title = 'supprimer';
    del.onclick = act(async () => {
      if (!confirm(`Supprimer le point « ${p.name} » ?`)) return;
      await api('DELETE', `points/${encodeURIComponent(p.name)}`);
      await loadPoints();
    });
    li.append(name, go, del);
    ul.append(li);
  }
  draw();
}

$('save-point').addEventListener('submit', act(async (ev) => {
  ev.preventDefault();
  await api('POST', 'points', { name: $('point-name').value });
  $('point-name').value = '';
  await loadPoints();
}));

// --- tête ------------------------------------------------------------------------------
let headTimer = null;
function sendHead() {
  clearTimeout(headTimer);
  headTimer = setTimeout(() => api('POST', 'head', { deg: -Number($('head').value) })
    .catch((e) => say(e.message)), 80);   // curseur à droite = regarder à droite (angle négatif)
}
$('head').addEventListener('input', sendHead);
$('head-center').addEventListener('click', () => { $('head').value = 0; sendHead(); });

// --- caméra ----------------------------------------------------------------------------
function startCamera(port) {
  const img = $('camera');
  const url = `http://${location.hostname}:${port}/stream?topic=/camera/image_raw&type=mjpeg&quality=50`;
  img.onerror = () => {   // serveur vidéo pas encore prêt : nouvel essai dans 5 s
    img.hidden = true; $('camera-off').hidden = false;
    setTimeout(() => { img.src = url + '&t=' + Date.now(); }, 5000);
  };
  img.onload = () => { img.hidden = false; $('camera-off').hidden = true; };
  img.src = url;
}

// --- démarrage -------------------------------------------------------------------------
(async () => {
  await poll();
  startCamera(state ? state.camera_port : 8081);
  loadPoints().catch((e) => say(e.message));
  setInterval(poll, 300);
})();
