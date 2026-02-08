// ============================
// operator.js 
// ============================

// --- Constants (AOI defaults: UAE box) ---
/* let AOI_BOUNDS = [[54.00, 24.00], [54.50, 24.50]];
let AOI_BOUNDS_PADDED = [[53.5, 23.5], [55.0, 25.0]]; */
let AOI_BOUNDS = [[107, 4.3], [135, 19]];
let AOI_BOUNDS_PADDED = [[106, 3.8], [136, 19.5]];

// --- Basemap styles ---
const esriImageryStyle = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    esri: {
      type: 'raster',
      tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      attribution: '© Esri & contributors'
    }
  },
  layers: [{ 
    id: 'base', type: 'raster', source: 'esri' ,
    paint: {
      'raster-brightness-min': 0.0,
      'raster-brightness-max': 1.0,  // can’t exceed 1
      'raster-contrast': 0.05,
      'raster-saturation': -0.1,
      'raster-opacity': 1.0
    }
  }]
};
const osmStyle = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors'
    }
  },
  layers: [{ id: 'base', type: 'raster', source: 'osm' }]
};
// choose one:
const BASE_STYLE = esriImageryStyle;

// --- Tiny helpers ---
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const on = (el, ev, fn) => el && el.addEventListener(ev, fn);




// --- Map + controls ---
let map;
class CoordsControl {
  onAdd(map) {
    this._map = map;
    this._el = document.createElement('div');
    this._el.className = 'maplibregl-ctrl';
    this._el.id = 'coordbar';
    this._el.style.color = 'white';
    this._el.textContent = '—';
    this._onMove = (e) => {
      const { lng, lat } = e.lngLat.wrap();
      this._el.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}  |  z ${map.getZoom().toFixed(2)}`;
    };
    map.on('mousemove', this._onMove);
    return this._el;
  }
  onRemove() {
    this._map.off('mousemove', this._onMove);
    this._el.remove();
    this._map = undefined;
  }
}
async function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: BASE_STYLE,
    // center: [54.37, 24.47],
    center: [123.42, 11.5],
    zoom: 8
  });
  window.map = map;
  map.addControl(new CoordsControl(), 'bottom-left');

  map.on('load', () => {
    ensureDetectLayers();
    ensureClientLayers();

    // initial view
    //map.setMaxBounds(AOI_BOUNDS_PADDED);
    map.fitBounds(AOI_BOUNDS, { padding: 40 });

    // --- AOI sources/layers (guard against duplicates) ---
    if (!map.getSource('aoi-poly')) {
      map.addSource('aoi-poly', { type:'geojson', data:{ type:'FeatureCollection', features:[] }});
    }
    if (!map.getSource('aoi-guides')) {
      map.addSource('aoi-guides', { type:'geojson', data:{ type:'FeatureCollection', features:[] }});
    }
    if (!map.getLayer('aoi-fill')) {
      map.addLayer({ id:'aoi-fill', type:'fill', source:'aoi-poly',
        paint:{ 'fill-color':'#00bcd4', 'fill-opacity':0.18 }});
    }
    if (!map.getLayer('aoi-line')) {
      map.addLayer({ id:'aoi-line', type:'line', source:'aoi-poly',
        paint:{ 'line-color':'#00bcd4', 'line-width':2 }});
    }
    if (!map.getLayer('aoi-guideline')) {
      map.addLayer({ id:'aoi-guideline', type:'line', source:'aoi-guides',
        layout:{ 'line-join':'round', 'line-cap':'round' },
        paint:{ 'line-color':'#00bcd4', 'line-dasharray':[2,2], 'line-width':1.5 }});
    }
    if (!map.getLayer('aoi-pts')) {
      map.addLayer({ id:'aoi-pts', type:'circle', source:'aoi-guides',
        paint:{ 'circle-radius':4, 'circle-color':'#00bcd4', 'circle-stroke-color':'#fff', 'circle-stroke-width':1 }});
    }

    // ensure proper sizing if shown via tab
    requestAnimationFrame(() => map.resize());
  });
}

// --- AOI drawing state & helpers ---
const MAX_KM2 = 1500; // <-- change your cap here for the AOI area
let drawingAOI = false;
let aoiPts = [];         // [[lng,lat], ...]
let aoiHover = null;     // {lng,lat} while drawing
let AOI_GEOJSON = null;  // final polygon
function toWKT(ring){ return 'POLYGON((' + ring.map(([x,y]) => `${x.toFixed(6)} ${y.toFixed(6)}`).join(',') + '))'; }
function bboxOfCoords(coords){
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for (const [x,y] of coords){
    if (!Number.isFinite(x)||!Number.isFinite(y)) continue;
    if (x<minX)minX=x; if (y<minY)minY=y; if (x>maxX)maxX=x; if (y>maxY)maxY=y;
  }
  if (![minX,minY,maxX,maxY].every(Number.isFinite)) return null;
  return [[minX,minY],[maxX,maxY]];
}
function padBounds(bb, f=0.1){
  const dx=(bb[1][0]-bb[0][0])*f, dy=(bb[1][1]-bb[0][1])*f;
  return [[bb[0][0]-dx, bb[0][1]-dy],[bb[1][0]+dx, bb[1][1]+dy]];
}




function lonLatToMeters([lon, lat]) {
  // Web Mercator (EPSG:3857) projection in meters
  const originShift = 2 * Math.PI * 6378137 / 2.0;
  const mx = lon * originShift / 180.0;
  let y = Math.log(Math.tan((90 + lat) * Math.PI / 360.0)) / (Math.PI / 180.0);
  const my = y * originShift / 180.0;
  return [mx, my];
}
function polygonAreaMeters2(coords) {
  // coords: [[lon,lat], ...] closed ring
  const pts = coords.map(lonLatToMeters);
  let a = 0;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [x1, y1] = pts[j], [x2, y2] = pts[i];
    a += (x1 * y2 - x2 * y1);
  }
  return Math.abs(a) / 2; // m²
}
function areaKm2FromGeoJSON(feature) {
  try {
    if (window.turf && turf.area) {
      return turf.area(feature) / 1e6; // geodesic m² -> km²
    }
  } catch (_) {}
  // Fallback (planar approx on 3857):
  const ring = feature.geometry.coordinates[0];
  return polygonAreaMeters2(ring) / 1e6;
}
function updateAoiUI(wkt, bb){
  const w = $('#aoi-wkt'), b = $('#aoi-bbox');
  if (w) w.value = wkt || '';
  if (b) b.textContent = bb ? `BBOX: [${bb[0][0].toFixed(5)}, ${bb[0][1].toFixed(5)}] – [${bb[1][0].toFixed(5)}, ${bb[1][1].toFixed(5)}]` : '';
  const copy = $('#aoi-copy'), apply = $('#aoi-apply');
  if (copy) copy.disabled = !wkt;
  if (apply) apply.disabled = !bb;
}
function refreshAoiPreview(){
  if (!map) return;
  const guides = map.getSource('aoi-guides'), poly = map.getSource('aoi-poly');
  if (!guides || !poly) return;

  const pts = aoiPts.map(c => ({ type:'Feature', geometry:{ type:'Point', coordinates:c }}));
  const lineCoords = aoiHover ? [...aoiPts, [aoiHover.lng, aoiHover.lat]] : aoiPts.slice();
  const guideFC = {
    type:'FeatureCollection',
    features: [
      ...(lineCoords.length>=2 ? [{ type:'Feature', geometry:{ type:'LineString', coordinates: lineCoords }}] : []),
      ...pts
    ]
  };
  guides.setData(guideFC);

  if (aoiPts.length >= 3){
    const ring = [...aoiPts, aoiPts[0]];
    poly.setData({ type:'FeatureCollection', features:[{ type:'Feature', geometry:{ type:'Polygon', coordinates:[ring] }, properties:{} }]});
    const fin = $('#aoi-finish'); if (fin) fin.disabled = false;
  } else {
    poly.setData({ type:'FeatureCollection', features:[] });
    const fin = $('#aoi-finish'); if (fin) fin.disabled = true;
  }
}
function startAoi(){
  drawingAOI = true; aoiPts = []; aoiHover = null; AOI_GEOJSON = null;
  if (map){ map.getCanvas().style.cursor = 'crosshair'; map.doubleClickZoom.disable(); }
  const fin = $('#aoi-finish'), rst = $('#aoi-reset'), app = $('#aoi-apply');
  if (fin) fin.disabled = true; if (rst) rst.disabled = false; if (app) app.disabled = true;
  updateAoiUI('', null);
  refreshAoiPreview();
}
function finishAoi(){
  if (aoiPts.length < 3) return;
  const ring = [...aoiPts, aoiPts[0]];
  AOI_GEOJSON = { type:'Feature', properties:{}, geometry:{ type:'Polygon', coordinates:[ring] } };
  drawingAOI = false; aoiHover = null;
  if (map){ map.getCanvas().style.cursor = ''; map.doubleClickZoom.enable(); }
  const guides = map.getSource('aoi-guides'); if (guides) guides.setData({ type:'FeatureCollection', features:[] });
  const poly = map.getSource('aoi-poly'); if (poly) poly.setData({ type:'FeatureCollection', features:[AOI_GEOJSON] });

  // === compute area (km²) ===
  const aoiKm2 = areaKm2FromGeoJSON(AOI_GEOJSON);

  // === display it ===
  const areaEl = document.getElementById('aoi-area');
  if (areaEl) {
    areaEl.textContent = `AOI area: ${aoiKm2.toFixed(2)} km²`;
    areaEl.dataset.km2 = aoiKm2.toFixed(6); // optional: keep precise value
  }

  // === enforce limit (optional) ===
  if (aoiKm2 > MAX_KM2) {
    alert(`AOI too large: ${aoiKm2.toFixed(2)} km² (limit ${MAX_KM2} km²). Please draw a smaller polygon.`);
    // you can also disable buttons here if needed
  }

  const bb = bboxOfCoords(ring);
  const wkt = toWKT(ring);
  updateAoiUI(wkt, bb);
}
function resetAoi(){
  drawingAOI = false; aoiPts = []; aoiHover = null; AOI_GEOJSON = null;
  if (map){ map.getCanvas().style.cursor = ''; map.doubleClickZoom.enable(); }
  const guides = map?.getSource('aoi-guides'); guides && guides.setData({ type:'FeatureCollection', features:[] });
  const poly = map?.getSource('aoi-poly'); poly && poly.setData({ type:'FeatureCollection', features:[] });
  updateAoiUI('', null);
  const fin = $('#aoi-finish'), rst = $('#aoi-reset'), app = $('#aoi-apply');
  if (fin) fin.disabled = true; if (rst) rst.disabled = true; if (app) app.disabled = true;
}
function applyAoi(){
  if (!AOI_GEOJSON || !map) return;
  const ring = AOI_GEOJSON.geometry.coordinates[0];
  const bb = bboxOfCoords(ring); if (!bb) return;
  AOI_BOUNDS = bb;
  AOI_BOUNDS_PADDED = padBounds(bb, 0.25);
  //map.setMaxBounds(AOI_BOUNDS_PADDED);
  map.fitBounds(AOI_BOUNDS, { padding: 40, duration: 600 });
  // Optionally notify server:
  // fetch('/admin/aoi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ wkt: toWKT(ring), bbox: bb })});
}
function setupAOIUI() {
  on($('#aoi-start'),  'click', startAoi);
  on($('#aoi-finish'), 'click', finishAoi);
  on($('#aoi-reset'),  'click', resetAoi);
  on($('#aoi-apply'),  'click', applyAoi);
  on($('#aoi-copy'),   'click', () => {
    const t = $('#aoi-wkt'); const v = t?.value?.trim(); if (v) navigator.clipboard?.writeText(v);
  });

  if (!map) return;
  map.on('mousemove', (e) => { if (!drawingAOI) return; aoiHover = e.lngLat.wrap(); refreshAoiPreview(); });
  map.on('click',     (e) => { if (!drawingAOI) return; const {lng,lat}=e.lngLat.wrap(); aoiPts.push([lng,lat]); const r = $('#aoi-reset'); if (r) r.disabled=false; refreshAoiPreview(); });
  map.on('dblclick',  ()  => { if (drawingAOI) finishAoi(); });
}

function fitToBbox(bbox, padding=60) {
  if (Array.isArray(bbox) && window.map && typeof window.map.fitBounds === 'function') {
    window.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding, duration: 400 });
  }
}

// --- Scan (ASF) UI state ---
let currentScanJob = null;

function setupScanUI(){
  const btn    = document.getElementById('aoi-scan');
  const feed   = document.getElementById('scan-feed');
  const counts = document.getElementById('scanCounts');
  if (!btn) return; // no scan controls on this page

  const addLine = (txt) => {
    if (!feed) return;
    const li = document.createElement('li');
    li.textContent = txt.trim();
    feed.prepend(li);
  };

  btn.addEventListener('click', async () => {
    const wkt = document.getElementById('aoi-wkt')?.value?.trim();
    if (!wkt) { alert('Draw & finish an AOI first.'); return; }
    btn.disabled = true; btn.textContent = 'Scanning…';
    addLine(`[${new Date().toLocaleTimeString()}] starting scan…`);
    try {
      const r  = await fetch('/admin/scan', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ wkt })
      });
      const js = await r.json();
      if (!r.ok || !js.ok) throw new Error(js.error || r.statusText);
      currentScanJob = js.jobId;
      if (counts) counts.textContent = `job ${currentScanJob}…`;
    } catch (e) {
      addLine(`! start failed: ${e.message}`);
      btn.disabled = false; btn.textContent = 'Scan & Download (ASF)';
    }
  });

  // hooks used by onSignal()
  window.__scanUI__ = {
    onLog(jobId, line){
      if (currentScanJob && jobId === currentScanJob) addLine(line);
    },
    onStatus(jobId, state, found, downloaded){
      if (!currentScanJob || jobId !== currentScanJob) return;
      if (counts) counts.textContent = `found: ${found||0}  downloaded: ${downloaded||0}  state: ${state}`;
      if (state === 'done' || state === 'error') {
        btn.disabled = false; btn.textContent = 'Scan & Download (ASF)';
      }
    }
  };
}

// === Inference UI handlers ===
const btnRunInfer  = document.getElementById('btnRunInfer');
const btnPreproc   = document.getElementById('btnPreprocess');
const modelSelect  = document.getElementById('modelSelect');
const inferStatus  = document.getElementById('inferStatus');

// add a detection layer once at map load
function ensureDetectLayers() {
  if (!map.getSource('detect-src')) {
    map.addSource('detect-src', { type: 'geojson', data: {type:'FeatureCollection', features:[]} });
    map.addLayer({ id:'detect-fill', type:'fill', source:'detect-src',
      paint:{ 'fill-color': '#9b51e0', 'fill-opacity': 0.35 }});
    map.addLayer({ id:'detect-line', type:'line', source:'detect-src',
      paint:{ 'line-color':'#ffffff', 'line-width':1.2, 'line-opacity':0.8 }});
  }
}

// simple logger (re-uses your updates log element)
function appendDetectionLog(fc) {
  const logEl = document.getElementById('log');
  const ts = new Date().toISOString();
  (fc.features || []).slice(0, 5).forEach((f, i) => {
    let coords = '';
    try {
      const g = f.geometry;
      if (g.type === 'Polygon' && g.coordinates?.[0]?.length) {
        const [lon, lat] = g.coordinates[0][0];
        coords = `(${lat.toFixed(5)}, ${lon.toFixed(5)})`;
      }
    } catch {}
    logEl.textContent = `[${ts}] DETECT polygon #${i+1} @ ${coords} conf=${(f.properties?.confidence ?? 0).toFixed?.(2) || f.properties?.confidence}\n` + logEl.textContent;
  });
  if ((fc.features || []).length > 5) {
    logEl.textContent = `[${ts}] DETECT +${fc.features.length - 5} more polygons …\n` + logEl.textContent;
  }
}

