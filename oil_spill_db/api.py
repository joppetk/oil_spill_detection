# api.py
from flask import Flask, request, jsonify
from sqlalchemy import text,  bindparam
from sqlalchemy.dialects.postgresql import JSONB
from psycopg2.extras import Json as PGJson
from datetime import datetime


from db import ENGINE            # from your db.py
from ims_ops import auto_plan_response_area_dist
from sensitive_areas import query_sensitive_areas
import json
import uuid
import datetime as dt
import os

ORG_UUID = os.getenv("ORG_UUID", "e3150590-3e96-457b-ba41-fdbe3ed3798c")

app = Flask(__name__)

def _center_of_item(it):
    """Return [lon,lat] if available (Point geom or geometry_center), else None."""
    g = it.get("geometry")
    if it.get("geometry_center"):
        return it["geometry_center"]
    if g and g.get("type") == "Point":
        return g.get("coordinates")
    return None

def _detect_provider_and_source(it):
    """Best-effort provider/source_id tagging for dedupe."""
    # Priority: Ramsar, WPI, OSM
    if (it.get("designation") or "").lower() == "ramsar":
        return "Ramsar", it.get("wdpaid") or it.get("ramsar_id")
    # WPI ports usually have 'FUNCTION' or 'depth' in our mapped props
    if "function" in it or "depth" in it:
        return "WPI", None
    # desalination via OSM
    if it.get("type") == "desalination" or it.get("plant_type") == "desalination":
        return "OSM", None
    # protected areas via OSM
    if it.get("type") == "protected_area":
        return "OSM", None
    return "Unknown", None

def _upsert_site(conn, item, site_type):
    """Insert or find a site; returns site_id (uuid)."""
    provider, source_id = _detect_provider_and_source(item)
    name = item.get("name") or f"{site_type.title()}"

    center = _center_of_item(item)
    lon, lat = (None, None)
    if center and len(center) == 2:
        lon, lat = float(center[0]), float(center[1])

    geom_json = None
    if item.get("geometry"):
        # keep full geometry if returned (e.g., Ramsar polygon)
        geom_json = json.dumps(item["geometry"])

    # Try exact provider+source_id first if available
    if source_id:
        row = conn.execute(text("""
          SELECT id FROM sites WHERE provider=:prov AND source_id=:sid
        """), {"prov": provider, "sid": str(source_id)}).first()
        if row:
            return row[0]

    # Fallback: dedupe by provider+name+rounded center
    row = None
    if lon is not None and lat is not None:
        row = conn.execute(text("""
          SELECT id
          FROM sites
          WHERE provider=:prov AND site_type=:stype AND name=:name
            AND center IS NOT NULL
            AND ROUND(ST_X(center)::numeric, 5) = :lon5
            AND ROUND(ST_Y(center)::numeric, 5) = :lat5
          LIMIT 1
        """), {"prov": provider, "stype": site_type, "name": name,
               "lon5": round(lon, 5), "lat5": round(lat, 5)}).first()
        if row:
            return row[0]

    # Insert
    params = {
        "stype": site_type,
        "name": name,
        "prov": provider,
        "sid": str(source_id) if source_id else None,
        "props": json.dumps(item),
        "geom": geom_json,
        "lon": lon, "lat": lat
    }
    site_id = conn.execute(text("""
      INSERT INTO sites (site_type, name, provider, source_id, props, geom, center)
      VALUES (
        :stype, :name, :prov, :sid, :props,
        CASE WHEN :geom IS NULL THEN NULL
             ELSE ST_SetSRID(ST_GeomFromGeoJSON(:geom)::geometry, 4326) END,
        CASE WHEN :lon IS NULL OR :lat IS NULL THEN NULL
             ELSE ST_SetSRID(ST_MakePoint(:lon,:lat),4326) END
      )
      RETURNING id
    """), params).scalar_one()
    return site_id

def _link_incident_site(conn, incident_id, site_id, relation_type, distance_m, snapshot_dict):
    conn.execute(text("""
      INSERT INTO incident_sites (incident_id, site_id, relation_type, distance_m, snapshot)
      VALUES (:iid, :sid, :rel, :dist, :snap)
      
      ON CONFLICT (incident_id, site_id, relation_type) DO UPDATE
        SET distance_m = EXCLUDED.distance_m,
            snapshot   = EXCLUDED.snapshot
    """), {"iid": incident_id, "sid": site_id, "rel": relation_type,
           "dist": distance_m, "snap": json.dumps(snapshot_dict or {})})

def rows_to_dicts(rows):
    # rows from .mappings().all() -> list of RowMapping; convert to plain dicts
    return [dict(r) for r in rows]

def json_or_400():
    try:
        return request.get_json(force=True) or {}
    except Exception:
        return None
    
# helper: lat/lon ring -> GeoJSON Polygon (lon,lat order)
def latlon_ring_to_geojson_polygon(latlon_ring):
    """latlon_ring = [[lat,lon], ...]; returns a GeoJSON dict Polygon (lon,lat)."""
    lonlat = [[lon, lat] for (lat, lon) in latlon_ring]
    # ensure closed ring
    if lonlat[0] != lonlat[-1]:
        lonlat.append(lonlat[0])
    return {"type": "Polygon", "coordinates": [lonlat]}




def iter_polygons_from_fc(fc):
    """Yield (geometry_json, confidence, extra) for Polygon & MultiPolygon."""
    feats = (fc or {}).get("features", []) or []
    for f in feats:
        g = (f or {}).get("geometry") or {}
        props = (f or {}).get("properties") or {}
        conf = props.get("confidence")
        if g.get("type") == "Polygon":
            yield g, conf, props
        elif g.get("type") == "MultiPolygon":
            # explode into individual Polygon parts
            for coords in (g.get("coordinates") or []):
                poly = {"type": "Polygon", "coordinates": coords}
                yield poly, conf, props
        # ignore other geometry types silently

def geojson_polygon_to_latlon_ring(gj):
    """GeoJSON Polygon -> [[lat,lon], ...] (outer ring)."""
    if not gj or gj.get("type") != "Polygon":
        return None
    ring_lonlat = gj["coordinates"][0]  # [[lon,lat],...]
    return [[lat, lon] for lon, lat in ring_lonlat]

