const clientId = 'pi-' + Math.random().toString(36).slice(2,8);
document.getElementById('id').textContent = clientId;

const $ = (id) => document.getElementById(id);
const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };

let ws = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let reconnectEnabled = true;
let localStream = null;
const viewers = new Map(); // mid -> { pc }
let ctrl = null;

let hudTimer = null;    // prevents multiple HUD intervals

let hbTimer = null;
let telemTimer = null;

let currentMaxBitrate = 250_000; // default from HTML
let currentMaxFps = 10;



function readQualityUI(){
  const br = parseInt(document.getElementById('brSel')?.value || "250000", 10);
  const fps = parseInt(document.getElementById('fpsSel')?.value || "10", 10);
  return { br, fps };
}

// Guardrails (auto-adjust)
function normalizeQuality(br, fps){
  // Suggested safe ceilings for stability
  if (br <= 120_000 && fps > 8) fps = 8;
  if (br <= 260_000 && fps > 10) fps = 10;
  return { br, fps };
}



function wsUrl() {
  return `wss://${location.host}/signal`;
}

function setClass(id, cls) {
  const el = $(id);
  if (!el) return;
  el.classList.remove('gpio-on', 'gpio-off', 'gpio-warn');
  if (cls) el.classList.add(cls);
}

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return String(ts);
  }
}

function updateGpioHud(gpio) {
  if (!gpio) {
    setText('deployPin', '—');
    setText('deployState', '—');
    setText('deployLast', '—');
    setClass('deployState', '');
    return;
  }

  const pin = gpio.pin ?? '—';
  const state = gpio.state ?? '—';
  const last = gpio.last_toggle ?? null;

  setText('deployPin', pin);
  setText('deployState', state);
  setText('deployLast', fmtTs(last));

  if (state === 'HIGH' || state === 'ON' || state === 'DEPLOYING') {
    setClass('deployState', 'gpio-on');
  } else if (state === 'LOW' || state === 'OFF' || state === 'IDLE') {
    setClass('deployState', 'gpio-off');
  } else {
    setClass('deployState', 'gpio-warn');
  }
}

/* ---------- Camera ---------- */

async function applySenderEncodingAll(){
  for (const [mid, v] of viewers.entries()){
    if (!v?.sender) continue;

    try{
      const params = v.sender.getParameters();
      params.encodings = params.encodings && params.encodings.length ? params.encodings : [{}];

      params.encodings[0].maxBitrate = currentMaxBitrate;
      params.encodings[0].maxFramerate = currentMaxFps;
      params.encodings[0].priority = 'low';

      await v.sender.setParameters(params);
      console.log(`[qos] applied to ${mid}: br=${currentMaxBitrate} fps=${currentMaxFps}`);
    }catch(e){
      console.warn('[qos] setParameters failed', e);
    }
  }
}

async function startCamera() {
  // capture camera (tune for sat link)
  localStream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 360, frameRate: { ideal: 10, max: 12 } },
    audio: false
  });
  const v = $('prev');
  if (v) v.srcObject = localStream;
} 

async function startCamera() {
  // Use UI-selected quality for capture FPS too
  let q = readQualityUI();
  q = normalizeQuality(q.br, q.fps);
  currentMaxBitrate = q.br;
  currentMaxFps = q.fps;

  const baseVideo = { width: 640, height: 360 };

  // Try strict FPS first; fallback if camera rejects constraints
  try {
    localStream = await navigator.mediaDevices.getUserMedia({
      video: { ...baseVideo, frameRate: { ideal: currentMaxFps, max: currentMaxFps } },
      audio: false
    });
  } catch (e) {
    console.warn('[cam] strict fps failed; falling back', e);
    localStream = await navigator.mediaDevices.getUserMedia({
      video: { ...baseVideo, frameRate: { ideal: currentMaxFps, max: 12 } },
      audio: false
    });
  }

  const v = $('prev');
  if (v) v.srcObject = localStream;
}



function stopCamera() {
  try {
    const v = $('prev');
    if (v) v.srcObject = null;
  } catch {}
  if (localStream) {
    localStream.getTracks().forEach(t => { try { t.stop(); } catch {} });
  }
  localStream = null;
} 



async function restartCameraAndReplaceTracks(){
  // stop + restart camera
  stopCamera();
  await new Promise(r => setTimeout(r, 300));
  await startCamera();

  const newTrack = localStream?.getVideoTracks?.()[0];
  if (!newTrack) return;

  // replace track into existing senders (no renegotiation needed)
  for (const [mid, v] of viewers.entries()){
    if (!v?.sender) continue;
    try {
      await v.sender.replaceTrack(newTrack);
    } catch (e) {
      console.warn(`[cam] replaceTrack failed for ${mid}`, e);
    }
  }

  // ensure encoder constraints match new settings
  await applySenderEncodingAll();
} 