// call server to preprocess latest download with SNAP graph
btnPreproc.addEventListener('click', async () => {
  inferStatus.textContent = 'Preprocessing (SNAP)…';
  try {
    const body = { aoiWkt: document.getElementById('aoi-wkt').value || null };
    const r = await fetch('/admin/preprocess', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    inferStatus.textContent = `Preprocess OK → ${j.outPath || 'latest.tif'}`;
  } catch (e) {
    inferStatus.textContent = `Preprocess ERROR: ${e.message}`;
  }
});

// run inference on newest scene intersecting AOI
/* btnRunInfer.addEventListener('click', async () => {
  const aoiWkt = document.getElementById('aoi-wkt').value || null;
  const modelId = modelSelect.value;
  inferStatus.textContent = 'Inferring (latest scene)…';
  try {
    const r = await fetch('/admin/infer', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify({ modelId, aoiWkt })
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);

    // j will contain { fc, bbox, count }
    const fc = j.fc || {type:'FeatureCollection', features:[]};
    map.getSource('detect-src').setData(fc);
    if (j.bbox && Array.isArray(j.bbox) && j.bbox.length === 4) {
      fitToBbox(j.bbox, 60);
    } else {
      safeFitToData(fc);
    }
    appendDetectionLog(fc);
    const count = j.count || (fc.features?.length || 0);
    //inferStatus.textContent = `Inference OK: ${j.count || (fc.features?.length||0)} polygons`;
    inferStatus.textContent = `Inference OK: ${count} polygons`;

    //raise incident here if count is more than zero
    if (count > 0) {
      console.log('[infer]',count)
      autoRaiseIncidentFromFC(fc).catch(err => console.warn('auto-raise failed:', err));
    }

  } catch (e) {
    inferStatus.textContent = `Inference ERROR: ${e.message}`;
  }
}); */
//////////////////////////////////////////////////

// --- Hazard & client video (WebRTC + WS) ---
let ws;
function send(obj){ ws && ws.readyState===1 && ws.send(JSON.stringify(obj)); }

// Hazards via DataChannel
let hazardPC, hazardCh;
async function connectHazard() {
  hazardPC = new RTCPeerConnection();
  hazardCh = hazardPC.createDataChannel('hazard', { ordered:false, maxRetransmits:1 });
  hazardCh.onopen    = () => { console.log('[ops] hazard channel OPEN'); startHazardStats(); };
  hazardCh.onmessage = onHazardFrame;
  hazardPC.onicecandidate = (e) => { if (e.candidate) send({ type:'candidate', candidate:e.candidate }); };
  const gps = { lat: 24.47, lon: 54.37 }; // hint
  const offer = await hazardPC.createOffer();
  await hazardPC.setLocalDescription(offer);
  send({ type:'offer', sdp: offer.sdp, gps });
}
let hzLastBytes = 0, hzLastTime = 0;
async function startHazardStats() {
  const el = $('#hz-metrics');
  hzLastBytes = 0; hzLastTime = performance.now();
  async function tick() {
    if (!hazardPC){ setTimeout(tick,1000); return; }
    try {
      const stats = await hazardPC.getStats();
      let bytes = 0, msgs = 0;
      stats.forEach(s => {
        if (s.type === 'data-channel' && s.label === 'hazard') {
          bytes = s.bytesReceived || 0;
          msgs  = s.messagesReceived || 0;
        }
      });
      const now = performance.now();
      const bps = (bytes - hzLastBytes) * 8 / ((now - hzLastTime) / 1000);
      const kbps = Math.max(0, bps / 1000).toFixed(1);
      el && (el.textContent = `hazard: ${msgs} msgs • ${bytes} B • ${kbps} kbps`);
      hzLastBytes = bytes; hzLastTime = now;
    } catch {}
    setTimeout(tick, 1000);
  }
  tick();
}


// Clients status on map //
function ensureClientLayers() {
  if (!map.getSource('clients-src')) {
    map.addSource('clients-src', {
      type: 'geojson',
      data: { type:'FeatureCollection', features:[] }
    });
  }

  // Glow/halo
  if (!map.getLayer('clients-halo')) {
    map.addLayer({
      id: 'clients-halo',
      type: 'circle',
      source: 'clients-src',
      paint: {
        'circle-color': '#22c55e', // green halo
        'circle-radius': 10,       // adjust to taste
        'circle-opacity': ['case', ['boolean', ['get','visible'], false], 0.35, 0.0],
        'circle-blur': 0.7
      }
    });
  }


  // Core dot
  if (!map.getLayer('clients-dot')) {
    map.addLayer({
      id: 'clients-dot',
      type: 'circle',
      source: 'clients-src',
      paint: {
        'circle-radius': 6,
        'circle-color': [
          'case',
          ['==', ['get', 'status'], 'taking_off'], '#f6c343',        // yellow
          ['==', ['get', 'status'], 'enroute'],     '#f6c343',        // yellow
          ['==', ['get', 'status'], 'arrived'],     '#00e676',        // green
          ['==', ['get', 'status'], 'idle'],        '#00e676',        // green
          '#cccccc'                                                   // default
        ],
        'circle-opacity': [
          'case',
          ['boolean', ['get', 'blink'], false], 1.0, 0.2              // blink toggle
        ],
        'circle-stroke-color': '#000',
        'circle-stroke-width': 1
      }
    });
  }

  // label with client id
  if (!map.getLayer('clients-label')) {
    map.addLayer({
      id: 'clients-label',
      type: 'symbol',
      source: 'clients-src',
      layout: {
        'text-field': ['get','id'],
        'text-font': ['Noto Sans Regular'],
        'text-size': 12,
        'text-offset': [0, 1.2],
        'text-anchor': 'top'
      },
      paint: {
        'text-color': '#ffffff',
        'text-halo-color': '#000000',
        'text-halo-width': 1.2,
        'text-opacity': ['case', ['get','visible'], 1.0, 0.4]
      }
    });
  }
}

// In your module scope
const clientPoints = new Map(); // id -> { id, lon, lat, status, lastSeen }

// Status → blink period (ms)
const BLINK_PERIOD = {
  idle: 2000,
  standby: 2000,
  arrived: 2000,
  taking_off: 1000,
  travel: 2000,
  to_waypoint: 2000
};

function statusToPeriod(status) {
  return BLINK_PERIOD[status] ?? 2000;
}

// Push/update one client (call when you get fresh data)
function upsertClientPoint({ id, lon, lat, status }) {
  if ( !Number.isFinite(lat) || !Number.isFinite(lon)) return;
  clientPoints.set(id, {
    id,
    lon: lon,
    lat: lat,
    status: status || 'idle',
    lastSeen: Date.now()
  });
}

// Rebuild the GeoJSON with a per-feature "visible" flag that blinks
function refreshClientSource() {
  const now = Date.now();
  const feats = [];
  for (const c of clientPoints.values()) {
    const period = statusToPeriod(c.status);
    const half = period / 2;
    const phase = Math.floor((now % period) / half); // 0 or 1
    const visible = (phase === 0); // 50% duty cycle blink

    feats.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
      properties: {
        id: c.id,
        status: c.status,
        visible
      }
    });
  }

  const src = map.getSource('clients-src');
  if (src) src.setData({ type:'FeatureCollection', features:feats });
}

// Run the blinker (lightweight)
setInterval(refreshClientSource, 200); // 5 fps is plenty for blinking


function upsertClientFromTelemetry(msg) {
  // msg is like:
  // { type:'telemetry', lat: 24.4876, lon: 54.3914, status?: 'idle' }
  if (!Number.isFinite(msg.lat) || !Number.isFinite(msg.lon)) return;

  // Use a stable id — ideally the device id your server already knows.
  // If your message doesn't include one, pick a default for now:
  const id = msg.droneId || msg.from || 'pi-1';

  upsertClientPoint({
    id,
    lon: msg.lon ,  lat: msg.lat , // <-- adapt to expected shape
    status: msg.status || 'idle'
  });
  // Optional immediate refresh (the blinker timer also refreshes periodically)
  refreshClientSource();
}

// Minimal clients store
const clients = {}; // { [id]: { id, connected, armed, status, lat, lon, rel_alt_m, battery_pct, last_seen } }

// Call this for every WS message
function updateClients(msg) {
  if (!msg || !msg.type) return;
  
  
  if (msg.type === 'telemetry') {
    const id = msg.droneId || msg.id;
    
    if (!id) return;
    const cur = clients[id] || { id };
    clients[id] = {
      ...cur,
      connected: msg.connected ?? cur.connected ?? true,
      armed:     msg.armed     ?? cur.armed     ?? false,
      status:    msg.status    ?? cur.status    ?? 'idle',
      lat:       Number.isFinite(msg.lat) ? msg.lat : cur.lat ?? null,
      lon:       Number.isFinite(msg.lon) ? msg.lon : cur.lon ?? null,
      rel_alt_m: Number.isFinite(msg.rel_alt_m) ? msg.rel_alt_m : cur.rel_alt_m ?? null,
      battery_pct: Number.isFinite(msg.battery_pct) ? msg.battery_pct : cur.battery_pct ?? null,
      last_seen: Date.now()
    };
    //console.log('[drone telem msg<=]', clients[id]);
    checkArrivalAfterTelemetry(id);
  }

  // optional: refresh your map / UI
  //refreshClientsDotLayer?.(clients);
}