def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    
@app.post("/detections/ingest")
def detections_ingest():
    """
    Body JSON:
    {
      "model_name": "unet_b48_v1",
      "model_version": "prod-2025-09",
      "image_id": "S1A_....SAFE",
      "captured_at": "2025-10-09T00:00:00Z",     # optional
      "extra": {...},                             # optional, stored as jsonb
      "fc": { FeatureCollection of polygons }     # required
    }
    """
    payload = request.get_json(force=True) or {}
    fc          = payload.get("fc")
    model_name  = payload.get("model_name") or "unknown"
    model_ver   = payload.get("model_version") or "unknown"
    image_id    = payload.get("image_id") or "unknown"
    captured_at = parse_ts(payload.get("captured_at"))  or datetime.utcnow().replace(tzinfo=None)
    extra_base  = payload.get("extra") or {}

    meta        = payload.get("meta")

    if not fc or fc.get("type") != "FeatureCollection":
        return jsonify({"ok": False, "error": "fc must be a FeatureCollection"}), 400

    inserted = []
    with ENGINE.begin() as conn:
        org_id = ORG_UUID


        for geom, conf, props in iter_polygons_from_fc(fc):
            geom_json = json.dumps(geom)  # pass to ST_GeomFromGeoJSON
            # merge base extra + feature props (props win)
            extra = dict(extra_base)
            extra.update(props or {})
            merged_extra = {**extra_base, **(props or {})}

            row = conn.execute(text("""
                INSERT INTO detections (
                    org_id, model_name, model_version,
                    image_id, captured_at, confidence,
                    polygon, extra
                )
                VALUES (
                    :org_id, :model_name, :model_version,
                    :image_id, :captured_at, :confidence,
                    ST_SetSRID(ST_GeomFromGeoJSON(:geom)::geometry, 4326),
                    -- geodesic area in km²:
                    
                    
                    :extra
                )
                RETURNING id,
                          to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                          area_sqkm
            """), {
                "org_id": org_id,
                "model_name": model_name,
                "model_version": model_ver,
                "image_id": image_id,
                "captured_at": captured_at,
                "confidence": conf,
                "geom": geom_json,
                "extra": PGJson(merged_extra),
            }).mappings().first()
            inserted.append(dict(row))

    return jsonify({"ok": True, "inserted": inserted, "count": len(inserted)})

    





@app.get("/detections")
def list_detections():
    limit = min(int(request.args.get("limit", "50")), 500)
    with ENGINE.begin() as conn:
        rows = conn.execute(text("""
            SELECT
              id, org_id, model_name, model_version, image_id,
              to_char(captured_at, 'YYYY-MM-DD HH24:MI:SS') AS captured_at,
              confidence, area_sqkm,
              ST_AsGeoJSON(polygon) AS polygon,
              ST_AsGeoJSON(bbox)    AS bbox,
              to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
            FROM detections
            ORDER BY created_at DESC
            LIMIT :lim
        """), {"lim": limit}).mappings().all()
    # jsonify can handle plain dicts; convert RowMapping -> dicts
    return jsonify({"ok": True, "items": rows_to_dicts(rows)})



@app.post("/incidents/from-detection-old")
def create_incident_from_detection_old():
    """
    Body:
    {
      "title": "DL Spill ...",
      "est_area_sqkm": 3.42,
      "confidence": 0.87,
      "centroid": {"lat": 24.47, "lon": 54.37},     # optional if geometry present
      "geometry": {...}                              # GeoJSON Polygon/MultiPolygon (preferred)
    }
    """
    data = request.get_json(force=True) or {}
    title = data.get("title") or "DL Spill"
    area_km2 = float(data.get("est_area_sqkm") or 0.0)
    geometry = data.get("geometry")
    centroid = data.get("centroid")

    # priority 1..9 (bigger area -> higher priority ~ smaller number)
    priority = max(1, 10 - int((area_km2 if area_km2 > 0 else 0) + 0.999))
    state = "Triage"

    # choose geometry/centroid expressions
    params = {
        "org_id": ORG_UUID,
        "title": title,
        "state": state,
        "priority": priority,
        "area": area_km2,
    }

    if geometry:
        params["geom"] = json.dumps(geometry)
        geom_expr = "ST_SetSRID(ST_GeomFromGeoJSON(:geom)::geometry, 4326)"
        centroid_expr = f"ST_Centroid({geom_expr})"
        footprint_expr = geom_expr
    elif centroid and isinstance(centroid.get("lat"), (int, float)) and isinstance(centroid.get("lon"), (int, float)):
        params["lat"] = float(centroid["lat"])
        params["lon"] = float(centroid["lon"])
        centroid_expr = "ST_SetSRID(ST_Point(:lon, :lat), 4326)"
        footprint_expr = "NULL"
    else:
        return jsonify({"ok": False, "error": "geometry or centroid required"}), 400

    sql = text(f"""
        INSERT INTO incidents (
            org_id, title, state, priority, est_area_sqkm, detection_source,
            centroid, footprint
        )
        VALUES (
            :org_id, :title, :state, :priority, :area, 'model',
            {centroid_expr}, {footprint_expr}
        )
        RETURNING id, title, state, priority, est_area_sqkm,
                  to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
    """)

    with ENGINE.begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        conn.execute(
            text("""
              INSERT INTO incident_events (incident_id, event_type, from_state, to_state)
              VALUES (:iid, 'Detection', NULL, :st)
            """),
            {"iid": row["id"], "st": state, "note": f"auto from detection; area≈{area_km2:.2f} km²"},
        )

    return jsonify({"ok": True, "incident": dict(row)})

@app.post("/incidents/from-detection-1")
def create_incident_from_detection_1():
    """
    Body:
    {
      "title": "DL Spill ...",
      "est_area_sqkm": 3.42,
      "confidence": 0.87,                         # optional
      "centroid": {"lat": 24.47, "lon": 54.37},   # optional if geometry present
      "geometry": {...}                            # GeoJSON Polygon/MultiPolygon (preferred)
    }
    """
    data = request.get_json(force=True) or {}
    title     = data.get("title") or "DL Spill"
    area_km2  = float(data.get("est_area_sqkm") or 0.0)
    geometry  = data.get("geometry")      # GeoJSON (Polygon/MultiPolygon) preferred
    centroid  = data.get("centroid")      # {lat, lon} optional
    source    = "model"                   # tag how it was created

    # priority 1..9 (bigger area -> higher priority ~ smaller number)
    priority = max(1, 10 - int((area_km2 if area_km2 > 0 else 0) + 0.999))
    # go straight into triage after creation
    state = "Triage"

    # Build SQL fragments for centroid/footprint (never pass :geom::geography directly)
    params = {
        "org_id": ORG_UUID,
        "title": title,
        "state": state,
        "priority": priority,
        "area": area_km2,
        "source": source,
    }

    if geometry:
        params["fp"] = json.dumps(geometry)
        footprint_expr = "ST_SetSRID(ST_GeomFromGeoJSON(:fp)::geometry, 4326)"
        centroid_expr  = f"ST_Centroid({footprint_expr})"
    elif centroid and isinstance(centroid.get("lat"), (int, float)) and isinstance(centroid.get("lon"), (int, float)):
        params["lat"] = float(centroid["lat"])
        params["lon"] = float(centroid["lon"])
        footprint_expr = "NULL"
        # NOTE: ST_MakePoint(lon,lat)
        centroid_expr  = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
    else:
        return jsonify({"ok": False, "error": "geometry or centroid required"}), 400

    with ENGINE.begin() as conn:
        # 1) Insert incident with centroid/footprint
        ins_row = conn.execute(text(f"""
            INSERT INTO incidents (
                org_id, title, state, priority, est_area_sqkm, detection_source,
                centroid, footprint
            )
            VALUES (
                :org_id, :title, :state, :priority, :area, :source,
                {centroid_expr}, {footprint_expr}
            )
            RETURNING id
        """), params).mappings().first()

        iid = ins_row["id"]

        # 2) Compute & store shoreline distance (km) right away
        conn.execute(text("""
            WITH coast AS (
              SELECT ST_Collect(geom)::geography AS g FROM shorelines
            )
            UPDATE incidents i
               SET dist_shore_km = CASE
                 WHEN (SELECT g FROM coast) IS NULL THEN NULL
                 WHEN i.footprint IS NOT NULL THEN ST_Distance(i.footprint::geography, (SELECT g FROM coast))/1000.0
                 WHEN i.centroid  IS NOT NULL THEN ST_Distance(i.centroid::geography,  (SELECT g FROM coast))/1000.0
                 ELSE NULL
               END,
                   updated_at = NOW()
             WHERE i.id = :iid
        """), {"iid": iid})

        # 3) Timeline entry
        conn.execute(text("""
          INSERT INTO incident_events (incident_id, event_type, from_state, to_state)
          VALUES (:iid, 'Detection', NULL, :st)
        """), {"iid": iid, "st": state, "note": f"auto from detection; area≈{area_km2:.2f} km²"})

        # 4) Re-read the incident for response
        row = conn.execute(text("""
            SELECT id, title, state, priority, est_area_sqkm, dist_shore_km,
                   to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
            FROM incidents WHERE id=:iid
        """), {"iid": iid}).mappings().first()

    return jsonify({"ok": True, "incident": dict(row)})



