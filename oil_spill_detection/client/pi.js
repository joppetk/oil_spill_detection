const clientId = 'pi-' + Math.random().toString(36).slice(2,8);
document.getElementById('id').textContent = clientId;

const $ = (id) => document.getElementById(id);

function setText(id, v){
  const el = $(id);
  if (el) el.textContent = v;
}

function startLocalHud(){
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
  }, 500); // 2 Hz UI updates
}

const ws = new WebSocket(`wss://${location.host}/signal`);
let localStream = null;
const viewers = new Map(); // mid -> { pc }

ws.addEventListener('open', async () => {
  document.getElementById('st').textContent = 'online';
  const gps = await getGps();
  ws.send(JSON.stringify({ type:'hello', role:'pi', clientId, gps }));
  startHeartbeat();

  // capture camera (tune for sat link)
  localStream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 360, frameRate: { ideal: 10, max: 12 } },
    audio: false
  });
  document.getElementById('prev').srcObject = localStream;

  startLocalHud();
});

let ctrl = null;

ws.addEventListener('message', async (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.type === 'viewer-offer') {

    const pc = new RTCPeerConnection({ iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }] });
    viewers.set(msg.mid, { pc });

    

    // inside the 'viewer-offer' handler, AFTER you created `pc`:
    pc.ondatachannel = (ev) => {
      if (ev.channel.label === 'ctrl') {
        setText('ctrlst', 'connecting');
        ctrl = ev.channel;
        ctrl.onopen = () => {
          console.log('[ctrl] open');
          setText('ctrlst', 'open');
          startTelemPump();              // begin streaming telemetry back
          ctrl.send(JSON.stringify({ type:'hello', role:'pi' }));
        };
        ctrl.onmessage = onCtrlMessage;  // handle incoming commands
        ctrl.onclose = () => setText('ctrlst', 'closed');
        ctrl.onerror = () => setText('ctrlst', 'error');
      }
    };

    

    // add camera
    const [track] = localStream.getVideoTracks();
    const sender = pc.addTrack(track, localStream);

    // cap bitrate for sat link (~200 kbps)
    const params = sender.getParameters();
    params.encodings = [{ maxBitrate: 200_000, maxFramerate: 8, priority: 'low' }];
    sender.setParameters(params).catch(()=>{});

    pc.onicecandidate = (e) => e.candidate && ws.send(JSON.stringify({ type:'viewer-ice', mid: msg.mid, candidate: e.candidate }));
    await pc.setRemoteDescription({ type:'offer', sdp: msg.sdp });
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    ws.send(JSON.stringify({ type:'viewer-answer', mid: msg.mid, sdp: answer.sdp }));
  } else if (msg.type === 'viewer-ice') {
    const v = viewers.get(msg.mid);
    v && v.pc.addIceCandidate(msg.candidate).catch(()=>{});
  }
});



function startHeartbeat(){
  setInterval(async () => {
    const gps = await getGps();
    ws.readyState===1 && ws.send(JSON.stringify({ type:'heartbeat', gps, status: 'cam' }));
  }, 5000);
}


async function onCtrlMessage(ev){
  let m; try { m = JSON.parse(ev.data); } catch { return; }
  const id = m.id;

  // helper to ack
  const ack = (ok, extra={}) => ctrl && ctrl.readyState === 'open' &&
    ctrl.send(JSON.stringify({ type:'ack', id, ok, ...extra }));

  try {
    
    // map commands to local REST on the Pi
    if (m.cmd === 'arm_takeoff') {
      ack(true, { accepted:true }); // early ack (so server won't timeout)
      await fetch('http://127.0.0.1:8088/arm_takeoff', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ agl: m.agl ?? 12 })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));
    } else if (m.cmd === 'goto_polygon') {
      ack(true, { accepted:true }); // early ack (so server won't timeout)
      await fetch('http://127.0.0.1:8088/goto_polygon', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ polygon: m.polygon, agl: m.agl ?? 12, strategy: m.strategy || 'centroid' })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));
    } else if (m.cmd === 'rtl') {
      ack(true, { accepted:true }); // early ack (so server won't timeout)
      await fetch('http://127.0.0.1:8088/rtl', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ use_rtl: m.use_rtl !== false, agl: m.agl ?? 10 })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));
    } else if (m.cmd === 'deploy') {
      ack(true, { accepted:true }); // early ack (so server won't timeout)
      await fetch('http://127.0.0.1:8088/deploy', {
        method:'POST', headers:{'Content-Type':'application/json','X-API-Key':'SUPERSECRET'},
        body: JSON.stringify({ duration_s: m.duration_s ?? 5 })
      });
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true}));
    } else if (m.cmd === 'status') {
      ack(true, { accepted:true });
      console.log(m)
      const s = await getStatus();
      
      ctrl.send(JSON.stringify({type:'ack', id:m.id, ok:true,...s}));
    } else  {
      ack(false, { error:'unknown cmd' });
      
    }

    

  } catch(e) {
    ack(false, { error: String(e) });
    console.warn('ctrl error', e);
  }
}

// replace your fake GPS with live status from the local service
async function getStatus(){
  try{
    const r = await fetch('http://127.0.0.1:8088/status', { headers:{'X-API-Key':'SUPERSECRET'} });
    return await r.json(); // {lat, lon, abs_alt_m, rel_alt_m, ...}
  }catch(e){
    return {};
  }
}

// push telemetry back to server over the DataChannel
function startTelemPump(){
  setInterval(async ()=>{
    if (!ctrl || ctrl.readyState !== 'open') return;
    const s = await getStatus();
    if (s && s.lat) ctrl.send(JSON.stringify({ type:'telemetry', ...s }));
  }, 500); // 2 Hz
}


//async function getGps() {
  // Replace with GPSD/serial integration on the Pi.
  //return { lat: 24.47 + (Math.random()-0.5)*0.01, lon: 54.37 + (Math.random()-0.5)*0.01 };
//}

async function getGps() {
  const s = await getStatus();
  if (typeof s.lat === 'number' && typeof s.lon === 'number') {
    return { lat: s.lat, lon: s.lon };
  }
  // fallback if telemetry not available yet
  return { lat: 24.47 + (Math.random()-0.5)*0.01, lon: 54.37 + (Math.random()-0.5)*0.01 };
}