//////////////////////////////////////////
////////////////////////////////////////
// live registry keyed by clientId
/* const clientsIndex = new Map(); // id -> {gps:{lat,lon}, status, armed, lastSeen}

function upsertClient(c) {
  const prev = clientsIndex.get(c.id) || {};
  clientsIndex.set(c.id, {
    id: c.id,
    gps: c.gps ?? prev.gps,
    status: c.status ?? prev.status,
    armed: c.armed ?? prev.armed,           // if your telemetry includes it
    lastSeen: Date.now(),
  });
}

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);

  if (msg.type === 'clients') {
    (msg.clients || []).forEach(upsertClient);
  }

  // If your ctrl telemetry includes armed/status/gps, merge it:
  // if (msg.type === 'telemetry' && msg.clientId) upsertClient({ id: msg.clientId, gps:{lat:msg.lat,lon:msg.lon}, armed: msg.armed, status: msg.status });
};




 */

/////////////////////////
// Clients (Tab 2)
const listEl = $('#clientList');
const tiles  = $$('#tab-cams .tile');
const conns  = new Map(); // clientId -> { pc, mid, video, statTimer,ctrl}

function ensureControls(tile, clientId){
  // overlay (optional, but nice to have)
  if (!tile.querySelector('.overlay')) {
    const ov = document.createElement('div');
    ov.className = 'overlay';
    tile.appendChild(ov);
  }
  // controls
  let controls = tile.querySelector('.controls');
  if (!controls) {
    controls = document.createElement('div');
    controls.className = 'controls';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Actions';
    btn.addEventListener('click', () => openActionsModal(clientId));
    controls.appendChild(btn);
    tile.appendChild(controls);
  }
}



function renderClients(items) {
  if (!listEl) return;
  listEl.innerHTML = '';
  items.forEach(c => {
    const li = document.createElement('li');
    // li.textContent = `${c.id}  ${c.gps ? `(${c.gps.lat.toFixed(3)}, ${c.gps.lon.toFixed(3)})` : ''}`;
    li.textContent = `${c.id}`
    li.onclick = () => toggleView(c.id);
    listEl.appendChild(li);

    
  });
}
async function toggleView(clientId) {
  if (conns.has(clientId)) {
    const { pc, video, statTimer, ctrl } = conns.get(clientId);
    if (statTimer) clearInterval(statTimer);
    //pc.close(); 
    //new
    try { ctrl && ctrl.close && ctrl.close(); } catch {}
    try { pc && pc.close && pc.close(); } catch {}
    conns.delete(clientId);
    if (video) {
      const tile = video.closest('.tile');
      video.srcObject = null;
      tile.querySelector('.overlay').textContent = '';

      const overlay = tile.querySelector('.overlay');
      if (overlay) overlay.textContent = '';
      ensureControls(tile, clientId);   // <-- use helper instead of custom add
      tile.classList.remove('streaming');

      // add an Actions button on the tile
      /* const controls = document.createElement('div');
      controls.className = 'controls';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = 'Actions';
      btn.onclick = () => openActionsModal(clientId);
      controls.appendChild(btn);
      tile.appendChild(controls); */

    }

    

    return;
  }


  const tile = tiles.find(t => !t.querySelector('video').srcObject);
  if (!tile) { alert('All 6 tiles are in use. Close one first.'); return; }
  const video = tile.querySelector('video');
  tile.querySelector('.overlay').textContent = clientId;
  ensureControls(tile, clientId);  // <-- add here too
  

  const pc = new RTCPeerConnection({ iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }] });
  
  // --- This is the WebRTC video channel ---
  pc.ontrack = (ev) => { video.srcObject = ev.streams[0]; };
  pc.addTransceiver('video', { direction:'recvonly' });


  // --- This is the WebRTC data channel ---
  const ctrl = pc.createDataChannel('ctrl');
  ctrl.onopen = () => {
    console.log('[ctrl]', clientId, 'open');
    
    // optional: ask for immediate status
    safeCtrlSend(clientId, { id: Date.now(), cmd:'status' });
  };


  
  

  ctrl.onmessage = (ev) => {
  let msg; try { msg = JSON.parse(ev.data); } catch { return; }
  updateClients(msg);
  
  if (msg.type === 'ack' && msg.id != null) {
    if (msg.accepted === true || msg.phase === 'accepted') return;
    const key = `${clientId}:${msg.id}`;
    console.log('[ack]', clientId, msg);
    const w = ctrlWaiters.get(key);
    // if (w) { ctrlWaiters.delete(key); w(msg.ok, msg); }
    if (w) { w(msg); return; }
    return;
  }
  if (msg.type === 'telemetry' ) {
    
    upsertClientFromTelemetry(msg);
    
  } else {
    console.log('[ctrl<=]', clientId, msg);
  }
};
  // --- END NEW ---


  pc.onicecandidate = (e) => { if (e.candidate) send({ type:'viewer-ice', targetId: clientId, mid, candidate: e.candidate }); };

  const mid = Math.random().toString(36).slice(2, 10);
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  conns.set(clientId, { pc, mid, video, ctrl });
  startTileStats(clientId);
  send({ type:'viewer-offer', targetId: clientId, mid, sdp: offer.sdp });
}

// Helper: safe send on ctrl
function safeCtrlSend(clientId, obj){
  const c = conns.get(clientId);
  if (!c || !c.ctrl || c.ctrl.readyState !== 'open') {
    console.warn('ctrl not open for', clientId);
    return false;
  }
  console.log('[ctrl] ', clientId, JSON.stringify(obj))
  try { c.ctrl.send(JSON.stringify(obj)); return true; } catch { return false; }
}


function startTileStats(clientId) {
  const conn = conns.get(clientId); if (!conn) return;
  const { pc, video } = conn;
  const overlay = video.closest('.tile').querySelector('.overlay');
  let lastBytes = 0, lastTime = performance.now(), lastFrames = 0;
  conn.statTimer = setInterval(async () => {
    try {
      const stats = await pc.getStats();
      let bytes = 0, frames = 0, w, h;
      stats.forEach(s => {
        if (s.type === 'inbound-rtp' && s.kind === 'video') {
          bytes = s.bytesReceived || bytes;
          frames = s.framesDecoded ?? frames;
        }
        if (s.type === 'track' && s.kind === 'video') {
          w = s.frameWidth || w; h = s.frameHeight || h;
        }
      });
      const now = performance.now();
      const bps = (bytes - lastBytes) * 8 / ((now - lastTime) / 1000);
      const kbps = (bps / 1000).toFixed(1);
      const fps  = Math.max(0, (frames - lastFrames) / ((now - lastTime) / 1000)).toFixed(1);
      
      overlay.textContent = `${clientId} • ${kbps} kbps • ${fps} fps${(w&&h)?` • ${w}x${h}`:''}`;
      lastBytes = bytes; lastTime = now; lastFrames = frames;
    } catch {}
  }, 1000);
}

// --- Hazard rendering ---
function upsertHazards(geojson) {
  const srcId = 'hazards-src';
  if (!map) return;
  if (!map.getSource(srcId)) {
    map.addSource(srcId, { type:'geojson', data: geojson });
    map.addLayer({ id:'hazards', type:'fill', source:srcId,
      paint:{
        'fill-color':['match',['get','class'],'confirmed','#ff4d4f','probable','#f8a60f','#888'],
        'fill-opacity':0.35
      }});
    map.addLayer({ id:'hazards-outline', type:'line', source:srcId,
      paint:{ 'line-color':'#fff','line-width':1,'line-opacity':0.5 }});
  } else {
    map.getSource(srcId).setData(geojson);
  }
}
function upsertDrift(arrows) {
  if (!map) return;
  const srcId = 'drift-src';
  const feats = arrows.map(a => ({
    type:'Feature', properties:a,
    geometry:{ type:'LineString', coordinates:[[a.lon,a.lat],[a.lon+a.dx,a.lat+a.dy]] }
  }));
  const data = { type:'FeatureCollection', features:feats };
  if (!map.getSource(srcId)) {
    map.addSource(srcId, { type:'geojson', data });
    map.addLayer({ id:'drift', type:'line', source:srcId, paint:{ 'line-color':'#2ea7ff','line-width':2 }});
  } else {
    map.getSource(srcId).setData(data);
  }
}
function bboxFromFC(fc) {
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  const visit = ([x,y]) => {
    if (!Number.isFinite(x)||!Number.isFinite(y)) return;
    if (x<-180||x>180||y<-90||y>90) return;
    if (x<minX)minX=x; if (y<minY)minY=y; if (x>maxX)maxX=x; if (y>maxY)maxY=y;
  };
  for (const f of (fc.features||[])) {
    const g = f.geometry; if (!g) continue;
    if (g.type==='Polygon') for (const ring of g.coordinates) for (const c of ring) visit(c);
    else if (g.type==='MultiPolygon') for (const poly of g.coordinates) for (const ring of poly) for (const c of ring) visit(c);
    else if (g.type==='LineString') for (const c of g.coordinates) visit(c);
    else if (g.type==='Point') visit(g.coordinates);
  }
  if (![minX,minY,maxX,maxY].every(Number.isFinite)) return null;
  if (minX>=maxX || minY>=maxY) return null;
  if ((maxX-minX)>60 || (maxY-minY)>40) return null;
  return [[minX,minY],[maxX,maxY]];
}
function safeFitToData(fc) {
  if (!map) return;
  const bb = bboxFromFC(fc);
  if (bb) {
    const dx = Math.max(0.01, (bb[1][0]-bb[0][0]) * 0.1);
    const dy = Math.max(0.01, (bb[1][1]-bb[0][1]) * 0.1);
    const padded = [[bb[0][0]-dx, bb[0][1]-dy],[bb[1][0]+dx, bb[1][1]+dy]];
    map.fitBounds(padded, { padding: 40, duration: 800 });
  } else {
    map.fitBounds(AOI_BOUNDS, { padding: 40, duration: 600 });
  }
}
function onHazardFrame(ev) {
  let frame; try { frame = JSON.parse(ev.data); } catch { return; }
  if (!frame?.topo?.objects?.hazards) return;
  const gj = topojson.feature(frame.topo, frame.topo.objects.hazards);
  upsertHazards(gj);

  const arrows = (gj.features || [])
    .filter(f => f.properties?.drift && Number.isFinite(f.properties.lon) && Number.isFinite(f.properties.lat))
    .map(f => ({ lat:f.properties.lat, lon:f.properties.lon, dx:f.properties.drift.dx, dy:f.properties.drift.dy }));
  if (arrows.length) upsertDrift(arrows);

  // prefer safe fit on actual FC; avoids bad bbox snapping to (0,0)
  safeFitToData(gj);
}

// --- WebSocket signalling ---
function onSignal(ev) {
  const msg = JSON.parse(ev.data);

  // hazards
  if (msg.type === 'answer' && hazardPC && !hazardPC.currentRemoteDescription) {
    hazardPC.setRemoteDescription({ type:'answer', sdp: msg.sdp }); return;
  }
  if (msg.type === 'candidate' && hazardPC) {
    hazardPC.addIceCandidate(msg.candidate).catch(()=>{}); // fallthrough; other branches may run
  }

  // operator logs
  if (msg.type === 'log') {
    const el = $('#log'); if (el) el.textContent = msg.line + '\n' + el.textContent;
  }

  // clients
  if (msg.type === 'clients') {
    renderClients(msg.clients);
  } else if (msg.type === 'viewer-answer') {
    const c = conns.get(msg.from); if (c) c.pc.setRemoteDescription({ type:'answer', sdp: msg.sdp });
  } else if (msg.type === 'viewer-ice') {
    const c = conns.get(msg.from); if (c) c.pc.addIceCandidate(msg.candidate).catch(()=>{});
  }

  // scan log/status messages would be handled here if you wired them
  if (msg.type === 'scan-log')    { window.__scanUI__?.onLog?.(msg.jobId, msg.line); }
  if (msg.type === 'scan-status') { window.__scanUI__?.onStatus?.(msg.jobId, msg.state, msg.found, msg.downloaded); }

}