/* ---------- Viewers / PeerConnections ---------- */
function closeViewers() {
  for (const [mid, v] of viewers.entries()) {
    try { v.pc && v.pc.close(); } catch {}
  }
  viewers.clear();
}

/* ---------- Timers ---------- */
function stopHeartbeat() {
  if (hbTimer) clearInterval(hbTimer);
  hbTimer = null;
}

function stopTelemPump() {
  if (telemTimer) clearInterval(telemTimer);
  telemTimer = null;
}

function startHeartbeat(){
  stopHeartbeat();
  hbTimer = setInterval(async () => {
    const gps = await getGps();
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type:'heartbeat', gps, status: 'cam' }));
    }
  }, 5000);
}

// push telemetry back to server over the DataChannel
function startTelemPump(){
  stopTelemPump();
  telemTimer = setInterval(async ()=>{
    if (!ctrl || ctrl.readyState !== 'open') return;
    const s = await getStatus();
    if (s && s.lat) ctrl.send(JSON.stringify({ type:'telemetry', ...s }));
  }, 500); // 2 Hz
}

/* ---------- WebSocket / Signaling ---------- */
function disconnectWs() {
  clearReconnectTimer();

  if (!ws) return;
  try {
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    ws.close();
  } catch {}
  ws = null;
}

function clearReconnectTimer(){
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
}

function nextReconnectDelayMs(){
  // 0.5s, 1s, 2s, 4s, ... up to 30s, with jitter
  const base = Math.min(30000, 500 * Math.pow(2, reconnectAttempt));
  const jitter = Math.floor(Math.random() * 400); // 0..399ms
  return base + jitter;
}

function scheduleReconnect(reason = 'unknown'){
  if (!reconnectEnabled) return;

  // If already connected/connecting, don't schedule
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  // Avoid stacking timers
  if (reconnectTimer) return;

  const delay = nextReconnectDelayMs();
  console.log(`[ws] schedule reconnect in ${delay}ms (attempt=${reconnectAttempt}, reason=${reason})`);

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;

    // If we're offline, wait for 'online' event
    if (navigator.onLine === false) {
      scheduleReconnect('offline');
      return;
    }

    reconnectAttempt += 1;
    connectWs();
  }, delay);
}

/* function startLocalHud(){
  const fmtBool = (v) => (v === null || v === undefined) ? '—' : String(v);
  const fmtNum  = (v, n=1) => (typeof v === 'number') ? v.toFixed(n) : '—';

  setInterval(async () => {
    const s = await getStatus();

    // If mixed-content blocks the fetch, s will be {}
    const ok = s && (s.droneId || s.status || s.lat || s.lon);
    setText('telemst', ok ? 'LIVE' : 'OFFLINE');

    setText('droneId', s.droneId ?? '—');
    setText('status',  s.status ?? '—');
    setText('fm',      s.flight_mode ?? '—');
    setText('armed',   fmtBool(s.armed));
    setText('inair',   fmtBool(s.in_air));

    if (typeof s.battery_pct === 'number') setText('batt', `${s.battery_pct.toFixed(1)}%`);
    else setText('batt', '—');

    setText('gpsfix', s.gps_fix ?? '—');
    setText('sats',   (s.satellites_used ?? '—'));

    if (typeof s.lat === 'number' && typeof s.lon === 'number'){
      setText('pos', `${s.lat.toFixed(6)}, ${s.lon.toFixed(6)}`);
    } else {
      setText('pos', '—');
    }

    const rel = (typeof s.rel_alt_m === 'number') ? `${s.rel_alt_m.toFixed(1)}m` : '—';
    const abs = (typeof s.abs_alt_m === 'number') ? `${s.abs_alt_m.toFixed(1)}m` : '—';
    setText('alt', `${rel} / ${abs}`);

    updateGpioHud(s.gpio);

  }, 500); // 2 Hz UI updates
} */