@app.post("/incidents/from-detection")
def create_incident_from_detection():
    """
    Body:
    {
      "title": "DL Spill ...",
      "est_area_sqkm": 3.42,
      "confidence": 0.87,
      "centroid": {"lat": 24.47, "lon": 54.37},     # optional if geometry present
      "geometry": {...}                              # GeoJSON Polygon/MultiPolygon (preferred)
    }
    """
    data = request.get_json(force=True) or {}
    title     = data.get("title") or "DL Spill"
    area_km2  = float(data.get("est_area_sqkm") or 0.0)
    geometry  = data.get("geometry")
    centroid  = data.get("centroid")
    source    = "model"

    priority = max(1, 10 - int((area_km2 if area_km2 > 0 else 0) + 0.999))
    state = "Triage"

    params = {
        "org_id": ORG_UUID,
        "title": title,
        "state": state,
        "priority": priority,
        "area": area_km2,
        "source": source,
    }

    if geometry:
        params["fp"] = json.dumps(geometry)
        footprint_expr = "ST_SetSRID(ST_GeomFromGeoJSON(:fp)::geometry, 4326)"
        centroid_expr  = f"ST_Centroid({footprint_expr})"
    elif centroid and isinstance(centroid.get("lat"), (int, float)) and isinstance(centroid.get("lon"), (int, float)):
        params["lat"] = float(centroid["lat"])
        params["lon"] = float(centroid["lon"])
        footprint_expr = "NULL"
        centroid_expr  = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
    else:
        return jsonify({"ok": False, "error": "geometry or centroid required"}), 400

    try:
        with ENGINE.begin() as conn:
            # 1) Insert incident (centroid + optional footprint)
            ins_row = conn.execute(text(f"""
                INSERT INTO incidents (
                    org_id, title, state, priority, est_area_sqkm, detection_source,
                    centroid, footprint
                )
                VALUES (
                    :org_id, :title, :state, :priority, :area, :source,
                    {centroid_expr}, {footprint_expr}
                )
                RETURNING id
            """), params).mappings().first()
            iid = ins_row["id"]

            # 2) Compute & store shoreline distance (km)
            conn.execute(text("""
                WITH coast AS (
                  SELECT ST_Collect(geom)::geography AS g FROM shorelines
                )
                UPDATE incidents i
                   SET dist_shore_km = CASE
                     WHEN (SELECT g FROM coast) IS NULL THEN NULL
                     WHEN i.footprint IS NOT NULL THEN ST_Distance(i.footprint::geography, (SELECT g FROM coast))/1000.0
                     WHEN i.centroid  IS NOT NULL THEN ST_Distance(i.centroid::geography,  (SELECT g FROM coast))/1000.0
                     ELSE NULL
                   END,
                       updated_at = NOW()
                 WHERE i.id = :iid
            """), {"iid": iid})

            # 3) Read numeric centroid back for API query
            latlon = conn.execute(text("""
                SELECT ST_Y(centroid) AS lat, ST_X(centroid) AS lon
                FROM incidents WHERE id = :iid
            """), {"iid": iid}).mappings().first()
            lat, lon = float(latlon["lat"]), float(latlon["lon"])

            print("lat = ", lat)
            print("lon = ", lon)

            # 4) Call external APIs ONCE and cache results
            try:
                sa_data = query_sensitive_areas(lat=lat, lon=lon, radius_km=100.0)
            except Exception as e:
                sa_data = {"nearest": {"port": None, "protected_area": None, "desalination": None}}

            print("sa_data = ", sa_data)

            # 5) Upsert nearest sites + link + denormalized columns
            nearest = (sa_data or {}).get("nearest", {}) or {}

            print("nearest port= ", nearest.get("port"))
            print("nearest prot= ", nearest.get("protected_area"))
            print("nearest desal= ", nearest.get("desalination"))

            # Port
            if nearest.get("port"):
                sid = _upsert_site(conn, nearest["port"], "port")
                _link_incident_site(conn, iid, sid, "nearest", nearest["port"].get("distance_m"), nearest["port"])
                conn.execute(text("""
                    UPDATE incidents
                       SET nearest_port_id = :sid,
                           nearest_port_distance_m = :dist
                     WHERE id = :iid
                """), {"sid": sid, "dist": nearest["port"].get("distance_m"), "iid": iid})

            # Protected area
            if nearest.get("protected_area"):
                sid = _upsert_site(conn, nearest["protected_area"], "protected_area")
                _link_incident_site(conn, iid, sid, "nearest", nearest["protected_area"].get("distance_m"), nearest["protected_area"])
                
                
                
                conn.execute(text("""
                    UPDATE incidents
                       SET nearest_protected_id = :sid,
                           nearest_protected_distance_m = :dist
                     WHERE id = :iid
                """), {"sid": sid, "dist": nearest["protected_area"].get("distance_m"), "iid": iid})

            # Desalination
            if nearest.get("desalination"):
                sid = _upsert_site(conn, nearest["desalination"], "desalination")
                _link_incident_site(conn, iid, sid, "nearest", nearest["desalination"].get("distance_m"), nearest["desalination"])
                conn.execute(text("""
                    UPDATE incidents
                       SET nearest_desal_id = :sid,
                           nearest_desal_distance_m = :dist
                     WHERE id = :iid
                """), {"sid": sid, "dist": nearest["desalination"].get("distance_m"), "iid": iid})

            # 6) Timeline entry
            conn.execute(text("""
              INSERT INTO incident_events (incident_id, event_type, from_state, to_state)
              VALUES (:iid, 'Detection', NULL, :st)
            """), {"iid": iid, "st": state})

            # 7) Re-read the incident for response
            row = conn.execute(text("""
                SELECT id, title, state, priority, est_area_sqkm, dist_shore_km,
                       nearest_port_distance_m, nearest_protected_distance_m, nearest_desal_distance_m,
                       to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
                FROM incidents WHERE id=:iid
            """), {"iid": iid}).mappings().first()

        return jsonify({"ok": True, "incident": dict(row)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/incidents")
def list_incidents():
    raw_state = request.args.get("state")
    limit = min(int(request.args.get("limit", "50")), 200)
    state = (raw_state or "").strip()
    params = {"lim": limit}

    if state == "" or state.lower() == "all":
        where = "TRUE"
    elif state == "open":
        where = "state NOT IN ('ResponseComplete','Closed')"
    elif state == "closed":
        where = "state IN ('ResponseComplete','Closed')"
    else:
        where = "state = :st"
        params["st"] = state

    sql = text(f"""
        SELECT id, title, state, priority, est_area_sqkm, dist_shore_km,
               to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
        FROM incidents
        WHERE {where}
        ORDER BY priority ASC, updated_at DESC
        LIMIT :lim
    """)

    with ENGINE.begin() as conn:
        rows = conn.execute(sql, params).mappings().all()

    # 🔧 convert to plain dicts here:
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})