//START Drone Actions

// If you have inference polygons, expose them here:
window.inferencePolygons = window.inferencePolygons || [
  { name: 'Result A', coords: [[25.2049,55.2707],[25.2051,55.2713],[25.2044,55.2716],[25.2042,55.2709]] }
];

// Promise-based ctrl send (waits for ack by id)
/* const ctrlWaiters = new Map();
let ctrlSeq = 1000;

function sendCtrlAndWait(clientId, payload, timeoutMs = 15000, { finalOnly = false } = {}) {
  const id = Math.random().toString(36).slice(2, 10);
  payload = { id, ...payload };
  if (!safeCtrlSend(clientId, payload)) return Promise.reject(new Error('ctrl not open'));

  return new Promise((resolve, reject) => {
    const key = `${clientId}:${id}`;
    const t = setTimeout(() => {
      ctrlWaiters.delete(key);
      reject(new Error('timeout'));
    }, timeoutMs);

    ctrlWaiters.set(key, (ok, msg) => {
      // ignore interim acks unless they’re final
      if (finalOnly && msg?.phase && msg.phase !== 'final') return;
      clearTimeout(t);
      ok ? resolve(msg) : reject(new Error(msg?.error || 'error'));
    });
  });
}
 */


const ctrlWaiters = new Map(); // key -> fn(msg)

function sendCtrlAndWait(clientId, payload, third, fourth) {
  const opts = (typeof third === 'number') ? { overallTimeoutMs: third, ...(fourth||{}) } : (third||{});
  const {
    overallTimeoutMs  = 120_000,   // hard cap
    progressTimeoutMs = 60_000,   // extend on any inbound with same id
    requireAckMs      = 60_000,    // must see an ack-ish msg quickly
    finalOnly         = false,    // require done to resolve
    donePred          = (m) => !!(m.done || m.complete || m.completed || m.phase === 'final' || m.state === 'done')
  } = opts;

  const id = Math.random().toString(36).slice(2, 10);
  const out = { id, ...payload };
  if (!safeCtrlSend(clientId, out)) return Promise.reject(new Error('ctrl not open'));

  return new Promise((resolve, reject) => {
    const key = `${clientId}:${id}`;
    let gotAck = false;
    let tOverall, tProgress, tAck;

    const clearAll = () => {
      clearTimeout(tOverall); clearTimeout(tProgress); clearTimeout(tAck);
      ctrlWaiters.delete(key);
    };
    const armProgress = () => {
      clearTimeout(tProgress);
      tProgress = setTimeout(() => { clearAll(); reject(new Error('timeout (no progress)')); }, progressTimeoutMs);
    };

    tOverall = setTimeout(() => { clearAll(); reject(new Error('timeout')); }, overallTimeoutMs);
    tAck     = setTimeout(() => { if (!gotAck) { clearAll(); reject(new Error('timeout (no ack)')); } }, requireAckMs);
    armProgress();

    ctrlWaiters.set(key, (msg) => {
      armProgress();
      if (msg.type === 'ack') gotAck = true;
      if (msg.ok === false) { clearAll(); reject(new Error(msg.error || 'error')); return; }

      if (finalOnly) {
        if (donePred(msg)) { clearAll(); resolve(msg); }
        // else: keep waiting
      } else {
        clearAll(); resolve(msg); // resolve on first ok
      }
    });
  });
}

const getStatus = (clientId) =>
  sendCtrlAndWait(clientId, { cmd:'status' }, {
    overallTimeoutMs: 60_000,
    progressTimeoutMs: 30_000,
    requireAckMs: 30_000,      // status often has no ack
    finalOnly: false      // resolve on first OK
  });

const armTakeoff = (clientId, agl = 10) =>
    sendCtrlAndWait(clientId, { cmd: 'arm_takeoff', agl }, {
      overallTimeoutMs: 20_000,
      progressTimeoutMs: 8_000,
      requireAckMs: 3_000,
      finalOnly: false,
    });
  
// --- shared "done" predicate for agents that vary field names
const CTRL_DONE = (m) =>
  !!(m?.done || m?.complete || m?.completed ||
     m?.state === 'done' || m?.phase === 'final' || m?.phase === 'completed' ||
     m?.phase === 'arrived');

// (optional) thin ring decimator so goto_polygon doesn't choke on huge rings
const decimate = (ring, step = 3) => {
  if (!Array.isArray(ring) || ring.length < 4) return ring;
  const out = [];
  for (let i = 0; i < ring.length - 1; i += step) out.push(ring[i]);
  const f = ring[0], l = out[out.length - 1];
  if (!l || l[0] !== f[0] || l[1] !== f[1]) out.push(f);
  return out.length >= 4 ? out : ring;
};

// --- nice wrappers around sendCtrlAndWait (robust/options-aware version)
const ctrlOps = {

  getStatus(clientId) {
    return sendCtrlAndWait(clientId, { cmd:'status' }, {
      overallTimeoutMs: 60_000,
      progressTimeoutMs: 30_000,
      requireAckMs: 30_000,      // status often has no ack
      finalOnly: false      // resolve on first OK
    });
  },

  // quick command; resolve on first OK (no final)
  armTakeoff(clientId, agl = 10) {
    return sendCtrlAndWait(clientId, { cmd: 'arm_takeoff', agl }, {
      overallTimeoutMs: 60_000,
      progressTimeoutMs: 30_000,
      requireAckMs: 30_000,      // status often has no ack
      finalOnly: false,
    });
  },

  // long command; require final/done; allow long progress gaps
  gotoEdge(clientId, polygon, agl = 12) {
    const poly = decimate(polygon, 3);
    return sendCtrlAndWait(clientId, { cmd: 'goto_polygon', polygon: poly, agl, strategy: 'edge' }, {
      overallTimeoutMs: 10 * 60_000,   // 10 min cap
      progressTimeoutMs: 60_000,       // agent may be quiet for a while
      requireAckMs: 30_000,             // fail early if not accepted
      finalOnly: false,
      donePred: CTRL_DONE,
    });
  },

  gotoCentroid(clientId, polygon, agl = 12) {
    const poly = decimate(polygon, 3);
    return sendCtrlAndWait(clientId, { cmd: 'goto_polygon', polygon: poly, agl, strategy: 'centroid' }, {
      overallTimeoutMs: 10 * 60_000,
      progressTimeoutMs: 60_000,
      requireAckMs: 30_000,
      finalOnly: false,
      donePred: CTRL_DONE,
    });
  },

  // duration-based; expect a final completion
  deploy(clientId, sec) {
    const durMs = Math.max(1, Number(sec)) * 1000;
    return sendCtrlAndWait(clientId, { cmd: 'deploy', duration_s: sec }, {
      overallTimeoutMs: durMs + 15_000,      // duration + buffer
      progressTimeoutMs: Math.min(durMs, 20_000),
      requireAckMs: 30_000,
      finalOnly: true,
      donePred: CTRL_DONE,
    });
  },

  // quick command; optionally wait until landed/idle via status polling
  async rtl(clientId, { waitLanding = false, maxWaitMs = 8 * 60_000 } = {}) {
    await sendCtrlAndWait(clientId, { cmd: 'rtl', use_rtl: true }, {
      overallTimeoutMs: 30_000,
      progressTimeoutMs: 10_000,
      requireAckMs: 30_000,
      finalOnly: false,
    });
    if (!waitLanding) return true;

    // status-poll fallback to detect end of RTL
    const t0 = Date.now();
    for (;;) {
      const s = await sendCtrlAndWait(clientId, { cmd: 'status' }, {
        overallTimeoutMs: 12_000, progressTimeoutMs: 6_000, requireAckMs: 0, finalOnly: false
      });
      const landed = (s?.in_air === false) || (s?.flight_mode === 'HOLD' || s?.flight_mode === 'MANUAL');
      if (landed) return true;
      if (Date.now() - t0 > maxWaitMs) throw new Error('RTL landing timeout');
      await new Promise(r => setTimeout(r, 5_000));
    }
  }
};



function parsePolygonInput(text) {
  // Try WKT first
  const wktMatch = text.match(/^POLYGON\s*\(\(\s*(.+?)\s*\)\)$/i);
  if (wktMatch) {
    const coords = wktMatch[1].split(',').map(pair => {
      const [lon, lat] = pair.trim().split(/\s+/).map(Number);
      if (isNaN(lat) || isNaN(lon)) throw new Error('Invalid WKT coordinates');
      return [lat, lon]; // Flip to [lat, lon]
    });
    return coords;
  }

  // Try JSON formats
  try {
    const obj = JSON.parse(text);
    if (Array.isArray(obj) && Array.isArray(obj[0]) && obj[0].length === 2) return obj;
    if (obj && obj.type === 'FeatureCollection') {
      const poly = obj.features.find(f => f.geometry.type === 'Polygon')?.geometry.coordinates?.[0];
      if (poly) return poly.map(([lon, lat]) => [lat, lon]);
    }
    if (obj && obj.type === 'Polygon') {
      const ring = obj.coordinates?.[0];
      if (ring) return ring.map(([lon, lat]) => [lat, lon]);
    }
  } catch {}

  throw new Error('Invalid polygon input');
}


// Modal state
const modal = document.getElementById('actions-modal');
const elDroneId = document.getElementById('act-drone-id');
const elDroneStatus = document.getElementById('act-status');
/* const elHomeLat = document.getElementById('act-home-lat');
const elHomeLon = document.getElementById('act-home-lon'); */
const elPolySel = document.getElementById('act-poly-select');
const elPolyCustomBtn = document.getElementById('act-poly-custom-btn');
const elPolyCustomRow = document.getElementById('act-poly-custom-row');
const elPolyCustom = document.getElementById('act-poly-custom');

function openActionsModal(clientId) {
  // populate fields
  elDroneId.value = clientId;
  
  elPolySel.innerHTML = '';
  (window.inferencePolygons || []).forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = i; opt.textContent = p.name || `Polygon ${i+1}`;
    elPolySel.appendChild(opt);
  });
  elPolyCustomRow.classList.add('hidden');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

// close modal
document.getElementById('act-close').onclick = () => {
  modal.classList.add('hidden'); modal.setAttribute('aria-hidden','true');
};



elPolyCustomBtn.onclick = () => elPolyCustomRow.classList.toggle('hidden');

function getChosenPolygon(){
  if (!elPolyCustomRow.classList.contains('hidden') && elPolyCustom.value.trim()){
    return parsePolygonInput(elPolyCustom.value.trim());
  }
  const idx = parseInt(elPolySel.value || '0', 10) || 0;
  return (window.inferencePolygons?.[idx]?.coords) || [];
}

document.getElementById('btn-get-status').onclick = async () => {
  const clientId = elDroneId.value;
  const poly = getChosenPolygon();
  try {
    //const s = await sendCtrlAndWait(clientId, { cmd:'status'}, 30000, { finalOnly:true }); 
    //const s = await getStatus(clientId);
    const s = await ctrlOps.getStatus(clientId);
    elDroneStatus.value = JSON.stringify(s, null, 2);

    console.log('status=',listEl,s, s.status, 'armed=', s.armed, 'batt=', s.battery_pct, 'mode=', s.flight_mode);
  } catch (e) { alert('Failed: ' + e.message); }
};

document.getElementById('btn-arm-takeoff').onclick = async () => {
  const clientId = elDroneId.value;
  const poly = getChosenPolygon();
  try {
    //await sendCtrlAndWait(clientId, { cmd:'arm_takeoff', agl: 10 }, 30000, { finalOnly:true }); 
    await ctrlOps.armTakeoff(clientId, 10);
  } catch (e) { alert('Failed: ' + e.message); }
};

document.getElementById('btn-goto-edge').onclick = async () => {
  const clientId = elDroneId.value;
  const poly = getChosenPolygon();
  try {
    //await sendCtrlAndWait(clientId, { cmd:'goto_polygon', polygon: poly, agl: 12, strategy: 'edge' });
    await ctrlOps.gotoEdge(clientId, poly, 12);
  } catch (e) { alert('Failed: ' + e.message); }
};

document.getElementById('btn-hover-center').onclick = async () => {
  const clientId = elDroneId.value;
  const poly = getChosenPolygon();
  try {
    //await sendCtrlAndWait(clientId, { cmd:'goto_polygon', polygon: poly, agl: 12, strategy: 'centroid' });
    await ctrlOps.gotoCentroid(clientId, poly, 12);
  } catch (e) { alert('Failed: ' + e.message); }
};

