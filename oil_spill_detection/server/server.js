// server/server.js
import https from 'https';
import 'dotenv/config';
import express from 'express';
import { rectPolygon } from './scheduler.js';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import { createPeerForClient } from './rtc.js';
import { Scheduler } from './scheduler.js';
import { spawn } from 'child_process';
import area from '@turf/area';
import wellknown from 'wellknown';
import { createProxyMiddleware } from 'http-proxy-middleware';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const certDir = path.join(__dirname, 'certs');
const key  = fs.readFileSync(path.join(certDir, 'cert.key'));
const cert = fs.readFileSync(path.join(certDir, 'cert.crt'));

const OPS = {
  PYTHON: process.env.OPS_PYTHON || 'python', // path to your py env
  POLLER_DIR: path.resolve(__dirname, '..', 'python-poller'),
  CKPT_UNET: process.env.CKPT_UNET || path.resolve(__dirname, '..', 'python-poller', 'weights', 'unet_sar2ch_b48_prod.pth'),
  CFG_UNET:  process.env.CFG_UNET  || path.resolve(__dirname, '..', 'python-poller', 'weights', 'infer_config.json'),
  // CKPT_DLV3: process.env.CKPT_DLV3 || path.resolve(__dirname, '..', 'python-poller', 'weights', 'deeplabv3_resnet50_oil_best_2.pt'),
  CKPT_DLV3: process.env.CKPT_DLV3 || path.resolve(__dirname, '..', 'python-poller', 'weights', 'deeplabv3_resnet101_oil_fold5_continued_best.pt'),
  CFG_DLV3:  process.env.CFG_DLV3  || path.resolve(__dirname, '..', 'python-poller', 'weights', 'infer_config.json'),
  SNAP_GRAPH: process.env.OPS_SNAP || path.resolve(__dirname, '..', 'python-poller', 'snap-graphs', 'GraphSubset2.xml'),
  PREPROC_IN: process.env.OPS_PREPROC_IN  || path.resolve(__dirname, '..', 'python-poller', 'data', 's1'),      // SAFE or zips (latest)
  PREPROC_OUT:process.env.OPS_PREPROC_OUT || path.resolve(__dirname, '..', 'python-poller', 'work'),            // preprocessed GeoTIFFs
};






// If client is sibling to /server:
const CLIENT_DIR = path.resolve(__dirname, '..', 'client');
// If your client is inside /server, use:
// const CLIENT_DIR = path.resolve(__dirname, 'client');
const clients = new Map(); // clientId -> { ws, gps:{lat,lon}, lastSeen, status }
const operators = new Set(); // operator sockets

console.log('Serving static from:', CLIENT_DIR);

const app = express();
// server/server.js

// ---- security / CSP (ok to keep before proxy) ----
app.use((req, res, next) => {
  res.setHeader('Content-Security-Policy',
    "default-src 'self'; connect-src 'self' ws: wss: http: https:; script-src 'self' 'unsafe-eval' https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: blob: https://demotiles.maplibre.org; font-src 'self' data: https://demotiles.maplibre.org; worker-src 'self' blob:; child-src 'self' blob:");
  next();
});

// Proxy REST API (Flask on 8282 in WSL)
app.use('/api', createProxyMiddleware({
  target: 'http://172.26.245.79:8282',
  changeOrigin: true,
  proxyTimeout: 120000,
  timeout: 120000,
}));
// (optional) if you want to proxy websockets like /signal through Node too:
// app.use('/signal', createProxyMiddleware({ target: 'ws://localhost:8282', changeOrigin: true, ws: true }));
///////////////////////////////////////////////


app.use(express.json({ limit: '10mb' }));  // was default 100kb
//const httpServer = createServer(app);
const httpServer = https.createServer({ key, cert }, app);


//FIX FOR CONFLICT BETWEEN THIS SERVER AND DATABASE SERVER