@app.get("/incidents/<uuid:iid>")
def get_incident(iid: uuid.UUID):
    iid_str = str(iid)
    with ENGINE.begin() as conn:
        inc = conn.execute(text("""
          SELECT id, title, state, priority, verification,
                 ST_AsGeoJSON(centroid)  AS centroid,
                 ST_AsGeoJSON(footprint) AS footprint,
                 est_area_sqkm, dist_shore_km, detection_source,
                 to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                 to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
          FROM incidents
          WHERE id = :id
        """), {"id": iid_str}).mappings().one_or_none()

        if not inc:
            return jsonify({"ok": False, "error": "Not found"}), 404

        ev = conn.execute(text("""
          SELECT to_char(at,'YYYY-MM-DD HH24:MI:SS') as at,
                 event_type, from_state, to_state, details
          FROM incident_events
          WHERE incident_id = :id
          ORDER BY at ASC
        """), {"id": iid_str}).mappings().all()

    inc = dict(inc)
    # decode GeoJSON strings if present
    for k in ("centroid", "footprint"):
        v = inc.get(k)
        if isinstance(v, str):
            try:
                inc[k] = json.loads(v)
            except json.JSONDecodeError:
                pass

    return jsonify({"ok": True, "incident": inc, "timeline": rows_to_dicts(ev)})

# --- TRIAGE: compute area/dist, set priority, move to Triage ---
@app.post("/incidents/<iid>/triage")
def api_triage(iid):
    with ENGINE.begin() as conn:
        # pull what we need once
        inc = conn.execute(text("""
          SELECT org_id, est_area_sqkm,
                 footprint, centroid
          FROM incidents WHERE id=:iid
        """), {"iid": iid}).mappings().one_or_none()
        if not inc:
            return jsonify({"ok": False, "error": "incident not found"}), 404

        # area_km2 (prefer footprint)
        area_km2 = conn.execute(text("""
            SELECT COALESCE(
              CASE WHEN i.footprint IS NOT NULL
                   THEN ST_Area(i.footprint::geography)/1e6
                   ELSE NULL END,
              i.est_area_sqkm
            )
            FROM incidents i WHERE i.id=:iid
        """), {"iid": iid}).scalar()

        # distance to shoreline (if shoreline loaded)
        dist_km = conn.execute(text("""
            WITH coast AS (
              SELECT CASE WHEN COUNT(*)=0 THEN NULL
                          ELSE ST_Collect(geom)::geography END AS g
              FROM shorelines
            )
            SELECT CASE
              WHEN (SELECT g FROM coast) IS NULL THEN NULL
              WHEN i.footprint IS NOT NULL
                   THEN ST_Distance(
                      ST_Centroid(i.footprint)::geography,
                      (SELECT g FROM coast)
                   )/1000.0
              WHEN i.centroid IS NOT NULL
                   THEN ST_Distance(
                      i.centroid::geography,
                      (SELECT g FROM coast)
                   )/1000.0
              ELSE NULL END
            FROM incidents i WHERE i.id=:iid
        """), {"iid": iid}).scalar()

        # simple priority rule
        def calc_priority(a, d):
            a = a or 0.0
            d = d if d is not None else 9999
            if a >= 10 or d < 5:  return 2
            if a >= 3  or d < 20: return 4
            if a >= 1  or d < 50: return 6
            return 8

        priority = calc_priority(area_km2, dist_km)

        # persist
        conn.execute(text("""
          UPDATE incidents
             SET state='Triage',
                 priority=:p,
                 dist_shore_km=:d,
                 updated_at=NOW()
           WHERE id=:iid
        """), {"iid": iid, "p": priority, "d": dist_km})

        conn.execute(text("""
          INSERT INTO incident_events(incident_id, event_type, from_state, to_state, details)
          VALUES (:iid, 'Triage', NULL, 'Triage',
                  jsonb_build_object('area_km2', :a, 'dist_km', :d, 'priority', :p))
        """), {"iid": iid, "a": area_km2, "d": dist_km, "p": priority})

    return jsonify({"ok": True, "area_km2": area_km2, "dist_km": dist_km, "priority": priority})