function startLocalHud(){
  if (hudTimer) return; // ✅ prevent duplicates on reconnect

  const fmtBool = (v) => (v === null || v === undefined) ? '—' : String(v);

  hudTimer = setInterval(async () => {
    const s = await getStatus();

    const ok = s && (s.droneId || s.status || s.lat || s.lon);
    setText('telemst', ok ? 'LIVE' : 'OFFLINE');

    setText('droneId', s.droneId ?? '—');
    setText('status',  s.status ?? '—');
    setText('fm',      s.flight_mode ?? '—');
    setText('armed',   fmtBool(s.armed));
    setText('inair',   fmtBool(s.in_air));

    if (typeof s.battery_pct === 'number') setText('batt', `${s.battery_pct.toFixed(1)}%`);
    else setText('batt', '—');

    setText('gpsfix', s.gps_fix ?? '—');
    setText('sats',   (s.satellites_used ?? '—'));

    if (typeof s.lat === 'number' && typeof s.lon === 'number'){
      setText('pos', `${s.lat.toFixed(6)}, ${s.lon.toFixed(6)}`);
    } else {
      setText('pos', '—');
    }

    const rel = (typeof s.rel_alt_m === 'number') ? `${s.rel_alt_m.toFixed(1)}m` : '—';
    const abs = (typeof s.abs_alt_m === 'number') ? `${s.abs_alt_m.toFixed(1)}m` : '—';
    setText('alt', `${rel} / ${abs}`);

    updateGpioHud(s.gpio);
  }, 500);
}

function connectWs() {
  setText('st', 'connecting…');

  ws = new WebSocket(wsUrl());

  ws.addEventListener('open', async () => {
    clearReconnectTimer();
    reconnectAttempt = 0;      // ✅ reset backoff on success

    setText('st', 'online');
    const gps = await getGps();
    ws.send(JSON.stringify({ type:'hello', role:'pi', clientId, gps }));
    startHeartbeat();
    startLocalHud();
  });

  ws.addEventListener('close', () => {
    setText('st', 'offline');
    setText('ctrlst', '—');

    stopHeartbeat();           // ✅ prevent duplicated heartbeat loops
    // (you can keep camera running; no need to stop it for reconnect)
    scheduleReconnect('close');
  });

  ws.addEventListener('error', () => {
    setText('st', 'error');
    // Some browsers also fire 'close' after 'error'; scheduleReconnect() prevents duplicates.
    scheduleReconnect('error');
  });

  ws.addEventListener('message', async (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === 'viewer-offer') {
      // require camera ready
      if (!localStream) {
        try { await startCamera(); } catch (e) { console.warn('camera start failed', e); }
      }

      const pc = new RTCPeerConnection({ iceServers: [
        { urls: ['stun:stun.l.google.com:19302'] },
        { urls: ['stun:stun1.l.google.com:19302'] }
      ] 
    });
      // viewers.set(msg.mid, { pc });
      viewers.set(msg.mid, { pc, sender: null });
      pc.ondatachannel = (ev) => {
        if (ev.channel.label === 'ctrl') {
          ctrl = ev.channel;
          setText('ctrlst', 'connecting');

          ctrl.onopen = () => {
            setText('ctrlst', 'open');
            startTelemPump(); // begin streaming telemetry back
            ctrl.send(JSON.stringify({ type:'hello', role:'pi' }));
          };
          ctrl.onclose = () => setText('ctrlst', 'closed');
          ctrl.onerror = () => setText('ctrlst', 'error');
          ctrl.onmessage = onCtrlMessage;
        }
      };

      // add camera
      const [track] = localStream.getVideoTracks();
      const sender = pc.addTrack(track, localStream);
      const entry = viewers.get(msg.mid);
      if (entry) entry.sender = sender;

      // selectable bitrate for sat link (~250 kbps)
      const params = sender.getParameters();
      //params.encodings = [{ maxBitrate: 200_000, maxFramerate: 8, priority: 'low' }];
      params.encodings = params.encodings && params.encodings.length ? params.encodings : [{}];
      params.encodings[0].maxBitrate = currentMaxBitrate;
      params.encodings[0].maxFramerate = currentMaxFps;
      params.encodings[0].priority = 'low';
      sender.setParameters(params).catch(()=>{});

      pc.onicecandidate = (e) => e.candidate && ws.send(JSON.stringify({ type:'viewer-ice', mid: msg.mid, candidate: e.candidate }));
      await pc.setRemoteDescription({ type:'offer', sdp: msg.sdp });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      ws.send(JSON.stringify({ type:'viewer-answer', mid: msg.mid, sdp: answer.sdp }));
    }
    else if (msg.type === 'viewer-ice') {
      const v = viewers.get(msg.mid);
      v && v.pc.addIceCandidate(msg.candidate).catch(()=>{});
    }
  });
}

/* ---------- Restart without reload ---------- */
async function restartStream() {
  
  const btn = $('restartBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Restarting…'; }

  reconnectEnabled = false;     // pause auto reconnect during manual restart
  clearReconnectTimer();

  try {
    // 1) Stop timers + channels
    stopHeartbeat();
    stopTelemPump();
    try { if (ctrl) ctrl.close(); } catch {}
    ctrl = null;
    setText('ctrlst', '—');

    // 2) Close viewers + WS + camera
    closeViewers();
    disconnectWs();
    stopCamera();

    // small pause helps release camera device cleanly on Pi
    await new Promise(r => setTimeout(r, 300));

    // 3) Start again
    await startCamera();     // user gesture via button helps camera permissions
    connectWs();

    reconnectEnabled = true;
    reconnectAttempt = 0;

  } catch (e) {
    console.warn('restartStream failed:', e);
    setText('st', 'restart failed');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Restart Stream'; }
  }
}