// helper: broadcast to all connected WS clients (operator UIs)
function broadcast(obj){
  try {
    wss.clients.forEach(ws => {
      if (ws.readyState === 1) ws.send(JSON.stringify(obj));
    });
  } catch {}
}
// simple in-memory job store
const scans = new Map();  // jobId -> {state, found, downloaded, logs[], started, ended}
let scanSeq = 0;

// utility to stream child output to console & operator log
function streamChild(child, tag='job') {
  child.stdout.on('data', (d) => {
    const line = d.toString();
    console.log(`[${tag}]`, line.trim());
    operators.forEach(op => safeSend(op, { type:'log', line: `[${tag}] ${line.trim()}` }));
  });
  child.stderr.on('data', (d) => {
    const line = d.toString();
    console.error(`[${tag} err]`, line.trim());
    operators.forEach(op => safeSend(op, { type:'log', line: `[${tag} err] ${line.trim()}` }));
  });
}




// --- metrics ---
const METRICS = {
  startedAt: Date.now(),
  hazard: { bytesSent: 0, messagesSent: 0, lastSeq: null, lastSentAt: null },
};
function snapshotMetrics() {
  const mem = process.memoryUsage();
  return {
    now: new Date().toISOString(),
    uptimeSec: Math.round((Date.now() - METRICS.startedAt) / 1000),
    registry: { clients: clients.size, operators: operators.size },
    scheduler: scheduler.stats(),                   // we'll add stats() below
    hazard: METRICS.hazard,
    memoryMB: {
      rss: Math.round(mem.rss / 1e6),
      heapUsed: Math.round(mem.heapUsed / 1e6),
      ext: Math.round(mem.external / 1e6),
    },
  };
}


// --- Dev CSP (permit our needs). Remove/lock down for prod. ---
app.use((req, res, next) => {
  res.setHeader(
    'Content-Security-Policy',
    [
      "default-src 'self'",
      "img-src 'self' data: blob: https://demotiles.maplibre.org",
      "script-src 'self' https://unpkg.com blob: 'unsafe-eval'", // blob for workers
      "style-src 'self' 'unsafe-inline' https://unpkg.com",
      "connect-src 'self' ws: wss: http: https:",                 // tiles/glyphs OK
      "font-src 'self' data: https://demotiles.maplibre.org",
      "worker-src 'self' blob:",                                  // <-- key line
      "child-src 'self' blob:"                                    // fallback for some browsers
    ].join("; ")
  );
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');
  next();
});




// Static files + root
app.use(express.static(CLIENT_DIR, { index: ['operator.html'] }));
app.get('/', (req, res) => res.sendFile(path.join(CLIENT_DIR, 'operator.html')));
// Quiet the favicon request in dev
app.get('/favicon.ico', (req, res) => res.status(204).end());

// ---- Hazard scheduler seed ----
/* const scheduler = new Scheduler();
const hazardsPath = new URL('./geofiles/sample-hazards.geojson', import.meta.url);
//const sampleHazards = JSON.parse(fs.readFileSync(hazardsPath, 'utf8'));
console.log('Loading hazards from', hazardsPath.href);
let sampleHazards;
try {
  sampleHazards = JSON.parse(fs.readFileSync(hazardsPath, 'utf8'));
  console.log('Loaded hazards features =', sampleHazards.features?.length ?? 0);
} catch (e) {
  console.error('Failed reading hazards:', e.message);
  sampleHazards = { type: 'FeatureCollection', features: [] };
}
scheduler.seed(sampleHazards);
broadcastLog(`[${new Date().toISOString()}] Seeded hazards: ${sampleHazards.features?.length ?? 0}`); */