# --- TASK VERIFICATION: set method/assignee/SLA, move to VerificationTasked ---
@app.post("/incidents/<iid>/task-verify")
def task_verify(iid):
    """
    Body JSON:
      {
        "client_id": "pi-123",
        "mode": "edge" | "centroid",
        "agl_m": 30,
        "polygon": [[lat,lon], ...]   # optional; if omitted, derive from incident
      }
    Effect:
      - Update incidents.state = 'VerificationTasked'
      - Insert row into verification_tasks with planned polygon
    """
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id")
    mode = (data.get("mode") or "centroid").lower()
    agl_m = data.get("agl_m", 30)
    poly_latlon = data.get("polygon")  # optional

    if not client_id:
        return jsonify({"ok": False, "error": "client_id required"}), 400
    if mode not in ("edge", "centroid"):
        return jsonify({"ok": False, "error": "mode must be 'edge' or 'centroid'"}), 400

    with ENGINE.begin() as conn:
        # fetch incident centroid/footprint + org
        inc = conn.execute(text("""
          SELECT org_id,
                 ST_AsGeoJSON(centroid)  AS centroid,
                 ST_AsGeoJSON(footprint) AS footprint
          FROM incidents WHERE id=:iid
        """), {"iid": iid}).mappings().one_or_none()

        if not inc:
            return jsonify({"ok": False, "error": "incident not found"}), 404

        # Build GeoJSON polygon to store:
        if poly_latlon and isinstance(poly_latlon, list) and len(poly_latlon) >= 4:
            poly_gj = latlon_ring_to_geojson_polygon(poly_latlon)
        else:
            # derive from incident footprint -> polygon
            if inc["footprint"]:
                poly_gj = json.loads(inc["footprint"])
                if poly_gj.get("type") != "Polygon":
                    return jsonify({"ok": False, "error": "incident footprint not a Polygon"}), 400
            else:
                # fallback: small square around centroid
                if not inc["centroid"]:
                    return jsonify({"ok": False, "error": "no geometry to task (no footprint or centroid)"}), 400
                c = json.loads(inc["centroid"])["coordinates"]  # [lon,lat]
                lon, lat = float(c[0]), float(c[1])
                # small square ~150 m
                half = 150.0 / 111320.0
                dLon = half / max(1e-6, (111320.0 * max(0.1, abs(__import__("math").cos(lat * __import__("math").pi/180)))))
                ring = [
                    [lat - half, lon - dLon],
                    [lat - half, lon + dLon],
                    [lat + half, lon + dLon],
                    [lat + half, lon - dLon],
                    [lat - half, lon - dLon],
                ]
                poly_gj = latlon_ring_to_geojson_polygon(ring)

        # store the planned polygon as geometry (4326)
        poly_json = json.dumps(poly_gj)

        # insert verification task
        task = conn.execute(text("""
          INSERT INTO verification_tasks (
              incident_id, drone_id, pattern, agl_m, status, polygon
          )
          VALUES (
              :iid, :cid, :strategy, :agl, 'tasked',
              ST_SetSRID(ST_GeomFromGeoJSON(:poly)::geometry, 4326)
          )
          RETURNING id, to_char(created_at,'YYYY-MM-DD HH24:MI:SS') AS created_at
        """), {"iid": iid, "cid": client_id, "strategy": mode, "agl": agl_m, "poly": poly_json}).mappings().first()

        # set incident state + add event
        conn.execute(text("UPDATE incidents SET state='VerificationTasked', updated_at=NOW() WHERE id=:iid"),
                     {"iid": iid})
        conn.execute(text("""
          INSERT INTO incident_events (incident_id, event_type, from_state, to_state, details)
          VALUES (:iid, 'VerificationTasked', 'Triage', 'VerificationTasked',
                  json_build_object('client_id', :cid, 'mode', :mode, 'agl_m', :agl))
        """), {"iid": iid, "cid": client_id, "mode": mode, "agl": agl_m})

    return jsonify({
        "ok": True,
        "task": {"id": task["id"], "created_at": task["created_at"]},
        "stored_polygon": poly_gj,   # echo what we saved
        "mode": mode,
        "agl_m": agl_m
    })

# --- UAV ARRIVED: move to VerificationInProgress, log event, touch task ---
@app.post("/incidents/<iid>/arrived")
def arrived(iid):
    """
    Body:
      { "client_id": "pi-123", "dist_m": 42.0 }   # dist_m optional, but useful to log
    Effect:
      - Update incidents.state = 'VerificationInProgress'
      - Mark latest verification_tasks for this incident as 'in_progress' + arrived_at
      - Add incident_events row
    """
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id")
    dist_m    = float(data.get("dist_m", 0.0))

    with ENGINE.begin() as conn:
        # get previous state (for timeline)
        prev = conn.execute(text("SELECT state FROM incidents WHERE id=:iid"),
                            {"iid": iid}).scalar()

        # mark latest verification task (if any) as in_progress + arrival info
        conn.execute(text("""
            UPDATE verification_tasks
               SET status = 'in_progress',
                   arrived_at = NOW(),
                   arrived_distance_m = :dist
             WHERE incident_id = :iid
               AND (drone_id = :cid OR :cid IS NULL)
               AND id = (
                   SELECT id FROM verification_tasks
                    WHERE incident_id=:iid
                    ORDER BY id DESC
                    LIMIT 1
               )
        """), {"iid": iid, "cid": client_id, "dist": dist_m})

        # bump state
        #conn.execute(text("""
        #    UPDATE incidents
        #       SET state='VerificationInProgress',
        #           updated_at=NOW()
        #     WHERE id=:iid
        #"""), {"iid": iid})

        # log event
        conn.execute(text("""
          INSERT INTO incident_events (incident_id, event_type, details)
          VALUES (:iid, 'UavArrived', 
                  json_build_object('client_id', :cid, 'dist_m', :dist))
        """), {"iid": iid, "from_state": prev, "cid": client_id, "dist": dist_m})

    return jsonify({"ok": True})


@app.get("/incidents/<iid>/get_verification_task")
def get_verification_task(iid):
    """
    Optional query param:
      ?status=active   -> only 'tasked' or 'in_progress'
      (omit)           -> latest task regardless of status
    """
    status_filter = request.args.get("status", "").lower()

    with ENGINE.begin() as conn:
        base_sql = """
          SELECT id, incident_id, drone_id, pattern, agl_m, status,
                 ST_AsGeoJSON(polygon) AS polygon,
                 to_char(created_at,'YYYY-MM-DD HH24:MI:SS') AS created_at
          FROM verification_tasks
          WHERE incident_id = :iid
        """
        if status_filter == "active":
            base_sql += " AND status IN ('tasked','in_progress') "
        base_sql += " ORDER BY created_at DESC LIMIT 1"

        row = conn.execute(text(base_sql), {"iid": iid}).mappings().first()

    if not row:
        return jsonify({"ok": False, "error": "no verification task","row":base_sql}), 404

    poly_gj = json.loads(row["polygon"]) if row["polygon"] else None
    ring_latlon = geojson_polygon_to_latlon_ring(poly_gj)

    return jsonify({
        "ok": True,
        "task": {
            "id": row["id"],
            "incident_id": row["incident_id"],
            "drone_id": row["drone_id"],       # <- what you need to match
            "pattern": row["pattern"],
            "agl_m": row["agl_m"],
            "status": row["status"],
            "created_at": row["created_at"]
        },
        "polygon_geojson": poly_gj,
        "polygon_latlon": ring_latlon
    })