/* ---------- Commands ---------- */
async function onCtrlMessage(ev){
  let m; try { m = JSON.parse(ev.data); } catch { return; }
  const id = m.id;

  const ack = (ok, extra={}) => ctrl && ctrl.readyState === 'open' &&
    ctrl.send(JSON.stringify({ type:'ack', id, ok, ...extra }));

  try {
    if (m.cmd === 'arm_takeoff') {
      ack(true, { accepted:true });
      await fetch('http://127.0.0.1:8088/arm_takeoff', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ agl: m.agl ?? 12 })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));

    } else if (m.cmd === 'goto_polygon') {
      ack(true, { accepted:true });
      await fetch('http://127.0.0.1:8088/goto_polygon', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ polygon: m.polygon, agl: m.agl ?? 12, strategy: m.strategy || 'centroid' })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));

    } else if (m.cmd === 'rtl') {
      ack(true, { accepted:true });
      await fetch('http://127.0.0.1:8088/rtl', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ use_rtl: m.use_rtl !== false, agl: m.agl ?? 10 })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));

    } else if (m.cmd === 'deploy') {
      ack(true, { accepted:true });
      await fetch('http://127.0.0.1:8088/deploy', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ duration_s: m.duration_s ?? 5 })
      });
      const s = await getStatus();
      updateGpioHud(s.gpio);
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));

    } else if (m.cmd === 'status') {
      ack(true, { accepted:true });
      const s = await getStatus();
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true, ...s}));

    } else {
      ack(false, { error:'unknown cmd' });
    }
  } catch(e) {
    ack(false, { error: String(e) });
    console.warn('ctrl error', e);
  }
}

/* ---------- Local status helpers ---------- */
async function getStatus(){
  try{
    const r = await fetch('http://127.0.0.1:8088/status', { headers:{'X-API-Key':'SUPERSECRET'} });
    return await r.json();
  }catch(e){
    return {};
  }
}

async function getGps() {
  const s = await getStatus();
  if (typeof s.lat === 'number' && typeof s.lon === 'number') return { lat: s.lat, lon: s.lon };
  return { lat: 24.47 + (Math.random()-0.5)*0.01, lon: 54.37 + (Math.random()-0.5)*0.01 };
}

/* ---------- Wire buttons + boot ---------- */
document.addEventListener('DOMContentLoaded', async () => {

  // restore previous choices
  const savedBr  = localStorage.getItem('pi_br');
  const savedFps = localStorage.getItem('pi_fps');
  if (savedBr && document.getElementById('brSel')) document.getElementById('brSel').value = savedBr;
  if (savedFps && document.getElementById('fpsSel')) document.getElementById('fpsSel').value = savedFps;

  // set defaults
  let q = readQualityUI();
  q = normalizeQuality(q.br, q.fps);
  currentMaxBitrate = q.br;
  currentMaxFps = q.fps;

  const applyBtn = document.getElementById('applyQBtn');
  applyBtn && applyBtn.addEventListener('click', async () => {
    let q = readQualityUI();
    q = normalizeQuality(q.br, q.fps);

    // reflect clamps back to UI
    document.getElementById('brSel').value = String(q.br);
    document.getElementById('fpsSel').value = String(q.fps);

    const prevFps = currentMaxFps;

    currentMaxBitrate = q.br;
    currentMaxFps = q.fps;

    localStorage.setItem('pi_br', String(q.br));
    localStorage.setItem('pi_fps', String(q.fps));

    // If FPS changed, we must restart capture to really change camera FPS
    if (prevFps !== currentMaxFps) {
      await restartCameraAndReplaceTracks();
    } else {
      await applySenderEncodingAll();
    }
  });

  // refresh button (if you still keep it)
  const refreshBtn = $('refreshBtn');
  refreshBtn && refreshBtn.addEventListener('click', () => location.reload());

  const restartBtn = $('restartBtn');
  restartBtn && restartBtn.addEventListener('click', restartStream);

  // initial start
  try {
    await startCamera();
  } catch (e) {
    console.warn('camera start failed on load (permission?)', e);
  }
  connectWs();
});

window.addEventListener('online', () => {
  console.log('[net] online -> reconnect');
  clearReconnectTimer();
  scheduleReconnect('online');
});

window.addEventListener('offline', () => {
  console.log('[net] offline');
  setText('st', 'offline');
});