/* app.post('/admin/simulate', (req, res) => {
  //24.489460, 54.284955
  const { lon=54.28, lat=24.49, size_km=2, class:klass='probable', confidence=0.7, priority=8, ttlMs=10000 } = req.body || {};
  const deg = size_km / 111; // ~deg per km
  const gj = rectPolygon({ lon, lat, dxDeg:deg, dyDeg:deg*0.6, klass, confidence,
                           drift:{ dx: 0.015, dy: 0.005 } });
  scheduler.ingest(gj, priority, ttlMs);
  broadcastLog(`[${new Date().toISOString()}] Ingested frame pri=${priority} @ (${lat.toFixed(3)}, ${lon.toFixed(3)}) conf=${confidence}`);
  res.json({ ok:true, injected: gj.features[0].properties });
});

// POST /ingest/oil   body = GeoJSON FeatureCollection of slick polygons
app.post('/ingest/oil', (req, res) => {
  const gj = req.body;
  if (!gj || gj.type !== 'FeatureCollection') {
    return res.status(400).json({ error: 'Expected GeoJSON FeatureCollection' });
  }
  const { priority = 8, ttlMs = 120000 } = req.query; // or accept from body
  scheduler.ingest(gj, Number(priority), Number(ttlMs));
  broadcastLog(`[${new Date().toISOString()}] Ingested external detections (n=${gj.features?.length ?? 0})`);
  res.json({ ok: true });
});

// GET /admin/simulate?lon=54.4&lat=24.48&size_km=3&class=confirmed&confidence=0.9
app.get('/admin/simulate', (req, res) => {
  const q = req.query;
  const lon = parseFloat(q.lon ?? '54.38');
  const lat = parseFloat(q.lat ?? '24.47');
  const size_km = parseFloat(q.size_km ?? '2');
  const klass = q.class || 'probable';
  const confidence = parseFloat(q.confidence ?? '0.7');
  const priority = parseInt(q.priority ?? '8', 10);
  const ttlMs = parseInt(q.ttlMs ?? '120000', 10);

  const deg = size_km / 111;
  const gj = rectPolygon({ lon, lat, dxDeg:deg, dyDeg:deg*0.6, klass, confidence,
                           drift:{ dx: 0.015, dy: 0.005 } });
  scheduler.ingest(gj, priority, ttlMs);
  broadcastLog(`[${new Date().toISOString()}] Ingested via GET pri=${priority} @ (${lat.toFixed(3)}, ${lon.toFixed(3)})`);
  res.json({ ok:true });
});

app.get('/admin/metrics', (req, res) => {
  res.json(snapshotMetrics());
}); */