# --- VERIFICATION RESULT: confirmed/refuted/unsure, close or advance state ---
@app.post("/incidents/<iid>/verify-result")
def verify_result(iid):
    """
    Body:
      {
        "client_id": "pi-123",
        "outcome": "confirmed" | "refuted" | "unsure",
        "notes": "free text",
        "evidence": { "photos": [...], "video": [...] }  # optional
      }
    Effect:
      - Insert into verification_results (if you have this table), else skip
      - Update latest verification_tasks -> completed + result
      - Update incidents.state accordingly (+ convenience columns if present)
      - Add incident_events row
    """
    data = request.get_json(force=True) or {}
    client_id = data.get("clientId")
    outcome   = (data.get("outcome") or "").lower()
    notes     = data.get("notes")
    evidence  = data.get("evidence") or {}

    if outcome not in ("confirmed", "refuted", "unsure"):
        return jsonify({"ok": False, "error": "outcome must be confirmed|refuted|unsure"}), 400

    with ENGINE.begin() as conn:
        prev = conn.execute(text("SELECT state FROM incidents WHERE id=:iid"),
                            {"iid": iid}).scalar()

        # optional: write a row into verification_results if your schema has it
        vr_id = None
        
        row = conn.execute(text("""
                INSERT INTO verification_results
                    (incident_id, client_id, outcome, notes, evidence)
                VALUES
                    (:iid, :cid, :outcome, :notes, CAST(:evidence AS jsonb))
                RETURNING id
            """),
            {
                "iid": iid,
                "cid": client_id,                 # can be None if column allows NULL
                "outcome": outcome,               # 'confirmed' | 'refuted' | 'unsure'
                "notes": notes or None,
                "evidence": json.dumps(evidence or {})  # <-- provide this param
            }
        ).mappings().first()
        if row: vr_id = row["id"]
       

        # close the latest verification task with the result
        conn.execute(text("""
            UPDATE verification_tasks
               SET status='completed',
                   completed_at=NOW(),
                   result=:result
             WHERE incident_id=:iid
               AND id = (
                   SELECT id FROM verification_tasks
                    WHERE incident_id=:iid
                    ORDER BY id DESC
                    LIMIT 1
               )
        """), {"iid": iid, "result": outcome})

        # compute new state
        new_state = {
            "confirmed": "VerifiedConfirmed",
            "refuted":   "VerifiedRefuted",
            "unsure":    "VerifiedUnsure"
        }[outcome]

        # update incident
        # (columns 'verification','verification_at','verification_by' are optional)
        conn.execute(text("""
            UPDATE incidents
               SET state            = 'VerificationResult',
                   verification     = :outcome,
                   verification_at  = NOW(),
                   verification_by  = COALESCE(:by, verification_by),
                   updated_at       = NOW()
             WHERE id = :iid
        """), {
            "state": new_state,      # <-- use computed new_state
            "outcome": outcome,
            "by": "operator",
            "iid": iid,
        })

        # Timeline entry
        conn.execute(text("""
            INSERT INTO incident_events
                (incident_id, event_type, from_state, to_state, details)
            VALUES
                (:iid, 'state_change', :from_state, 'VerificationResult',
                 json_build_object('client_id', :cid, 'result', 'VerificationResult', 'notes', :notes, 'vr_id', :vrid))
        """), {
            "iid": iid,
            "from_state": prev,
            "to_state": new_state,
            "cid": client_id,
            "outcome": outcome,
            "notes": notes,
            "vrid": vr_id
        })

        # --- If refuted: immediately complete the response workflow ---
        if outcome == "refuted":
            # Move incident to its terminal state
            conn.execute(text("""
                UPDATE incidents
                   SET state = 'Closed',
                       updated_at = NOW()
                 WHERE id = :iid
            """), {"iid": iid})

            # Add a timeline entry for the completion
            conn.execute(text("""
                INSERT INTO incident_events
                    (incident_id, event_type, from_state, to_state, details)
                VALUES
                    (:iid, 'state_changed',  'VerificationResult', 'Closed',
                     json_build_object('result', :outcome, 'notes', :notes))
            """), {
                "iid": iid,
                "from_state": new_state,   # e.g., 'VerifiedRefuted'
                "outcome": outcome,        # 'refuted'
                "notes": notes
            })

            # ensure the JSON you return shows the final state
            new_state = "Closed"

        # --- If unsure: go back to triage---
        if outcome == "unsure":
            # Move incident to its terminal state
            conn.execute(text("""
                UPDATE incidents
                   SET state = 'Triage',
                       updated_at = NOW()
                 WHERE id = :iid
            """), {"iid": iid})

            # Add a timeline entry for the completion
            conn.execute(text("""
                INSERT INTO incident_events
                    (incident_id, event_type, from_state, to_state, details)
                VALUES
                    (:iid, 'state_changed',  'VerificationResult', 'Triage',
                     json_build_object('result', :outcome, 'notes', :notes))
            """), {
                "iid": iid,
                "from_state": new_state,   # e.g., 'VerifiedUnsure'
                "outcome": outcome,        # 'unsure'
                "notes": notes
            })

            # ensure the JSON you return shows the final state
            new_state = "Triage"

    return jsonify({"ok": True, "result": outcome, "state": new_state, "vr_id": vr_id})




# --- VERIFICATION RESULT: record outcome; move to VerificationResult (or close/inconclusive) ---
# POST /incidents/<iid>/verification/result
# body: { task_id, result: 'confirmed'|'dismissed'|'inconclusive', confidence?, notes?, refined_footprint? }
@app.post("/incidents/<iid>/verification/result")
def verification_result(iid):
    data = request.get_json(force=True) or {}
    result  = data.get("result")
    conf    = data.get("confidence")
    notes   = data.get("notes") or {}
    refine  = data.get("refined_footprint")  # GeoJSON Polygon (optional)
    task_id = data.get("task_id")

    if result not in ("confirmed","dismissed","inconclusive"):
        return jsonify({"ok": False, "error": "invalid result"}), 400

    with ENGINE.begin() as conn:
        # optionally update refined footprint & recompute area/dist
        if refine:
            conn.execute(text("""
              UPDATE incidents
                 SET footprint = ST_SetSRID(ST_GeomFromGeoJSON(:g)::geometry,4326),
                     updated_at=NOW()
               WHERE id=:iid
            """), {"iid": iid, "g": json.dumps(refine)})

            # recompute dist_shore_km if you’ve loaded shorelines
            conn.execute(text("""
              WITH coast AS (SELECT ST_Collect(geom)::geography AS g FROM shorelines)
              UPDATE incidents i
                 SET dist_shore_km = CASE
                   WHEN (SELECT g FROM coast) IS NULL THEN NULL
                   WHEN i.footprint IS NOT NULL THEN ST_Distance(i.footprint::geography, (SELECT g FROM coast))/1000.0
                   WHEN i.centroid  IS NOT NULL THEN ST_Distance(i.centroid::geography,  (SELECT g FROM coast))/1000.0
                   ELSE NULL
                 END,
                     updated_at=NOW()
               WHERE i.id=:iid
            """), {"iid": iid})

        # set verification + state
        conn.execute(text("""
          UPDATE incidents
             SET verification = :res, updated_at=NOW()
           WHERE id=:iid
        """), {"iid": iid, "res": result})
        conn.execute(text("""
          UPDATE incidents SET state='VerificationResult', updated_at=NOW() WHERE id=:iid
        """), {"iid": iid})

        conn.execute(text("""
          INSERT INTO incident_events (incident_id, event_type, from_state, to_state, details)
          VALUES (:iid, 'VerificationResult', 'VerificationTasked', 'VerificationResult',
                  jsonb_build_object('task_id', :tid, 'result', :res, 'confidence', :conf, 'notes', :notes::jsonb))
        """), {"iid": iid, "tid": task_id, "res": result, "conf": conf, "notes": json.dumps(notes)})

    return jsonify({"ok": True})



