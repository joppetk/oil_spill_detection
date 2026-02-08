// server/scheduler.js
import * as topojson from 'topojson-server';

// ---------- tiny bbox + utils (no external deps) ----------
function computeBBox(geojson) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  function visitCoords(coords) {
    if (typeof coords[0] === 'number') {
      const [x, y] = coords;
      if (x < minX) minX = x; if (y < minY) minY = y;
      if (x > maxX) maxX = x; if (y > maxY) maxY = y;
    } else {
      for (const c of coords) visitCoords(c);
    }
  }
  function visitGeom(geom) {
    if (!geom) return;
    if (geom.coordinates) visitCoords(geom.coordinates);
    if (geom.geometries) geom.geometries.forEach(visitGeom);
  }

  if (geojson.type === 'FeatureCollection') geojson.features.forEach(f => visitGeom(f.geometry));
  else if (geojson.type === 'Feature') visitGeom(geojson.geometry);
  else visitGeom(geojson);

  return [minX, minY, maxX, maxY];
}

function bboxesIntersect(a, b) {
  const [minX, minY, maxX, maxY] = a;
  const [fminX, fminY, fmaxX, fmaxY] = b;
  return !(maxX < fminX || minX > fmaxX || maxY < fminY || minY > fmaxY);
}

function pointBBox({ lat, lon }, expandDeg) {
  return [lon - expandDeg, lat - expandDeg, lon + expandDeg, lat + expandDeg];
}

function makeFrame(topo, bbox, priority, ttlMs) {
  const createdAt = Date.now();
  const header = {
    kind: 'oil_slick',
    version: 1,
    createdAt,
    seq: Math.floor(Math.random() * 1e9),
    priority,
    ttlMs
  };
  return {
    header,
    topo,
    bbox,
    expiresAt: createdAt + ttlMs,
    minimal() { return { header, topo, bbox }; }
  };
}

// ---------- main scheduler ----------
export class Scheduler {
  constructor() {
    this.frames = [];
    this.subscribers = new Set();
    // run every 500 ms so you see updates fast
    setInterval(() => this.tick(), 500);
  }

  seed(geojson) {
    const topo = topojson.topology({ hazards: geojson });
    const bbox = computeBBox(geojson);
    const f = makeFrame(topo, bbox, /*priority*/ 5, /*ttlMs*/ 60000);
    this.frames.push(f);
    console.log(`[Scheduler] Seeded ${geojson.features?.length ?? 0} features, bbox=${bbox.join(',')}`);
  }

  ingest(geojson, priority = 7, ttlMs = 12000) {
    const topo = topojson.topology({ hazards: geojson });
    const bbox = computeBBox(geojson);
    const f = makeFrame(topo, bbox, priority, ttlMs);
    this.frames.push(f);
    console.log(`[Scheduler] Ingested frame seq=${f.header.seq} pri=${priority} ttl=${ttlMs} bbox=${bbox.join(',')}`);
  }

  subscribe(gps, onFrame) {
    const sub = { gps, onFrame, lastSent: 0 };
    this.subscribers.add(sub);
    console.log(`[Scheduler] Subscriber added. total=${this.subscribers.size} gps=${JSON.stringify(gps)}`);
    // immediate push so you see something right away
    this.pushBestFor(sub, /*force*/ true);
    return () => {
      this.subscribers.delete(sub);
      console.log(`[Scheduler] Subscriber removed. total=${this.subscribers.size}`);
    };
  }

  tick() {
    const now = Date.now();
    const before = this.frames.length;
    this.frames = this.frames.filter(f => f.expiresAt > now);
    const purged = before - this.frames.length;
    if (purged) console.log(`[Scheduler] Purged ${purged} expired frame(s)`);

    for (const sub of this.subscribers) this.pushBestFor(sub, false);
  }

  pushBestFor(sub, force = false) {
    if (!this.frames.length) return;
    // be generous so AOI intersects even if GPS is a bit off
    const aoi = pointBBox(sub.gps || { lat: 0, lon: 0 }, /*expandDeg*/ 1.2);
    const candidates = this.frames
      .filter(f => bboxesIntersect(aoi, f.bbox))
      .sort((a, b) => b.header.priority - a.header.priority || a.expiresAt - b.expiresAt);

    const best = candidates[0] || this.frames[0];
    const now = Date.now();
    if (force || now - sub.lastSent > 800) {
      try {
        sub.onFrame(best.minimal());
        sub.lastSent = now;
        // light log every 3s max
        if (force || (now % 3000) < 900) {
          console.log(`[Scheduler] Sent seq=${best.header.seq} pri=${best.header.priority} to a subscriber`);
        }
      } catch (e) {
        console.error('[Scheduler] send failed:', e.message);
      }
    }
  }

  stats() {
  return {
    frames: this.frames.length,
    subscribers: this.subscribers.size,
  };
}

}

// ---------- helper to synthesize a rectangle polygon ----------
export function rectPolygon({
  lon, lat, dxDeg, dyDeg,
  klass = 'probable',
  confidence = 0.6,
  drift = { dx: 0.01, dy: 0.004 }
}) {
  const coords = [
    [lon - dxDeg, lat - dyDeg],
    [lon + dxDeg, lat - dyDeg],
    [lon + dxDeg, lat + dyDeg],
    [lon - dxDeg, lat + dyDeg],
    [lon - dxDeg, lat - dyDeg]
  ];
  return {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { id: `sim-${Date.now()}`, class: klass, confidence, lon, lat, drift },
      geometry: { type: 'Polygon', coordinates: [coords] }
    }]
  };
}