/* document.getElementById('btn-survey').onclick = async () => {
  const clientId = elDroneId.value;
  const poly = getChosenPolygon();

  const sec = Math.max(1, parseInt(document.getElementById('act-deploy-sec').value || '5', 10));
  try {
    // Requires a small handler on the Pi to iterate polygon vertices; see note below.
    await sendCtrlAndWait(clientId, { cmd:'survey_polygon', polygon: poly, agl: 12 });
    //await sendCtrlAndWait(clientId, { cmd:'arm_takeoff', agl: sec }); 
  } catch (e) { alert('Failed: ' + e.message); }
}; */

document.getElementById('btn-deploy').onclick = async () => {
  const clientId = elDroneId.value;
  const sec = Math.max(1, parseInt(document.getElementById('act-deploy-sec').value || '5', 10));
  ///try { await sendCtrlAndWait(clientId, { cmd:'deploy', duration_s: sec }, 30000, { finalOnly:true }); }
try { 
    //await sendCtrlAndWait(clientId, { cmd:'arm_takeoff', agl: sec }); 
    //const s = await sendCtrlAndWait(clientId, { cmd:'status'}, 30000, { finalOnly:true }); 
    //console.log('status=',listEl,s, s.status, 'armed=', s.armed, 'batt=', s.battery_pct, 'mode=', s.flight_mode);
    //await sendCtrlAndWait(clientId, { cmd:'deploy', duration_s: sec }, 30000, { finalOnly:true });
    await ctrlOps.deploy(clientId, sec);
  } 
  catch (e) { alert('Failed: ' + e.message); }
};

document.getElementById('btn-rtl').onclick = async () => {
  const clientId = elDroneId.value;
  try { 
    //await sendCtrlAndWait(clientId, { cmd:'rtl', use_rtl: true });
    await ctrlOps.rtl(clientId, { waitLanding: false }); }
  catch (e) { alert('Failed: ' + e.message); }
};



//END DroneActions



//Incidents
// ===== Incidents Tab (API + UI) =====
const API_BASE = ''; // same origin as your Flask API

function goToIncidentsTab() {
  document.querySelector('nav .tab[data-tab="incidents"]')?.click();
}


function centroidOfPolyRing(ring) {
  let x=0, y=0, a=0;
  for (let i=0, j=ring.length-1; i<ring.length; j=i++) {
    const [x1,y1]=ring[j], [x2,y2]=ring[i];
    const f = x1*y2 - x2*y1; a += f; x += (x1+x2)*f; y += (y1+y2)*f;
  }
  if (!a) { const n=ring.length; const s=ring.reduce((p,[lo,la])=>(p[0]+=lo,p[1]+=la,p),[0,0]); return { lon:s[0]/n, lat:s[1]/n }; }
  a *= 0.5; return { lon:x/(6*a), lat:y/(6*a) };
}

function incidentTargetLatLon(incident) {
  // Prefer footprint centroid if present, else centroid, else null
  try {
    if (incident.footprint?.type === 'Polygon') {
      const ring = incident.footprint.coordinates?.[0];
      if (Array.isArray(ring) && ring.length >= 3) {
        const c = centroidOfPolyRing(ring);   // you already have this util
        return { lat: c.lat, lon: c.lon };
      }
    }
    if (incident.centroid?.type === 'Point') {
      const [lon, lat] = incident.centroid.coordinates || [];
      if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon };
    }
    if (incident.centroid?.lat != null && incident.centroid?.lon != null) {
      return { lat: Number(incident.centroid.lat), lon: Number(incident.centroid.lon) };
    }
  } catch {}
  return null;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = d => d * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
            Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function kmToM(km) { return km * 1000; }

function minDistToRingMeters(lat, lon, ring /* [[lat,lon],...] */) {
  if (!Array.isArray(ring) || ring.length === 0) return Infinity;
  // simple & fast: min distance to vertices (good enough for "arrived" threshold)
  return ring.reduce((best, [vlat, vlon]) => {
    const d = kmToM(haversineKm(lat, lon, vlat, vlon));
    return Math.min(best, d);
  }, Infinity);
}

// --- arrival watchers (keyed by `${iid}:${clientId}`) ---
const arrivalWatchers = new Map();
/**
 * plan = {
 *   mode: 'centroid' | 'edge',
 *   centroid?: {lat,lon},
 *   polygon?: [[lat,lon],...],   // if mode==='edge'
 *   thresholdM?: number          // default 60
 * }
 */

function startArrivalWatcher(iid, clientId, plan) {
  const key = `${iid}:${clientId}`;
  arrivalWatchers.set(key, {
    iid,
    clientId,
    mode: plan.mode || 'centroid',
    centroid: plan.centroid || null,
    polygon:  plan.polygon  || null,
    thresholdM: Number.isFinite(plan.thresholdM) ? plan.thresholdM : 60,
    fired: false
  });
  // return a small disposer if you want to cancel later
  return () => arrivalWatchers.delete(key);
}

async function maybeFireArrival(iid, clientId, distM) {
  try {
    document.dispatchEvent(new CustomEvent('uav-arrived', { detail: { iid, clientId, distM } }));
    // toast/alert is optional
    try { toast?.(`UAV ${clientId} arrived (~${Math.round(distM)}m)`, { type: 'success' }); } catch {}
    await fetch(`${API_BASE}/api/incidents/${iid}/arrived`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ client_id: clientId, dist_m: distM })
    });
  } catch (e) {
    console.warn('arrived POST failed', e);
  }
}

function checkArrivalAfterTelemetry(id) {
  // called after we upsert clients[id]
  if (!clients[id]) return;
  const { lat, lon } = clients[id] || {};
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

  for (const [key, w] of arrivalWatchers) {
    if (w.clientId !== id || w.fired) continue;

    let distM = Infinity;
    if (w.mode === 'centroid' && w.centroid) {
      distM = kmToM(haversineKm(lat, lon, w.centroid.lat, w.centroid.lon));
    } else if (w.mode === 'edge' && Array.isArray(w.polygon)) {
      distM = minDistToRingMeters(lat, lon, w.polygon);
    }

    if (distM <= w.thresholdM) {
      w.fired = true;                     // one-shot
      arrivalWatchers.set(key, w);        // persist change
      maybeFireArrival(w.iid, w.clientId, distM);
      // no continue; keep loop to allow other watchers on same id if you ever have them
    }
  }
}

function waitForArrival(iid, clientId, { maxWaitMs = 5*60_000 } = {}) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      cleanup();
      reject(new Error('arrival timeout'));
    }, maxWaitMs);

    const onEvt = (e) => {
      if (e.detail?.iid === iid && e.detail?.clientId === clientId) {
        cleanup(); resolve(e.detail);
      }
    };
    const cleanup = () => {
      clearTimeout(t);
      document.removeEventListener('uav-arrived', onEvt);
    };
    document.addEventListener('uav-arrived', onEvt);
  });
}



function extractIncidentLL(incident) {
  // 1) explicit lat/lon fields
  if (incident.centroid_lat != null && incident.centroid_lon != null) {
    return { lat: Number(incident.centroid_lat), lon: Number(incident.centroid_lon) };
  }
  // 2) GeoJSON Point
  if (incident.centroid?.type === 'Point' && Array.isArray(incident.centroid.coordinates)) {
    const [lon, lat] = incident.centroid.coordinates;
    return { lat: Number(lat), lon: Number(lon) };
  }
  // 3) centroid from GeoJSON Polygon/MultiPolygon (very simple average of shell)
  const g = incident.footprint;
  if (g?.type === 'Polygon' && Array.isArray(g.coordinates?.[0])) {
    const ring = g.coordinates[0];
    let sx=0, sy=0, n=0;
    for (const [lon, lat] of ring) { sx += lon; sy += lat; n++; }
    if (n) return { lat: sy/n, lon: sx/n };
  }
  if (g?.type === 'MultiPolygon' && Array.isArray(g.coordinates?.[0]?.[0])) {
    const ring = g.coordinates[0][0];
    let sx=0, sy=0, n=0;
    for (const [lon, lat] of ring) { sx += lon; sy += lat; n++; }
    if (n) return { lat: sy/n, lon: sx/n };
  }
  return null;
}

function getClientIdsFromList(listEl) {
  if (typeof listEl === 'string') listEl = document.getElementById(listEl);
  if (!listEl) return [];

  const ids = new Set();

  // Prefer explicit attributes if present
  listEl.querySelectorAll('[data-client-id]').forEach(el => ids.add(el.dataset.clientId));
  listEl.querySelectorAll('[data-id]').forEach(el => ids.add(el.dataset.id));
  listEl.querySelectorAll('option[value]').forEach(el => ids.add(el.value));

  // Fallback: text inside <li>
  listEl.querySelectorAll('li').forEach(li => {
    const raw = (li.dataset.clientId || li.dataset.id || li.getAttribute('value') || li.textContent || '').trim();
    if (!raw) return;
    // If the LI has extra text, extract an id-like token (optional)
    const m = raw.match(/[A-Za-z]+-[A-Za-z0-9]{3,}/);
    ids.add(m ? m[0] : raw);
  });

  return [...ids].filter(Boolean);
}

async function getTaskedDroneId(incidentId) {
  const r = await fetch(`${API_BASE}/api/incidents/${incidentId}/verification_task`, { method: 'GET' });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || 'failed to load verification_task');
  // try a few common shapes
  return j.drone_id ?? j.task?.drone_id ?? j?.[0]?.drone_id ?? null;
}


/**
 * Pick nearest idle client for an incident.
 * @param {HTMLElement} listEl  The DOM element containing the clients list.
 * @param {string|object} incidentOrId  Incident id or an incident object.
 * @returns {Promise<string|null>}  clientId or null if none suitable.
 */
async function pickNearestIdleClient(listEl, incidentOrId) {
  // 1) get incident
  const incident = (typeof incidentOrId === 'string')
    ? (await IncidentsAPI.getIncident(incidentOrId)).incident
    : incidentOrId;
    

  const tgt = extractIncidentLL(incident);
  if (!tgt) {
    console.warn('No incident location available.');
    return null;
  }
  console.log(tgt)
  // 2) collect client IDs
  const clientIds = getClientIdsFromList(listEl);
  
  if (!clientIds.length) return null;
  

  // 3) query everyone (in parallel) for status
  const results = await Promise.all(clientIds.map(async (cid) => {
    try {
      //const s = await sendCtrlAndWait(cid, { cmd:'status' }, 30000, { finalOnly:true });
      //const s = await getStatus(cid);
      const s = await ctrlOps.getStatus(cid);
      // expect fields: status, lat, lon, armed, in_air, battery_pct, flight_mode, etc.
      if (s?.lat == null || s?.lon == null) return null; // can’t compute distance
      const dist_km = haversineKm(tgt.lat, tgt.lon, Number(s.lat), Number(s.lon));
      //console.log('Client ',cid, ' = ', dist_km, ' kms from the spill')
      return { cid, s, dist_km };
    } catch (e) {
      // ignore offline/timeouts
      return null;
    }
  }));

  const viable = results.filter(Boolean);

  // 4) prefer IDLE; else fall back to nearest on ground (not in_air)
  const idle = viable
    .filter(r => r.s?.status === 'idle')
    .sort((a, b) => a.dist_km - b.dist_km);

  //if (idle.length) return idle[0].cid;
  if (idle.length) return idle[0];

  const onGround = viable
    .filter(r => r.s?.in_air === false) // armed or not, but on ground
    .sort((a, b) => a.dist_km - b.dist_km);

  //return onGround[0]?.cid ?? null;
  return onGround[0] ?? null;
}

/**
 * Scan all clientIds in listEl, query status via ctrl channel,
 * and return the clientId whose reported s.droneId matches the
 * verification_tasks.drone_id for the given incident.
 */