@app.get("/response-rules")
def list_rules():
    try:
        with ENGINE.begin() as conn:
            rows = conn.execute(text("""
                SELECT rr.id,
                       rt.name AS tier_name  ,
                       rt.tier_order AS tier_rank,     -- add this
                       rt.tier_level AS tier_group,    -- keep if you still want 1..3
                       rr.min_probability, rr.max_probability,
                       rr.min_consequence, rr.max_consequence,
                       rr.min_area_km2, rr.max_area_km2,
                       rr.min_dist_shore_km, rr.max_dist_shore_km
                FROM response_rules rr
                JOIN response_tiers  rt ON rt.id = rr.tier_id
                ORDER BY rt.tier_order ASC, rr.id ASC
            """)).mappings().all()

        
        return jsonify({"ok": True, "items": rows_to_dicts(rows)})
    except Exception as e:
        # surfaces SQL / connection issues in JSON so the UI shows the message
        return jsonify({"ok": False, "error": str(e)}), 500


@app.patch("/response-rules/<rid>")
def update_rule(rid):
    data = request.json or {}
    fields = {
      "min_probability":data.get("min_probability"),
      "max_probability":data.get("max_probability"),
      "min_consequence":data.get("min_consequence"),
      "max_consequence":data.get("max_consequence"),
      "min_area_km2": data.get("min_area_km2"),
      "max_area_km2": data.get("max_area_km2"),
      "min_dist_shore_km": data.get("min_dist_shore_km"),
      "max_dist_shore_km": data.get("max_dist_shore_km"),
    }
    sets = ", ".join([f"{k}=:{k}" for k in fields.keys() if fields[k] is not None or k in data])
    if not sets:
        return jsonify({"ok": False, "error": "no fields"}), 400
    with ENGINE.begin() as conn:
        conn.execute(text(f"UPDATE response_rules SET {sets} WHERE id=:id"),
                     {**fields, "id": rid})
    return jsonify({"ok": True})

@app.post("/auto-plan/<iid>")
def api_auto_plan(iid):
    decided_by = request.headers.get("X-User-Id")
    try:
        plan_id, tier_id, area_km2, dist_km, consequence = auto_plan_response_area_dist(iid, decided_by=decided_by, auto=True,fallback_probability=None, fallback_consequence=None)
        print(consequence)
        return jsonify({"ok": True, "plan_id": plan_id, "tier_id": tier_id,
                        "area_km2": area_km2, "dist_km": dist_km, "consequence": consequence})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    


 
    
@app.get("/sensitive-areas")
def api_sensitive_areas():
    # Optional incident cache path
    incident_id = request.args.get("incident_id")

    # If incident_id is provided, read from cached links (fastest)
    if incident_id:
        with ENGINE.begin() as conn:
            # Pull all links for this incident
            links = conn.execute(text("""
                SELECT s.id, s.name, s.site_type, s.provider, s.source_id,
                       s.props,
                       ST_X(COALESCE(s.center, ST_Centroid(s.geom))) AS lon,
                       ST_Y(COALESCE(s.center, ST_Centroid(s.geom))) AS lat,
                       is1.relation_type,
                       is1.distance_m
                  FROM incident_sites is1
                  JOIN sites s ON s.id = is1.site_id
                 WHERE is1.incident_id = :iid
                 ORDER BY is1.distance_m NULLS LAST, s.site_type
            """), {"iid": incident_id}).mappings().all()

        # Split by type
        def as_feat(r):
            return {
                "id": str(r["id"]),
                "name": r["name"],
                "type": r["site_type"],
                "provider": r["provider"],
                "source_id": r["source_id"],
                "geometry_center": [r["lon"], r["lat"]] if r["lon"] is not None and r["lat"] is not None else None,
                "distance_m": r["distance_m"],
                "props": r["props"],
                "relation_type": r["relation_type"],
            }

        ports   = [as_feat(r) for r in links if r["site_type"] == "port"]
        prot    = [as_feat(r) for r in links if r["site_type"] == "protected_area"]
        desals  = [as_feat(r) for r in links if r["site_type"] == "desalination"]

        # nearest = the ones recorded as 'nearest' (fallback to min distance if not present)
        def pick_nearest(rows):
            if not rows: return None
            for r in rows:
                if r.get("relation_type") == "nearest":
                    return r
            return min(rows, key=lambda x: x["distance_m"] if x["distance_m"] is not None else 9e18)

        nearest = {
            "port":            pick_nearest(ports),
            "protected_area":  pick_nearest(prot),
            "desalination":    pick_nearest(desals),
        }

        return jsonify({
            "ok": True,
            "query": {"incident_id": incident_id},
            "nearest": nearest,
            "lists": {
                "ports": ports,
                "protected_areas": prot,
                "desalination": desals
            }
        })

    # Otherwise: ad-hoc query by lat/lon against sites table (no external calls)
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        radius_km = float(request.args.get("radius_km", 30))
    except KeyError:
        return jsonify({"ok": False, "error": "lat and lon are required (or provide incident_id)"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "lat, lon, radius_km must be numbers"}), 400

    radius_m = int(radius_km * 1000)

    with ENGINE.begin() as conn:
        rows = conn.execute(text("""
            WITH q AS (
              SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography AS pt
            )
            SELECT s.id, s.name, s.site_type, s.provider, s.source_id, s.props,
                   ST_X(COALESCE(s.center, ST_Centroid(s.geom))) AS lon,
                   ST_Y(COALESCE(s.center, ST_Centroid(s.geom))) AS lat,
                   ST_Distance((SELECT pt FROM q),
                               COALESCE(s.center, ST_Centroid(s.geom))::geography) AS distance_m
              FROM sites s
             WHERE COALESCE(s.center, ST_Centroid(s.geom)) IS NOT NULL
               AND ST_DWithin((SELECT pt FROM q),
                              COALESCE(s.center, ST_Centroid(s.geom))::geography, :radius_m)
             ORDER BY distance_m ASC
        """), {"lon": lon, "lat": lat, "radius_m": radius_m}).mappings().all()

    def as_feat(r):
        return {
            "id": str(r["id"]),
            "name": r["name"],
            "type": r["site_type"],
            "provider": r["provider"],
            "source_id": r["source_id"],
            "geometry_center": [r["lon"], r["lat"]] if r["lon"] is not None and r["lat"] is not None else None,
            "distance_m": r["distance_m"],
            "props": r["props"],
        }

    features = [as_feat(r) for r in rows]
    ports    = [f for f in features if f["type"] == "port"]
    prot     = [f for f in features if f["type"] == "protected_area"]
    desals   = [f for f in features if f["type"] == "desalination"]

    def pick_nearest(lst): return lst[0] if lst else None

    nearest = {
        "port":            pick_nearest(ports),
        "protected_area":  pick_nearest(prot),
        "desalination":    pick_nearest(desals),
    }

    return jsonify({
        "ok": True,
        "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "nearest": nearest,
        "lists": {
            "ports": ports,
            "protected_areas": prot,
            "desalination": desals
        }
    })