app.post('/admin/scan', (req, res) => {
  const wkt = (req.body?.wkt || '').trim();
  if (!wkt || !/POLYGON\s*\(/i.test(wkt)) {
    return res.status(400).json({ ok:false, error: 'Invalid AOI WKT' });
  }

  const jobId = `scan_${Date.now()}_${++scanSeq}`;
  const rec = { id: jobId, state: 'running', found: 0, downloaded: 0, logs: [], started: Date.now() };
  scans.set(jobId, rec);

  // ---- configure process env & working dir ----
  const env = { ...process.env, OPS_AOI_WKT: wkt, PYTHONUNBUFFERED: '1' };
  const cwd = path.resolve(__dirname, '..'); // repo root (server/..)

  // ---- choose python exe & script/args (override via env if you like) ----
  const pyExe  = process.env.SCAN_PYTHON || 'python'; // e.g. "py" on Windows, or full path
  //const script = process.env.SCAN_SCRIPT  || 'python-poller/s1_poller.py';
  const script = process.env.SCAN_SCRIPT  || 'python-poller/scan_download.py';
  // for a “scan & download only” run; tweak to match your s1_poller flags
  const defaultArgs = ['--once', '--limit', '3', '--mode', 'download'];
  const extraArgs   = (process.env.SCAN_ARGS?.trim() || '').split(/\s+/).filter(Boolean);
  const args = [script, ...(extraArgs.length ? extraArgs : defaultArgs)];

  // ---- spawn the poller ----
  const child = spawn(pyExe, args, { cwd, env });

  // stream stdout/err into WS & counters
  const push = (buf, isErr = false) => {
    const lines = buf.toString().split(/\r?\n/).filter(Boolean);
    for (const line of lines) {
      const text = (isErr ? '[err] ' : '') + line;
      rec.logs.push(text); if (rec.logs.length > 200) rec.logs.shift();

      // naive counters – adjust to your script’s prints
      if (/FOUND\s+NEW/i.test(line) || /FOUND SCENE/i.test(line)) rec.found++;
      if (/(DOWNLOADING|DOWNLOADED|SAVED TO)/i.test(line))      rec.downloaded++;

      broadcast({ type: 'scan-log',    jobId, line: text });
      broadcast({ type: 'scan-status', jobId, state: rec.state, found: rec.found, downloaded: rec.downloaded });
    }
  };

  child.stdout.on('data', d => push(d, false));
  child.stderr.on('data', d => push(d, true));
  child.on('close', code => {
    rec.state = (code === 0 ? 'done' : 'error');
    rec.ended = Date.now();
    broadcast({ type: 'scan-status', jobId, state: rec.state, found: rec.found, downloaded: rec.downloaded });
  });

  res.json({ ok: true, jobId });
});



// --- SNAP preprocess newest (server-side) ---
app.post('/admin/preprocess', async (req, res) => {
  try {
     const wkt = (req.body?.aoiWkt || '').trim();
    if (!wkt || !/POLYGON\s*\(/i.test(wkt)) {
      return res.status(400).json({ ok:false, error: 'Invalid AOI WKT' });
    }

    // WKT -> GeoJSON -> area (m²) -> km²
    const geojson = wellknown.parse(wkt);          // expects lon/lat (EPSG:4326)
    const km2 = area(geojson) / 1e6;               // Turf computes spherical area

    
    // pick newest SAFE/ZIP inside PREPROC_IN
    const candidates = fs.readdirSync(OPS.PREPROC_IN)
      .map(n => path.join(OPS.PREPROC_IN, n))
      .filter(p => fs.statSync(p).isFile() || fs.statSync(p).isDirectory())
      .sort((a,b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    if (!candidates.length) return res.status(404).json({ error: 'No inputs in PREPROC_IN' });

    const input = candidates[0];
    const stem = path.basename(input).replace(/\.(SAFE|zip|ZIP)$/,'');
    const outPath = path.join(OPS.PREPROC_OUT, `${stem}_oilprep.tif`);

    // call gpt (SNAP) via a helper py, or call gpt.exe directly
    const script = path.join(OPS.POLLER_DIR, 'run_gpt_once.py'); // create this small wrapper (below)
    const args = [ script, OPS.SNAP_GRAPH, input, outPath, wkt ];
    const child = spawn(OPS.PYTHON, args, { cwd: OPS.POLLER_DIR });
    streamChild(child, 'snap');
    child.on('close', (code) => {
      if (code !== 0) return operators.forEach(op => safeSend(op, { type:'log', line:`[snap] exit ${code}` }));
    });

    res.json({ ok:true, aoi_km2: km2.toFixed(2), input, outPath });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});



// --- Run inference on newest preprocessed scene intersecting AOI ---
/* app.post('/admin/infer', async (req, res) => {
  try {
    const { modelId='unet_b48_v1', aoiWkt=null } = req.body || {};
    
    // choose newest TIF in PREPROC_OUT
    const tifs = fs.readdirSync(OPS.PREPROC_OUT)
      .filter(n => /\.tif(f)?$/i.test(n))
      .map(n => path.join(OPS.PREPROC_OUT, n))
      .sort((a,b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    if (!tifs.length) return res.status(404).json({ error: 'No preprocessed GeoTIFFs found' });

    const inputTif = tifs[0];
    const script = path.join(OPS.POLLER_DIR, 'infer_latest.py');
    const args = [ script, '--tif', inputTif, '--ckpt', OPS.CKPT, '--cfg', OPS.CFG ];
    if (aoiWkt) args.push('--aoi-wkt', aoiWkt);

    const child = spawn(OPS.PYTHON, args, { cwd: OPS.POLLER_DIR });
    let buf = '';
    streamChild(child, 'infer');
    child.stdout.on('data', d => { buf += d.toString(); });

    child.on('close', (code) => {
      if (code !== 0) return res.status(500).json({ error:`infer exit ${code}` });
      // script prints a single JSON on last line
      try {
        const lines = buf.trim().split(/\r?\n/);
        const last = lines[lines.length-1];
        const out = JSON.parse(last); // {fc, bbox, count}
        // broadcast a UI update (optional)
        operators.forEach(op => safeSend(op, { type:'log', line:`[detect] ${out.count} polygons` }));
        return res.json(out);
      } catch (e) {
        return res.status(500).json({ error: 'Bad JSON from infer', detail: e.message });
      }
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}); */

app.post('/admin/infer', async (req, res) => {
  try {
    const { modelId = 'unet_b48_v1', aoiWkt = null } = req.body || {};

    // Map modelId → ckpt & cfg
    const MODEL_MAP = {
      // adjust paths to yours
      unet_b48_v1: {
        ckpt: OPS.CKPT_UNET,       // e.g. "C:/.../unet_b48_best.pth"
        cfg:  OPS.CFG_UNET,        // e.g. "C:/.../unet_cfg.json"
        name: 'unet_b48_v1',          // value for --model
      },
      deeplabv3_v1: {
        ckpt: OPS.CKPT_DLV3,       // e.g. "C:/.../deeplabv3_resnet50_oil_best_2.pt"
        cfg:  OPS.CFG_DLV3,        // optional; can reuse UNet cfg if not needed
        name: 'deeplabv3_v1',
      },
    };

    if (!MODEL_MAP[modelId]) {
      return res.status(400).json({ ok:false, error:`Unknown modelId ${modelId}` });
    }
    const { ckpt, cfg, name } = MODEL_MAP[modelId];

    //======Pick the “latest” preprocessed GeoTIFF=========
    const tifs = fs.readdirSync(OPS.PREPROC_OUT)
      .filter(n => /\.tif(f)?$/i.test(n))
      .map(n => path.join(OPS.PREPROC_OUT, n))
      .sort((a,b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    //======Pick the “latest” preprocessed GeoTIFF=========

    if (!tifs.length) return res.status(404).json({ error: 'No preprocessed GeoTIFFs found' });

    const inputTif = tifs[0];


    //=========Spawn Python as a child process=============
    const script = path.join(OPS.POLLER_DIR, 'infer_latest.py');

    const args = [
      script,
      '--tif', inputTif,
      '--ckpt', ckpt,
      '--cfg',  cfg,
      '--model', name,            // <—— pass selected model
    ];
    if (aoiWkt) args.push('--aoi-wkt', aoiWkt);

    const child = spawn(OPS.PYTHON, args, { cwd: OPS.POLLER_DIR });
    //=========Spawn Python as a child process=============



    //=========Collect stdout output from Python===========
    let buf = '';
    streamChild(child, 'infer');
    child.stdout.on('data', d => { buf += d.toString(); });
    //=========Collect stdout output from Python===========


    //=========When Python exits, interpret the result=====
    /*
    Logic:
    If Python exit code != 0 → server returns HTTP 500.
    Otherwise:
    split stdout into lines
    take the last line
    parse it as JSON → out
    broadcast a message to connected operator clients (websocket/SSE style)
    return out as the API response (res.json(out))
    */
    child.on('close', (code) => {
      if (code !== 0) return res.status(500).json({ error:`infer exit ${code}` });
      try {
        const lines = buf.trim().split(/\r?\n/);
        const last  = lines[lines.length-1];
        const out   = JSON.parse(last);
        operators.forEach(op => safeSend(op, { type:'log', line:`[detect] ${out.count} polygons` }));
        return res.json(out);
      } catch (e) {
        return res.status(500).json({ error: 'Bad JSON from infer', detail: e.message });
      }
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
  //=========When Python exits, interpret the result=====
});



// ---- WebSocket signalling (offer/answer + ICE) ----
const wss = new WebSocketServer({ server: httpServer, path: '/signal' });
wss.on('connection', (ws) => {
  let peerCtx = null;
  let role = 'unknown';
  let myId = null;

  ws.on('message', async (raw) => {
    const msg = JSON.parse(raw.toString());

    // 0) hello / presence
    if (msg.type === 'hello') {
      role = msg.role; // 'pi' or 'operator'
      myId = msg.clientId || `op-${Math.random().toString(36).slice(2,8)}`;
      if (role === 'pi') {
        clients.set(myId, { ws, gps: msg.gps || null, lastSeen: Date.now(), status: 'idle' });
        console.log('[REG] PI up:', myId, msg.gps);
        // notify operators
        operators.forEach(op => safeSend(op, { type:'clients', clients: listClients() }));
      } else if (role === 'operator') {
        operators.add(ws);
        console.log('[REG] OP up:', myId);
        safeSend(ws, { type:'clients', clients: listClients() });
      }
      return;
    }

    // 0b) live GPS/status from PI
    if (role === 'pi' && msg.type === 'heartbeat') {
      const c = clients.get(myId);
      if (c) { c.gps = msg.gps || c.gps; c.lastSeen = Date.now(); c.status = msg.status || c.status; }
      return;
    }

    // 1) operator asks for list
    if (role === 'operator' && msg.type === 'list') {
      return safeSend(ws, { type:'clients', clients: listClients() });
    }

    // 2) operator -> pi : viewer offer (start stream)
    if (role === 'operator' && msg.type === 'viewer-offer') {
      const target = clients.get(msg.targetId);
      if (target) {
        safeSend(target.ws, { type:'viewer-offer', from: myId, mid: msg.mid, sdp: msg.sdp });
      }
      return;
    }

    // 3) pi -> operator : answer
    if (role === 'pi' && msg.type === 'viewer-answer') {
      // forward to the specific operator (we broadcast; operators ignore if mid not found)
      operators.forEach(op => safeSend(op, { type:'viewer-answer', from: myId, mid: msg.mid, sdp: msg.sdp }));
      return;
    }

    // 4) ICE bridge both ways
    if (msg.type === 'viewer-ice') {
      if (role === 'operator') {
        const target = clients.get(msg.targetId);
        target && safeSend(target.ws, { type:'viewer-ice', from: myId, mid: msg.mid, candidate: msg.candidate });
      } else if (role === 'pi') {
        operators.forEach(op => safeSend(op, { type:'viewer-ice', from: myId, mid: msg.mid, candidate: msg.candidate }));
      }
      return;
    }

    if (msg.type === 'offer') {
      // <-- pass msg.sdp
      peerCtx = await createPeerForClient(ws, msg.gps, scheduler, msg.sdp, METRICS);
    }
    if (msg.type === 'candidate' && peerCtx) {
      await peerCtx.pc.addIceCandidate(msg.candidate).catch(()=>{});
    }
  });
  ws.on('close', () => {
    if (role === 'pi' && myId) { clients.delete(myId); }
    if (role === 'operator') { operators.delete(ws); }
    // refresh operators list
    operators.forEach(op => safeSend(op, { type:'clients', clients: listClients() }));
  });
});

function broadcastLog(line) {
  operators.forEach(op => safeSend(op, { type: 'log', line }));
}


function listClients() {
  const now = Date.now();
  return [...clients.entries()].map(([id, c]) => ({
    id, gps: c.gps, lastSeen: now - c.lastSeen, status: c.status
  }));
}

function safeSend(ws, obj) {
  try { ws.readyState === ws.OPEN && ws.send(JSON.stringify(obj)); } catch {}
}

// Graceful shutdown (nice with nodemon)
function shutdown() {
  try { wss.clients.forEach(c => c.close()); } catch {}
  try { wss.close(() => httpServer.close(() => process.exit(0))); } catch { process.exit(0); }
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

const PORT = process.env.PORT || 8181;
httpServer.listen(PORT, () => console.log(`Server on https://localhost:${PORT}`));