async function findTaskedClientIdFromList(listEl, incidentId) {
  const ids = getClientIdsFromList(listEl);
  if (!ids.length) throw new Error('no client ids found in listEl');

  const taskedDroneId = await IncidentsAPI.getVerificationTask(incidentId)
  //const taskedDroneId = await IncidentsAPI.taskVerify(incidentId)
  
  if (!taskedDroneId) throw new Error('no tasked drone_id on server');
  console.log('tasked drone id',ids,taskedDroneId.task.drone_id)
  // ask every client for status (in parallel), tolerate timeouts
  const settles = await Promise.allSettled(
    ids.map(id =>
      //sendCtrlAndWait(id, { cmd: 'status' }, 30000, { finalOnly: true })
      //getStatus(id)
      ctrlOps.getStatus(id)
        .then(s => ({ id, s }))
    )
  );

  // find first whose reported droneId matches the tasked one
  for (const res of settles) {
    if (res.status !== 'fulfilled') continue;
    const { id, s } = res.value || {};
    const reported = s?.droneId ?? s?.drone_id ?? s?.drone?.id;
    // helpful debug
    console.debug('[status]', id, '→ reported:', reported, 'tasked:', taskedDroneId.task.drone_id, s);
    if (reported && String(reported) === String(taskedDroneId.task.drone_id)) {
      return id;
    }
  }
  return null; // nothing matched
}


function ringLonLatToLatLon(ring) {
  return ring.map(([lon, lat]) => [lat, lon]);
}
function squareAround(lat, lon, halfSideMeters = 150) {
  const dLat = halfSideMeters / 111320;
  const dLon = halfSideMeters / (111320 * Math.cos(lat * Math.PI / 180));
  return [
    [lat - dLat, lon - dLon],
    [lat - dLat, lon + dLon],
    [lat + dLat, lon + dLon],
    [lat + dLat, lon - dLon],
    [lat - dLat, lon - dLon],
  ];
}

const isFiniteNum = (x) => Number.isFinite(x);

function toLatLon(pair){
  let [a,b] = pair.map(Number);
  const looksLatLon = Math.abs(a) <= 90 && Math.abs(b) <= 180;
  const looksLonLat = Math.abs(b) <= 90 && Math.abs(a) <= 180;
  if (!looksLatLon && looksLonLat) [a,b] = [b,a]; // auto-fix lon,lat inputs
  return [a,b];
}

function closeRing(r){
  const f = r[0], l = r[r.length-1];
  return (f && l && (f[0]===l[0] && f[1]===l[1])) ? r : [...r, f];
}

function cleanRing(r){
  let ring = r.map(toLatLon).filter(([lat,lon]) => isFiniteNum(lat) && isFiniteNum(lon));
  ring = closeRing(ring);
  return ring.length >= 4 ? ring : [];
}

function ringArea(r){ // planar-ish area for ranking
  let A = 0;
  for (let i=0;i<r.length-1;i++){
    const [y1,x1] = r[i], [y2,x2] = r[i+1]; // treat (lat,lon) as (y,x)
    A += (x1*y2 - x2*y1);
  }
  return Math.abs(A)/2;
}

// Accepts ring / Polygon / MultiPolygon / GeoJSON Feature
function flattenGeo(geom){
  if (!geom) return [];
  // simple ring [[lat,lon], ...]
  if (Array.isArray(geom) && Array.isArray(geom[0]) && typeof geom[0][0] !== 'object'){
    const r = cleanRing(geom);
    return r.length ? [r] : [];
  }
  // Polygon: [outer, holes...]
  if (Array.isArray(geom) && Array.isArray(geom[0]) && Array.isArray(geom[0][0]) && typeof geom[0][0][0] !== 'object'){
    const r = cleanRing(geom[0] || []);
    return r.length ? [r] : [];
  }
  // MultiPolygon: [[[outer,...]], [[outer,...]], ...]
  if (Array.isArray(geom) && Array.isArray(geom[0]) && Array.isArray(geom[0][0]) && Array.isArray(geom[0][0][0])){
    const rings = [];
    for (const poly of geom){
      const r = cleanRing((poly && poly[0]) || []);
      if (r.length) rings.push(r);
    }
    return rings;
  }
  if (geom?.type === 'Feature')       return flattenGeo(geom.geometry);
  if (geom?.type === 'Polygon')       return flattenGeo(geom.coordinates);
  if (geom?.type === 'MultiPolygon')  return flattenGeo(geom.coordinates);
  return [];
}

function pickPrimaryRing(rings){
  if (!rings.length) return [];
  let best = rings[0], bestA = ringArea(best);
  for (let i=1;i<rings.length;i++){
    const a = ringArea(rings[i]);
    if (a > bestA) { best = rings[i]; bestA = a; }
  }
  return best;
}



function polygonForIncident(incident, fallbackHalfSideM = 150) {
  /* if (incident?.footprint?.type === 'Polygon' && incident.footprint.coordinates?.[0]) {
    console.log('[polygonForIncident-footprint]',incident)
    return ringLonLatToLatLon(incident.footprint.coordinates[0]);
  } */
  if (incident?.centroid?.type === 'Point') {
    const [lon, lat] = incident.centroid.coordinates;
    console.log('[polygonForIncident-centroid]',lat,lon)
    return squareAround(lat, lon, fallbackHalfSideM);
  }
  return null;
}


function pickLargestPolygon(fc) {
  let best=null, bestArea=-1;
  for (const f of (fc.features||[])) {
    if (!f?.geometry || f.geometry.type!=='Polygon') continue;
    const km2 = areaKm2FromGeoJSON({ type:'Feature', geometry:f.geometry, properties:{} }); // you already have areaKm2FromGeoJSON
    if (km2 > bestArea) { bestArea = km2; best = { feature:f, area_km2: km2 }; }
  }
  return best;
}


async function fetchSensitiveAreas(lat, lon, radiusKm = 30){
  const q = new URLSearchParams({ lat, lon, radius_km: String(radiusKm) });
  const res = await fetch(`${API_BASE}/api/sensitive-areas?${q.toString()}`);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'query failed');
  return data;
}

async function fetchResponsePlan(iid){
  
  const res = await fetch(`${API_BASE}/api/incidents/${iid}/response-plan`);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'query failed');
  return data;
}