@app.get("/tier-level")
def api_tier_level():
    incident_id = request.args.get("incident_id")
    org_id      = request.args.get("org_id")

    with ENGINE.begin() as conn:
        # 1) Resolve org_id
        if org_id is None:
            if incident_id:
                row = conn.execute(text("""
                    SELECT org_id FROM incidents WHERE id = :iid
                """), {"iid": incident_id}).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "incident not found"}), 404
                org_id = str(row[0])
            else:
                # fallback: take org from the latest incident
                row = conn.execute(text("""
                    SELECT org_id
                    FROM incidents
                    ORDER BY created_at DESC
                    LIMIT 1
                """)).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "no org_id and no incidents to infer from"}), 400
                org_id = str(row[0])

        # 2) Get tiers for this org
        tiers = conn.execute(text("""
            SELECT
              rt.id,
              rt.name,
              rt.tier_order,
              rt.tier_level,
              rt.description
            FROM response_tiers rt
            WHERE rt.org_id = :org_id
            ORDER BY rt.tier_order ASC, rt.name ASC
        """), {"org_id": org_id}).mappings().all()

        if not tiers:
            return jsonify({"ok": True, "org_id": org_id, "tiers": []})

        tier_ids = [t["id"] for t in tiers]

        # 3) Get plans for these tiers
        # assume table: response_plans(id, org_id, tier_id, name, details/json/actions)
        plans = conn.execute(text("""
            SELECT
              ra.id,
              ra.tier_id,
              ra.name,
              ra.description,
              ra.plan_json
            FROM response_actions ra
            WHERE ra.org_id = :org_id
              AND ra.tier_id = ANY(:tier_ids)
            ORDER BY ra.name ASC
        """), {"org_id": org_id, "tier_ids": tier_ids}).mappings().all()

    # 4) group plans under tiers
    plans_by_tier = {}
    for p in plans:
        tid = p["tier_id"]
        plans_by_tier.setdefault(tid, []).append({
            "id": str(p["id"]),
            "name": p["name"],
            "description": p["description"],
            "plan_json": p["plan_json"],
        })

    out = []
    for t in tiers:
        tid = t["id"]
        out.append({
            "id": str(tid),
            "name": t["name"],
            "tier_order": t["tier_order"],
            "tier_level": t["tier_level"],
            "description": t["description"],
            "plans": plans_by_tier.get(tid, [])
        })

    return jsonify({
        "ok": True,
        "org_id": org_id,
        "tiers": out
    })




@app.get("/incidents/<iid>/response-plan")
def api_response_plan(iid):
    incident_id = iid
    if not incident_id:
        return jsonify({"ok": False, "error": "incident_id is required"}), 400

    try:
        with ENGINE.begin() as conn:
            # get the most recent plan for this incident
            row = conn.execute(text("""
                SELECT
                  rp.id,
                  rp.incident_id,
                  rp.tier_id,
                  rp.decided_by,
                  rp.decided_at,
                  rp.auto_generated,
                  rp.rationale,
                  rp.snapshot,
                  rp.name,
                  rp.description,
                  rp.plan_json,
                  rp.response_action_id,
                  rt.tier_level,
                  rt.description
                FROM response_plans rp
                LEFT JOIN response_tiers rt
                  ON rt.id = rp.tier_id
                WHERE rp.incident_id = :iid
                ORDER BY rp.decided_at DESC, rp.created_at DESC
                LIMIT 1
            """), {"iid": incident_id}).mappings().first()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    if not row:
        # no plan yet for this incident
        return jsonify({
            "ok": True,
            "incident_id": incident_id,
            "plan": None
        })

    # normalize JSON fields
    snap = row["snapshot"]
    plan_json = row["plan_json"]
    # snapshot and plan_json are jsonb from PG; if your driver gives strings, you can parse:
    # (safe to leave as-is if already dicts)

    # convert datetimes to string
    decided_at = row["decided_at"].isoformat() if row["decided_at"] else None

    return jsonify({
        "ok": True,
        "incident_id": incident_id,
        "tier_level": row["tier_level"],
        "tier_description": row["description"],
        "plan": {
            "id": str(row["id"]),
            "incident_id": str(row["incident_id"]),
            "tier_id": str(row["tier_id"]) if row["tier_id"] else None,
            "decided_by": str(row["decided_by"]) if row["decided_by"] else None,
            "decided_at": decided_at,
            "auto_generated": bool(row["auto_generated"]),
            "rationale": row["rationale"] or "",
            "name": row["name"] or "",
            "description": row["description"] or "",
            "snapshot": snap or {},
            "plan_json": plan_json or {},
            "response_action_id": str(row["response_action_id"]) if row["response_action_id"] else None
        }
    })


@app.post("/incidents/<iid>/complete-response")
def complete_response(iid):
    """
    Body:
      {
        "client_id": "pi-123",
        
        "notes": "free text",
        "evidence": { "photos": [...], "video": [...] }  # optional
      }
    
    """
    data = request.get_json(force=True) or {}
    client_id = data.get("clientId")
    
    notes     = data.get("notes")
    evidence  = data.get("evidence") or {}

    

    with ENGINE.begin() as conn:
        

        

     
        
        # Move incident to its terminal state
        conn.execute(text("""
            UPDATE incidents
                SET state = 'ResponseComplete',
                    updated_at = NOW()
                WHERE id = :iid
        """), {"iid": iid})

        

        # Add a timeline entry for the completion
        conn.execute(text("""
            INSERT INTO incident_events
                (incident_id, event_type, from_state, to_state, details)
            VALUES
                (:iid, 'state_changed',  'ResponseActive', 'ResponseComplete',
                    json_build_object('notes', :notes))
        """), {
            "iid": iid,
            "from_state": 'ResponseComplete',   # e.g., 'VerifiedRefuted'
            
            "notes": notes
        })

        conn.execute(text("""
            INSERT INTO incident_events
                (incident_id, event_type, from_state, to_state, details)
            VALUES
                (:iid, 'state_change',  'ResponseComplete', 'Closed',
                    json_build_object('notes', :notes))
        """), {
            "iid": iid,
            "from_state": 'Closed',   # e.g., 'VerifiedRefuted'
            
            "notes": notes
        })

        # ensure the JSON you return shows the final state
        new_state = "Closed"

       

    return jsonify({"ok": True})

def api_sensitive_areas_old():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        radius_km = float(request.args.get("radius_km", 20))
    except KeyError:
        return jsonify({"ok": False, "error": "lat and lon are required"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "lat, lon, radius_km must be numbers"}), 400

    data = query_sensitive_areas(lat=lat, lon=lon, radius_km=radius_km)

    # SAFETY: ensure we got a dict (not a Response)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Internal: expected dict from query_sensitive_areas"}), 500

    return jsonify({"ok": True, **data})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8282, debug=True)