const DetectionsAPI = {
  async ingest(fc, meta = {}) {
    const r = await fetch(`${API_BASE}/api/detections/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Optional: if org_id is required server-side and no DEFAULT_ORG_ID:
        // 'X-Org-Id': 'YOUR-ORG-UUID',
      },
      body: JSON.stringify({ fc, ...meta }),
    });
    const txt = await r.text();
    if (!r.ok) throw new Error(`ingest failed ${r.status} ${r.statusText}: ${txt.slice(0,200)}`);
    const j = JSON.parse(txt);
    if (!j.ok) throw new Error(j.error || 'ingest failed');
    return j;
  },
};



const IncidentsAPI = {
  async listIncidents(state = 'open', limit = 50) {
    
    const r = await fetch(`${API_BASE}/api/incidents?state=${encodeURIComponent(state)}&limit=${limit}`);
    
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Failed to fetch incidents');
    return j.items;
  },
  async getIncident(id) {
    const r = await fetch(`${API_BASE}/api/incidents/${id}`);
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Incident not found');
    return j;
  },
  async createFromDetection(payload) {
    const r = await fetch(`${API_BASE}/api/incidents/from-detection`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
    });
    const txt = await r.text();
    if (!r.ok) throw new Error(`Create incident failed: ${r.status} ${r.statusText} ${txt.slice(0,120)}`);
    const j = JSON.parse(txt);
    if (!j.ok) throw new Error(j.error || 'Create incident failed');
    return j.incident;
  },
  async triage (incidentId) {
    const r = await fetch(`${API_BASE}/api/incidents/${incidentId}/triage`, { method: 'POST' });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'Triage failed');
    return j; // { area_km2, dist_km, priority }
  },

  async  getVerificationTask(incidentId, { active = true } = {}) {
    const url = `${API_BASE}/api/incidents/${incidentId}/get_verification_task` +
                (active ? '?status=active' : '');
    const r = await fetch(url, { method: 'GET' });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'Fetch verification_task failed');
    return j; // { ok, task:{drone_id,...}, polygon_* }
  },

  async taskVerify(incidentId, body) {
    const r = await fetch(`${API_BASE}/api/incidents/${incidentId}/task-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), // {client_id, mode, agl_m, polygon?}
    });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'Task verify failed');
    return j;
  },
  async  postVerifyResult(incidentId, outcome, clientId) {
    const notes = document.getElementById('verifyNotes')?.value || '';
    const r = await fetch(`${API_BASE}/api/incidents/${incidentId}/verify-result`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ client_id: clientId, outcome, notes })
    });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'verify failed');
    return j;
  },
  async  completeResponse(incidentId, outcome, clientId) {
    const notes = document.getElementById('verifyNotes')?.value || '';
    const r = await fetch(`${API_BASE}/api/incidents/${incidentId}/complete-response`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ client_id: clientId, outcome, notes })
    });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || 'verify failed');
    return j;
  },
  
  async autoPlan(incidentId) {
    const r = await fetch(`${API_BASE}/api/auto-plan/${incidentId}`, {
      method: 'POST',
      headers: { 'X-User-Id': 'operator-ui' } // <-- adjust as needed
    });
    const txt = await r.text();
    let j;
    try { j = JSON.parse(txt); } catch {
      throw new Error(`Auto-plan failed: HTTP ${r.status} ${r.statusText} — ${txt.slice(0,200)}`);
    }
    if (!r.ok || !j.ok) throw new Error(j.error || `Auto-plan failed: HTTP ${r.status}`);
    return j;
  },
  async listRules() {
    const r = await fetch(`${API_BASE}/api/response-rules`);
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Failed to fetch rules');
    return j.items;
  },
  async updateRule(id, payload) {
    const r = await fetch(`${API_BASE}/api/response-rules/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'Failed to save rule');
    return true;
  },
};

const IncidentsUI = (() => {
  // DOM refs (lazy)
  let tableBody, filterSel, refreshBtn, detailBox, titleEl, stateEl, priorityEl, verifEl, areaEl,protEl,portEl,desalEl, distEl, tierLevelEl,incidentDescriptionEl,tlEl, taskBtn, confirmBtn, refuteBtn, unsureBtn, planBtn, responseBtn,closeBtn,rulesBody;
  let initialised = false;

  function $(id) { return document.getElementById(id); } 
  function setStatus(msg) {
    const s = document.getElementById('status');
    if (s) s.textContent = msg;
  }

  function fmt(x, d = 3) {
    if (x === null || x === undefined || Number.isNaN(x)) return '—';
    return Number(x).toFixed(d);
  }

  async function refreshList() {
    try {
      setStatus('loading incidents…');
      const state = filterSel.value;
      const items = await IncidentsAPI.listIncidents(state);
      renderTable(items);
      setStatus('ready');

      
    } catch (e) {
      console.error(e);
      setStatus('failed to load incidents');
      tableBody.innerHTML = `<tr><td colspan="7" style="color:#b91c1c;">${e.message}</td></tr>`;
    }
  }

  function renderTable(items) {
    tableBody.innerHTML = '';
    if (!items.length) {
      tableBody.innerHTML = `<tr><td colspan="7" style="opacity:.7;">No incidents</td></tr>`;
      return;
    }
    for (const row of items) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family:monospace;">${row.id}</td>
        <td>${row.title}</td>
        <td>${row.state}</td>
        <td>${row.priority}</td>
        <!--<td>${fmt(row.est_area_sqkm)}</td>

        <td>${fmt(row.dist_shore_km)}</td>-->
        <td>${row.updated_at}</td>
        <td><button class="btn btn-sm" data-open="${row.id}">Open</button></td>
      `;
      tableBody.appendChild(tr);
    }
  }
  let currentIncident = null;
  async function openIncident(id) {
    try {
      setStatus(`loading ${id}…`);
      const { incident, timeline } = await IncidentsAPI.getIncident(id);
      currentIncident = incident;
      console.log(['incident details'],incident)

      const { nearest, lists } = await fetchSensitiveAreas(incident.centroid.coordinates[1], incident.centroid.coordinates[0], 100);
      console.log('Nearest port:', nearest.port);
      console.log('Nearest protected area:', nearest.protected_area);
      console.log('Nearest desalination:', nearest.desalination);

      //const best = selectNearestAvailableClient(incident);
      //if (best) {
      //  console.log('[deploy] nearest available client:', best.id, best.dist.toFixed(2), 'km');
        /* const sel = document.getElementById('deploy-client');
        if (sel) sel.value = best.id; */ // preselect in dropdown
      //} else {
      //  console.warn('[deploy] no suitable client found (unarmed + recent + gps)');
      //}

      titleEl.textContent = `${incident.title} (${incident.id})`;
      stateEl.textContent = incident.state;
      priorityEl.textContent = incident.priority;
      verifEl.textContent = incident.verification ?? '—';
      areaEl.textContent = fmt(incident.est_area_sqkm);
      distEl.textContent = fmt(incident.dist_shore_km);

      const prot  = nearest?.protected_area ?? null;
      const desal = nearest?.desalination   ?? null;
      const port  = nearest?.port           ?? null;

      const respPlan = await fetchResponsePlan(incident.id);
      console.log('[response plan]',respPlan.plan);
      renderResponsePlanBox(respPlan);

      const tier_level = respPlan?.tier_level ?? null;
      const incident_description = respPlan?.tier_description ?? null;
      tierLevelEl.textContent = tier_level;
      incidentDescriptionEl.textContent = incident_description;

      // if (protEl)  protEl.textContent  = prot?.name  ?? 'None within 100 Km radius';
      if (protEl) {protEl.textContent = prot?.name && prot?.distance_m != null ? `${prot.name} - ${(prot.distance_m / 1000).toFixed(2)} km`  : 'None within 100 Km radius';}
      if (desalEl) {desalEl.textContent = desal?.name && desal?.distance_m != null ? `${desal.name} - ${(desal.distance_m / 1000).toFixed(2)} km`  : 'None within 100 Km radius';}
      //if (desalEl) desalEl.textContent = desal?.name ?? 'None within 100 Km radius';
      if (portEl)  portEl.textContent  = port?.name  ?? 'None within 100 Km radius';
      if (portEl) {portEl.textContent = port?.name && port?.distance_m != null ? `${port.name} - ${(port.distance_m / 1000).toFixed(2)} km`  : 'None within 100 Km radius';}

      
      
      
      
      tlEl.innerHTML = '';
      (timeline || []).forEach(ev => {
        const li = document.createElement('li');
        li.textContent = `${ev.at} — ${ev.event_type} ${ev.from_state ?? ''}→${ev.to_state ?? ''}`;
        tlEl.appendChild(li);
      });

      
      

      if (taskBtn) {
        // enable/disable depending on stage
        //Detection, Triage, VerificationTasked, VerificationResult, ResponsePlanning, ResponseActive, ResponseComplete
        //taskBtn.disabled = !['Triage','VerificationTasked','VerificationResult','Detection'].includes(incident.state);
        taskBtn.disabled = !['Triage'].includes(incident.state);
        
        taskBtn.onclick = async () => {
          try {
            const choice = await pickNearestIdleClient(listEl, currentIncident?.id || null);
            if (!choice) {                     // <? guard for null
              alert('No suitable client found (idle or on-ground with GPS).');
              return;
            }
            const { cid, dist_km } = choice;   // <? use the returned object
            console.log('Chosen client:', cid, 'dist_km=', dist_km);

            taskBtn.disabled = true;

            const mode = 'centroid';
            const agl  = 10;
            const cruiseSpeed_km_per_sec = 0.0040;
            const tripSeconds = 1000 * dist_km / cruiseSpeed_km_per_sec;

            if (dist_km > 100) throw new Error('All available UAV are too far.');
            await deployForVerification(mode, cid, agl, tripSeconds); //0.004 is the drone speed in km/second

            await refreshList();
            await openIncident(currentIncident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            taskBtn.disabled = false;
          }
        };
      }

      if (confirmBtn) {
        // enable/disable depending on stage
        //Detection, Triage, VerificationTasked, VerificationResult, ResponsePlanning, ResponseActive, ResponseComplete
        confirmBtn.disabled = !['VerificationTasked'].includes(incident.state);

        confirmBtn.onclick = async () => {
          try {

            const ClientId = await findTaskedClientIdFromList(listEl, currentIncident?.id || null)
            if (!ClientId) {
              alert('No client found (reestablish connection).');
            } else {
              console.log('Chosen client:', ClientId);
            }
            confirmBtn.disabled = true;

            await IncidentsAPI.postVerifyResult(currentIncident?.id || null, 'confirmed', ClientId);
            
            // refresh list & detail so UI shows "VerificationTasked"
            await refreshList();
            await openIncident(incident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            confirmBtn.disabled = false;
          }
        };
      }

      if (refuteBtn) {
        // enable/disable depending on stage
        //Detection, Triage, VerificationTasked, VerificationResult, ResponsePlanning, ResponseActive, ResponseComplete
        refuteBtn.disabled = !['VerificationTasked'].includes(incident.state);

        refuteBtn.onclick = async () => {
          try {

            
            const ClientId = await findTaskedClientIdFromList(listEl, currentIncident?.id || null)
            if (!ClientId) {
              alert('No client found (reestablish connection).');
            } else {
              console.log('Chosen client:', ClientId);
            }
            refuteBtn.disabled = true;

            await IncidentsAPI.postVerifyResult(currentIncident?.id || null, 'refuted', ClientId);

            
            
            // refresh list & detail so UI shows "VerificationTasked"
            await refreshList();
            await openIncident(incident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            refuteBtn.disabled = false;
          }
        };
      }

      if (unsureBtn) {
        // enable/disable depending on stage
        //Detection, Triage, VerificationTasked, VerificationResult, ResponsePlanning, ResponseActive, ResponseComplete
        unsureBtn.disabled = !['VerificationTasked'].includes(incident.state);

        unsureBtn.onclick = async () => {
          try {

            
            const ClientId = await findTaskedClientIdFromList(listEl, currentIncident?.id || null)
            if (!ClientId) {
              alert('No client found (reestablish connection).');
            } else {
              console.log('Chosen client:', ClientId);
            }
            unsureBtn.disabled = true;

            await IncidentsAPI.postVerifyResult(currentIncident?.id || null, 'unsure', ClientId);

            
            
            // refresh list & detail so UI shows "VerificationTasked"
            await refreshList();
            await openIncident(incident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            unsureBtn.disabled = false;
          }
        };
      }

      if (planBtn) {
        //planBtn.disabled = !['Detection','Triage','VerificationTasked','ResponsePlanning','ResponseActive','ResponseComplete','Closed'].includes(incident.state);
        planBtn.disabled = !['VerificationResult'].includes(incident.state);
        
        planBtn.onclick = async () => {
          try {
            planBtn.disabled = true;
            const res = await IncidentsAPI.autoPlan(currentIncident?.id || null);
            alert(`Planned.\nArea=${fmt(res.area_km2)} km²  Dist=${res.dist_km == null ? 'n/a' : fmt(res.dist_km,2)+' km'}`);
            await refreshList();
            await openIncident(incident.id);
          } catch (e) {
            alert(e.message);
          } finally {
            planBtn.disabled = false;
          }
        };
      }

      if (responseBtn) {
        
        responseBtn.disabled = !['ResponseActive'].includes(incident.state);
        
        responseBtn.onclick = async () => {
          try {
            const choice = await pickNearestIdleClient(listEl, currentIncident?.id || null);
            if (!choice) {                     // <? guard for null
              alert('No suitable client found (idle or on-ground with GPS).');
              return;
            }
            const { cid, dist_km } = choice;   // <? use the returned object
            console.log('Chosen client:', cid, 'dist_km=', dist_km);

            responseBtn.disabled = true;

            const mode = 'centroid';
            const agl  = 10;
            const cruiseSpeed_km_per_sec = 0.0046;
            const tripSeconds = 1000 * dist_km / cruiseSpeed_km_per_sec;

            if (dist_km > 100) throw new Error('All available UAV are too far.');
            await deployForVerification(mode, cid, agl, tripSeconds); //0.0046 is the drone speed in km/second

            await refreshList();
            await openIncident(currentIncident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            responseBtn.disabled = false;
          }
        };
      }

      if (closeBtn) {
        // enable/disable depending on stage
        //Detection, Triage, VerificationTasked, VerificationResult, ResponsePlanning, ResponseActive, ResponseComplete
        closeBtn.disabled = !['ResponseActive'].includes(incident.state);

        closeBtn.onclick = async () => {
          try {

            
            
            // 

            await IncidentsAPI.completeResponse(currentIncident?.id || null);
            closeBtn.disabled = true;
            
            
            
            await refreshList();
            await openIncident(incident.id);
            
          } catch (e) {
            alert(e.message);
          } finally {
            closeBtn.disabled = false;
          }
        };
      }

      detailBox.style.display = 'block';
      setStatus('ready');
    } catch (e) {
      console.error(e);
      alert(e.message);

      setStatus('error');
    }
  }

  async function deployForVerification(mode, clientId, aglMeters, tripSeconds) {
    // mode: 'edge' | 'centroid'
    // clientId: the UAV connection id
    // aglMeters: number
    if (!currentIncident) throw new Error('Open an incident first.');
    if (!clientId) throw new Error('Pick a UAV/client.');
    if (!['edge','centroid'].includes(mode)) throw new Error('mode must be edge|centroid');

    // Build polygon for the UAV (lat,lon ring)
    const poly =  polygonForIncident(currentIncident, 150);
    if (!poly || poly.length < 4) throw new Error('No valid footprint/centroid to task.'); 

   

    // 1) Arm UAV
    //await sendCtrlAndWait(clientId, { cmd:'arm_takeoff', agl: aglMeters }, 30000, { finalOnly:true }); 
    await ctrlOps.armTakeoff(clientId, aglMeters);

    const s = await ctrlOps.getStatus(clientId);

    // 2) Mark incident as VerificationTasked + log a task server-side
    //    (include polygon so server can store what we actually asked the UAV to fly)
    await IncidentsAPI.taskVerify(currentIncident.id, {
      client_id: s.droneId,
      mode,
      agl_m: aglMeters,
      polygon: poly, // [[lat,lon], ...]
    });

    const tgt = incidentTargetLatLon(currentIncident);
    if (mode === 'centroid' && tgt) {
      startArrivalWatcher(currentIncident.id, s.droneId, { mode:'centroid', centroid: tgt, thresholdM: 60 });
    } else if (mode === 'edge' && Array.isArray(poly)) {
      startArrivalWatcher(currentIncident.id, s.droneId, { mode:'edge', polygon: poly, thresholdM: 60 });
    }


    // Send to UAV via  ctrl command
    //await sendCtrlAndWait(clientId, { cmd: 'goto_polygon', polygon: poly, agl: aglMeters, strategy: mode }, 600000, { finalOnly:true });
    await ctrlOps.gotoCentroid(clientId, poly, aglMeters, { finalOnly:false });
    //await sendCtrlAndWait(clientId, { cmd: 'goto_polygon', polygon: poly, agl: aglMeters, strategy: mode });

    // 4) Wait until arrived (telemetry) or fall back to status-poll completion
    try {
      await waitForArrival(currentIncident.id, s.droneId, { maxWaitMs: tripSeconds });
        } catch {
          // Fallback: poll status for a finished/idle state
          const t0 = Date.now();
          for (;;) {
            const s = await ctrlOps.getStatus(clientId);
            const finished = (s?.mission?.state === 'finished') ||
                            (s?.reachPolygon === true) ||
                            (s?.in_air === false);
            if (finished) break;
            if (Date.now() - t0 > 5*60_000) throw new Error('mission not finished (poll timeout)');
            await new Promise(r => setTimeout(r, 5_000));
          }
        }

    
    

    return { ok: true };
  }

  

  
  async function refreshRules() {
    try {
      const rows = await IncidentsAPI.listRules();
      renderRules(rows);
    } catch (e) {
      console.error(e);
      rulesBody.innerHTML = `<tr><td colspan="6" style="color:#b91c1c;">${e.message}</td></tr>`;
    }
  }

  function renderRules(rows) {
    rulesBody.innerHTML = '';
    if (!rows.length) {
      rulesBody.innerHTML = `<tr><td colspan="6" style="opacity:.7;">No rules</td></tr>`;
      return;
    }
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><b>${r.tier_name}</b></td>
        <td><b>${r.tier_group}</b></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="min_probability" type="number" step="0.01" value="${r.min_probability ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="max_probability" type="number" step="0.01" value="${r.max_probability ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="min_consequence" type="number" step="0.01" value="${r.min_consequence ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="max_consequence" type="number" step="0.01" value="${r.max_consequence ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="min_area_km2" type="number" step="0.01" value="${r.min_area_km2 ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="max_area_km2" type="number" step="0.01" value="${r.max_area_km2 ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="min_dist_shore_km" type="number" step="0.1" value="${r.min_dist_shore_km ?? ''}"></td>
        <td><input class="rule" style="width:80px" data-id="${r.id}" data-k="max_dist_shore_km" type="number" step="0.1" value="${r.max_dist_shore_km ?? ''}"></td>
        <td><button class="btn btn-sm" data-save="${r.id}">Save</button></td>
      `;
      rulesBody.appendChild(tr);
    }
  }

  function asList(val) {
  if (!val) return '';
  if (Array.isArray(val)) {
    return '<ul>' + val.map(v => `<li>${v}</li>`).join('') + '</ul>';
  }
  if (typeof val === 'object') {
    // pretty-print simple objects
    return '<ul>' + Object.entries(val).map(
      ([k,v]) => `<li><b>${k}</b>: ${typeof v === 'object' ? JSON.stringify(v) : v}</li>`
    ).join('') + '</ul>';
  }
  return String(val);
}

function renderResponsePlanBox(resp) {
  const box = document.getElementById('response-plan-body');
  if (!box) return;

  const tierLevel = resp.tier_level ?? null;
  const plan = resp.plan;

  if (!plan) {
    box.innerHTML = '<p class="muted">No response plan generated yet.</p>';
    return;
  }

  const pj =  plan.snapshot || {};

  // unpack typical keys from your JSON structure
  const spillEvent = plan.name || pj.spill_event || '—';
  const escalate   = pj.escalate || {};
  const objectives = pj.objectives || [];
  const optPayloads = pj.optional_payloads || pj.optionalPayloads || [];
  const resources  = pj.resources || {};
  const sitreps    = pj.sitreps || {};
  const techniques = pj.techniques || [];
  const uavTasks   = pj.uav_tasks || pj.uavTasks || [];

  box.classList.remove('plan-empty');
  box.innerHTML = `
    

    <div class="label">Escalate</div>
    <div class="value">
      ${escalate.deadline_min ? `deadline_min: ${escalate.deadline_min}<br>` : ''}
      ${escalate.ics ? `ics: "${escalate.ics}"<br>` : ''}
      ${escalate.notify ? asList(escalate.notify) : ''}
    </div>

    <div class="label">Objectives</div>
    <div class="value">${asList(objectives) || '—'}</div>

    <div class="label">Optional Payloads</div>
    <div class="value">${asList(optPayloads) || '—'}</div>

    <div class="label">Resources</div>
    <div class="value">
      ${resources.booms ? `<b>booms</b><br>${typeof resources.booms === 'object' ? JSON.stringify(resources.booms) : resources.booms}<br>` : ''}
      ${resources.skimmers ? `<b>skimmers</b> ${asList(resources.skimmers)}` : ''}
      ${resources.storage_m3 ? `<br><b>storage_m3:</b> ${resources.storage_m3}` : ''}
      ${resources.vessels ? `<br><b>vessels</b> ${asList(resources.vessels)}` : ''}
      ${(!resources.booms && !resources.skimmers && !resources.vessels) ? '—' : ''}
    </div>

    <div class="label">Sitreps</div>
    <div class="value">
      ${sitreps.initial_min ? `initial_min: ${sitreps.initial_min}<br>` : ''}
      ${sitreps.period_hours ? `period_hours: ${sitreps.period_hours}` : ''}
      ${(!sitreps.initial_min && !sitreps.period_hours) ? '—' : ''}
    </div>

    <div class="label">Techniques</div>
    <div class="value">
      ${asList(techniques) || '—'}
    </div>

    <div class="label">UAV Tasks</div>
    <div class="value">
      ${uavTasks.length
        ? '<ul>' + uavTasks.map(t => `
            <li>
              <b>${t.name || 'Task'}</b>
              ${t.pattern ? ` — ${t.pattern}` : ''}
              ${t.alt_agl_m ? ` (${t.alt_agl_m} m AGL)` : ''}
            </li>`).join('') + '</ul>'
        : '—'
      }
    </div>
  `;
}

  function readRuleRow(rowEl) {
    const read = k => {
      const el = rowEl.querySelector(`input[data-k="${k}"]`);
      if (!el) return null;
      const val = el.value.trim();
      return val === '' ? null : Number(val);
    };
    return {
      min_probability: read('min_probability'),
      max_probability: read('max_probability'),
      min_consequence: read('min_consequence'),
      max_consequence: read('max_consequence'),
      min_area_km2: read('min_area_km2'),
      max_area_km2: read('max_area_km2'),
      min_dist_shore_km: read('min_dist_shore_km'),
      max_dist_shore_km: read('max_dist_shore_km'),
    };
  }

  function attachEvents() {
    refreshBtn.addEventListener('click', refreshList);
    filterSel.addEventListener('change', refreshList);

    // delegate clicks for Open/Save buttons
    document.addEventListener('click', (e) => {
      const openBtn = e.target.closest('button[data-open]');
      if (openBtn) {
        openIncident(openBtn.dataset.open);
        return;
      }
      const saveBtn = e.target.closest('button[data-save]');
      if (saveBtn) {
        const id = saveBtn.dataset.save;
        const row = saveBtn.closest('tr');
        const payload = readRuleRow(row);
        saveBtn.disabled = true;
        IncidentsAPI.updateRule(id, payload)
          .then(() => alert('Saved'))
          .catch(err => alert(err.message))
          .finally(() => (saveBtn.disabled = false));
      }
    });
  }

  async function initOnce() {
    if (initialised) return;
    initialised = true;

    tableBody = document.querySelector('#inc-table tbody');
    filterSel = document.getElementById('inc-filter');
    refreshBtn = document.getElementById('inc-refresh');
    detailBox = document.getElementById('inc-detail');
    titleEl = document.getElementById('inc-title');
    stateEl = document.getElementById('inc-state');
    priorityEl = document.getElementById('inc-priority');
    verifEl = document.getElementById('inc-verif');
    areaEl = document.getElementById('inc-area');
    protEl = document.getElementById('inc-nearest-prot-area');
    desalEl = document.getElementById('inc-nearest-desalination');
    portEl = document.getElementById('inc-nearest-port');
    distEl = document.getElementById('inc-dist');
    tierLevelEl = document.getElementById('inc-tier');
    incidentDescriptionEl = document.getElementById('inc-description');

    // 
    tlEl = document.getElementById('inc-timeline');
    taskBtn = document.getElementById('btn-task');
    confirmBtn = document.getElementById('btn-confirm');
    refuteBtn = document.getElementById('btn-refute');
    unsureBtn = document.getElementById('btn-unsure');
    planBtn = document.getElementById('btn-plan');
    responseBtn = document.getElementById('btn-response');
    closeBtn = document.getElementById('btn-close');
    rulesBody = document.querySelector('#rules-table tbody');

    attachEvents();
    await refreshList();
    await refreshRules();
  }

  // public
  return {
    initOnce,
    openIncident,
  };
})();

// Hook into your tab system — runs when Incidents tab is shown
(function wireTabs() {
  const tabsNav = document.querySelector('nav.tabs');
  const panels = document.querySelectorAll('.tabpanel');

  function showTab(name) {
    // highlight the button
    document.querySelectorAll('nav .tab').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === name)
    );

    // show exactly one panel
    const wantId = `tab-${name}`;
    document.querySelectorAll('.tabpanel').forEach(p => {
      const active = p.id === wantId;
      p.classList.toggle('active', active);
      p.hidden = !active;
      p.style.display = active ? '' : 'none';  // <= hard hide
    });

    if (name === 'incidents') IncidentsUI.initOnce();
    if (name === 'map' && window.map) setTimeout(() => window.map.resize(), 0);
  }


  if (tabsNav) {
    tabsNav.addEventListener('click', (e) => {
      const btn = e.target.closest('.tab');
      if (!btn) return;
      const name = btn.dataset.tab;
      if (!name) return;
      e.preventDefault();
      showTab(name);
    });
  }

  async function autoRaiseIncidentFromFC(fc) {
    const choice = pickLargestPolygon(fc);
    if (!choice) return;

    const f = choice.feature;
    const ring = f.geometry.coordinates?.[0] || [];
    const cen  = ring.length ? centroidOfPolyRing(ring) : null;

    const title = `DL Spill ${new Date().toISOString().slice(0,16).replace('T',' ')} (${choice.area_km2.toFixed(2)} km²)`;

    // 1) create incident
    const incident = await IncidentsAPI.createFromDetection({
      title,
      est_area_sqkm: choice.area_km2,
      confidence: f.properties?.confidence ?? null,
      centroid: cen,
      geometry: f.geometry, // optional; backend may ignore
    });

    // 2) auto-triage (non-blocking if it fails)
    try {
      const triage = await IncidentsAPI.triage(incident.id);
      
      console.log('[triage]', triage); // {area_km2, dist_km, priority}
    } catch (e) {
      console.warn('triage failed:', e);
    }

    // switch tab and open it
    goToIncidentsTab();
    await IncidentsUI.initOnce();
    await IncidentsUI.openIncident(incident.id);
  }


  btnRunInfer.addEventListener('click', async () => {
  const aoiWkt = document.getElementById('aoi-wkt').value || null;
  const modelId = modelSelect.value;
  inferStatus.textContent = 'Inferring (latest scene)…';
  try {
    const r = await fetch('/admin/infer', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify({ modelId, aoiWkt })
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);

    // j will contain { fc, bbox, count }
    const fc = j.fc || {type:'FeatureCollection', features:[]};
    map.getSource('detect-src').setData(fc);
    if (j.bbox && Array.isArray(j.bbox) && j.bbox.length === 4) {
      fitToBbox(j.bbox, 60);
    } else {
      safeFitToData(fc);
    }
    appendDetectionLog(fc);
    const count = j.count || (fc.features?.length || 0);
    //inferStatus.textContent = `Inference OK: ${j.count || (fc.features?.length||0)} polygons`;
    inferStatus.textContent = `Inference OK: ${count} polygons`;

    //raise incident here if count is more than zero
    if (count > 0) {
      //console.log('[infer]',count)

      const meta = {
        model_name: modelId,
        model_version: 'ui',                 // or from your pipeline
        image_id: j.image_id || null,        // if your /admin/infer returns it
        captured_at: j.captured_at || null,  // if available; otherwise omit
        extra: { aoi_wkt: aoiWkt || null },  // anything useful
      };
      // Ingest (don’t break inference UI if it fails)
      try {
        const res = await DetectionsAPI.ingest(fc, meta);
        console.log('[detections] inserted', res.count);
        } catch (err) {
          console.warn('detections ingest failed:', err);
        }

      // Auto-incident (don’t break UI if it fails)
      try {
        await autoRaiseIncidentFromFC(fc);
      } catch (err) {
        console.warn('auto-raise failed:', err);
      }
    }

  } catch (e) {
    inferStatus.textContent = `Inference ERROR: ${e.message}`;
  }
});

  

  // Fallback: if the incidents panel is already active on load
  if (document.getElementById('tab-incidents')?.classList.contains('active')) {
    IncidentsUI.initOnce();
  }
  // Initial tab = whichever button is marked active in HTML, else 'map'
  const initial = document.querySelector('nav.tabs .tab.active')?.dataset.tab || 'map';
  showTab(initial);
})();

//END Incidents

// --- Boot sequence (safe order) ---
window.addEventListener('DOMContentLoaded', () => {
  try {
    //wireTabs();
    initMap().then(() => {
      setupAOIUI();
      setupScanUI();
      // WS after map exists
      // ws = new WebSocket(`wss://${location.host}/signal`);
      const scheme = (location.protocol === 'https:') ? 'wss' : 'ws';
      ws = new WebSocket(`${scheme}://${location.host}/signal`);

      ws.addEventListener('open', () => {
        const st = $('#status'); if (st) st.textContent = 'online';
        send({ type:'hello', role:'operator', clientId: 'operator-ui' });
        // connectHazard();
      });
      ws.addEventListener('message', onSignal);
    });
  } catch (e) {
    console.error('Fatal init error:', e);
  }
});



window.__probeIngest = async function () {
  const fc = {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { confidence: 0.9 },
      geometry: {
        type: 'Polygon',
        coordinates: [
          [[54.30,24.45],[54.31,24.45],[54.31,24.46],[54.30,24.46],[54.30,24.45]]
        ]
      }
    }]
  };

  await fetch('/api/detections/ingest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    fc: { type:'FeatureCollection', features: [] },
    model_name: 'probe',
    model_version: 'oneshot'
  })
}).then(async r => ({ status: r.status, text: await r.text() }));


  // Return both status + body to make debugging easy
  const txt = await res.text();
  try { return { status: res.status, json: JSON.parse(txt) }; }
  catch { return { status: res.status, text: txt }; }
};


