#cat > ~/oilapp/ims_ops.py << 'PY'
from sqlalchemy import text
from db import ENGINE
from uuid import UUID
from datetime import datetime
import json


# Utility to run one transaction and return a connection
def _tx():
    return ENGINE.begin()

def _as_uuid_or_none(val: str | None):
    if not val:
        return None
    try:
        return str(UUID(val))
    except Exception:
        return None

def get_org_id():
    with _tx() as conn:
        oid = conn.execute(text("SELECT id FROM orgs ORDER BY created_at LIMIT 1")).scalar()
        if not oid:
            oid = conn.execute(text("INSERT INTO orgs (name) VALUES ('Default Org') RETURNING id")).scalar()
        return oid

def create_user(email="ops@example.com", name="Ops", role="operator"):
    with _tx() as conn:
        org_id = get_org_id()
        uid = conn.execute(text("""
            INSERT INTO users (org_id, email, display_name, role)
            VALUES (:o,:e,:n,:r) RETURNING id
        """), {"o": org_id, "e": email, "n": name, "r": role}).scalar()
        return uid
    
def _compute_consequence_from_incident(row):
    """
    row must have:
      - nearest_protected_distance_m
      - nearest_desal_distance_m
      - nearest_port_distance_m
      - dist_shore_km
    Returns int 1..5
    """
    
    info  = row.get("snapshot")
    shore_km = row.get("dist_shore_km")

    if info['type'] == 'protected_area':
        prot_m  = row.get("distance_m")
        desal_m = None
        port_m = None
    elif info['type'] == 'desalination':
        desal_m  = row.get("distance_m")
    elif info['type'] == 'port':
        port_m  = row.get("distance_m")

  
   

    prot_km  = (prot_m  / 1000.0) if prot_m  is not None else None
    desal_km = (desal_m / 1000.0) if desal_m is not None else None
    port_km  = (port_m  / 1000.0) if port_m  is not None else None

    
    # 1) Hard escalators (same as in SQL version)
    if prot_km is not None and prot_km <= 3:
        return 5
    if desal_km is not None and desal_km <= 10:
        return 5

    pts = 0

    # 2) Protected proximity
    if prot_km is not None:
        if prot_km <= 15: pts += 3
        elif prot_km <= 30: pts += 2

    # 3) Desal proximity (outside hard escalator)
    if desal_km is not None:
        if desal_km <= 20: pts += 2

    # 4) Port / infra proximity
    if port_km is not None:
        if port_km <= 1: pts += 3
        elif port_km <= 5: pts += 2
        elif port_km <= 10: pts += 1

    # 5) Shoreline distance
    if shore_km is not None:
        if shore_km <= 1: pts += 2
        elif shore_km <= 3: pts += 1
        elif shore_km >= 50: pts -= 2
        elif shore_km >= 20: pts -= 1

    # 6) Map points -> consequence
    if pts >= 8:   return 5
    if pts >= 5:   return 4
    if pts >= 3:   return 3
    if pts >= 1:   return 2
    return 1

def insert_detection(org_id, model_name, model_version, image_id, captured_at_iso, confidence, polygon_wkt, extra_json="{}"):
    sql = text("""
      INSERT INTO detections
      (org_id, model_name, model_version, image_id, captured_at, confidence, polygon, extra)
      VALUES (:org, :mname, :mver, :img, :ts, :conf, ST_GeomFromText(:wkt,4326), :extra::jsonb)
      RETURNING id, area_sqkm
    """)
    with _tx() as conn:
        row = conn.execute(sql, {
            "org": org_id, "mname": model_name, "mver": model_version,
            "img": image_id, "ts": captured_at_iso, "conf": confidence,
            "wkt": polygon_wkt, "extra": extra_json
        }).mappings().one()
        return row["id"], row["area_sqkm"]

def create_incident_from_detections(detection_ids, title="New Incident", detection_source="deeplabv3", raised_by=None):
    if not detection_ids:
        raise ValueError("detection_ids cannot be empty")
    placeholders = ", ".join([f":d{i}" for i in range(len(detection_ids))])
    params = {f"d{i}": d for i, d in enumerate(detection_ids)}

    # Union polygons, compute centroid/area, create incident, attach members
    with _tx() as conn:
        org_id = get_org_id()
        union_sql = text(f"""
            WITH dets AS (
              SELECT polygon FROM detections WHERE id IN ({placeholders})
            ), geom AS (
              SELECT ST_UnaryUnion(ST_Collect(polygon)) AS footprint
              FROM dets
            )
            INSERT INTO incidents
              (org_id, title, state, priority, centroid, footprint, est_area_sqkm, detection_source, raised_by)
            SELECT :org, :title, 'Detection', 3,
                   ST_Centroid(footprint),
                   footprint,
                   ST_Area(ST_Transform(footprint, 4326)::geography)/1e6,
                   :src, :uid
            FROM geom
            RETURNING id
        """)
        inc_id = conn.execute(union_sql, {"org": org_id, "title": title, "src": detection_source, "uid": raised_by, **params}).scalar()

        link_sql = text(f"""
          INSERT INTO incident_detections (incident_id, detection_id)
          SELECT :iid, id FROM detections WHERE id IN ({placeholders})
        """)
        conn.execute(link_sql, {"iid": inc_id, **params})

        # timeline note
        conn.execute(text("""
          INSERT INTO incident_events (incident_id, event_type, details)
          VALUES (:iid, 'note', '{"msg":"incident created from detections"}'::jsonb)
        """), {"iid": inc_id})

        return inc_id

def set_state(incident_id, new_state, actor_user):
    with _tx() as conn:
        # attribute action for the trigger
        conn.execute(text("SELECT set_config('app.current_user', :u, false)"), {"u": str(actor_user)})
        conn.execute(text("UPDATE incidents SET state=:s WHERE id=:id"), {"s": new_state, "id": incident_id})

def create_verification_mission(incident_id, assigned_asset=None):
    with _tx() as conn:
        # waypoint = incident centroid
        waypoint = conn.execute(text("SELECT centroid FROM incidents WHERE id=:id"), {"id": incident_id}).scalar()
        mid = conn.execute(text("""
          INSERT INTO missions (org_id, incident_id, purpose, waypoint, status, assigned_to)
          SELECT i.org_id, i.id, 'verification', i.centroid, 'planned', :asset
          FROM incidents i WHERE i.id=:id
          RETURNING id
        """), {"id": incident_id, "asset": assigned_asset}).scalar()
        return mid

def record_verification(incident_id, outcome, actor_user):
    if outcome not in ("confirmed","refuted","unsure"):
        raise ValueError("outcome must be confirmed|refuted|unsure")
    with _tx() as conn:
        conn.execute(text("SELECT set_config('app.current_user', :u, false)"), {"u": str(actor_user)})
        conn.execute(text("""
          UPDATE incidents
          SET state='VerificationResult', verification=:o, verified_by=:u, verified_at=now()
          WHERE id=:id
        """), {"o": outcome, "u": actor_user, "id": incident_id})
        if outcome in ("refuted","unsure"):
            conn.execute(text("UPDATE incidents SET state='Closed' WHERE id=:id"), {"id": incident_id})

def auto_plan_response(incident_id, probability, consequence, decided_by=None, auto=True):
    # pick tier from response_rules, create response_plans, move state to ResponsePlanning
    with _tx() as conn:
        row = conn.execute(text("""
          WITH i AS (SELECT org_id FROM incidents WHERE id=:iid)
          SELECT rr.tier_id FROM response_rules rr
          WHERE rr.org_id = (SELECT org_id FROM i)
            AND :p BETWEEN rr.min_probability AND rr.max_probability
            AND :c BETWEEN rr.min_consequence AND rr.max_consequence
          ORDER BY rr.id LIMIT 1
        """), {"iid": incident_id, "p": probability, "c": consequence}).fetchone()
        if not row:
            raise RuntimeError("No matching response rule for given probability/consequence")
        tier_id = row[0]

        plan_id = conn.execute(text("""
          INSERT INTO response_plans (incident_id, tier_id, decided_by, auto_generated, rationale, snapshot)
          SELECT :iid, :tid, :uid, :auto, :why,
                 rr.actions_template
          FROM response_rules rr WHERE rr.tier_id=:tid
          RETURNING id
        """), {"iid": incident_id, "tid": tier_id, "uid": decided_by, "auto": auto,
               "why": f"Auto plan p={probability:.2f}, c={consequence}"}).scalar()

        # transition states
        conn.execute(text("UPDATE incidents SET state='ResponsePlanning' WHERE id=:iid"), {"iid": incident_id})
        # app would expand snapshot -> missions/tasks here
        conn.execute(text("UPDATE incidents SET state='ResponseActive' WHERE id=:iid"), {"iid": incident_id})

        return plan_id, tier_id
    
def auto_plan_response_area_dist1(incident_id, decided_by=None, auto=True,
                                 fallback_probability=None, fallback_consequence=None):
    """
    Pick a response_tier via response_rules using:
      - area_km2: ST_Area(footprint) km², fallback to est_area_sqkm
      - dist_km : distance of (footprint centroid or centroid) to shoreline, if shorelines table exists
    Then create a response_plans row using the rule's actions_template (JSON).
    """
    uid = _as_uuid_or_none(decided_by)  # avoid uuid cast errors

    with ENGINE.begin() as conn:
        # Pull org_id + est_area + any geometries we can use
        inc = conn.execute(text("""
            SELECT org_id,
                   est_area_sqkm,
                   footprint,
                   centroid
            FROM incidents
            WHERE id = :iid
        """), {"iid": incident_id}).mappings().one_or_none()
        if not inc:
            raise RuntimeError("Incident not found")

        # Compute area_km2: prefer footprint area, else est_area_sqkm
        area_km2 = conn.execute(text("""
            SELECT COALESCE(
                    CASE WHEN footprint IS NOT NULL
                          THEN ST_Area(footprint::geography)/1e6
                    END,
                    est_area_sqkm
                  ) AS area_km2
            FROM incidents
            WHERE id = :iid
        """), {"iid": incident_id}).scalar()

        if area_km2 is None:
            raise RuntimeError("Incident has no area (no footprint and no est_area_sqkm)")

        # Compute distance to shoreline (km) using footprint centroid if present, else centroid
        dist_km = conn.execute(text("""
          WITH coast AS (
            SELECT ST_Collect(geom)::geography AS g FROM shorelines
          ),
          i AS (
            SELECT CASE
                    WHEN footprint IS NOT NULL THEN ST_Centroid(footprint)::geography
                    WHEN centroid  IS NOT NULL THEN centroid::geography
                    ELSE NULL
                  END AS g
            FROM incidents
            WHERE id = :iid
          )
          SELECT CASE
                  WHEN (SELECT g FROM coast) IS NULL OR (SELECT g FROM i) IS NULL
                  THEN NULL
                  ELSE ST_Distance((SELECT g FROM i), (SELECT g FROM coast)) / 1000.0
                END
        """), {"iid": incident_id}).scalar()

        # Compute consequence
        

        # Find matching rule for this org
        row = conn.execute(text("""
          WITH i AS (SELECT org_id FROM incidents WHERE id=:iid)
          SELECT rr.tier_id
          FROM response_rules rr
          WHERE rr.org_id = (SELECT org_id FROM i)
            AND (rr.min_area_km2       IS NULL OR :a >= rr.min_area_km2)
            AND (rr.max_area_km2       IS NULL OR :a <  rr.max_area_km2)
            AND (
                 :d IS NULL  -- if distance unknown, ignore distance bands
                 OR (
                      (rr.min_dist_shore_km IS NULL OR :d >= rr.min_dist_shore_km) AND
                      (rr.max_dist_shore_km IS NULL OR :d <  rr.max_dist_shore_km)
                    )
                )
          ORDER BY rr.id
          LIMIT 1
        """), {"iid": incident_id, "a": area_km2, "d": dist_km}).fetchone()

        # Optional fallback: probability / consequence bands
        if not row and (fallback_probability is not None and fallback_consequence is not None):
            row = conn.execute(text("""
              WITH i AS (SELECT org_id FROM incidents WHERE id=:iid)
              SELECT rr.tier_id
              FROM response_rules rr
              WHERE rr.org_id = (SELECT org_id FROM i)
                AND (rr.min_probability IS NULL OR :p >= rr.min_probability)
                AND (rr.max_probability IS NULL OR :p <  rr.max_probability)
                AND (rr.min_consequence IS NULL OR :c >= rr.min_consequence)
                AND (rr.max_consequence IS NULL OR :c <  rr.max_consequence)
              ORDER BY rr.id
              LIMIT 1
            """), {"iid": incident_id, "p": fallback_probability, "c": fallback_consequence}).fetchone()

        if not row:
            raise RuntimeError("No matching response rule for area/dist (and no fallback matched).")

        tier_id = row[0]

        # Insert plan; scope the rule by org to be safe; default actions_template -> {}
        why = (f"Auto by area={area_km2:.3f} km², dist={dist_km:.2f} km"
               if dist_km is not None else
               f"Auto by area={area_km2:.3f} km² (no coastline loaded)")

        plan_id = conn.execute(text("""
          INSERT INTO response_plans (incident_id, tier_id, decided_by, auto_generated, rationale, snapshot)
          SELECT :iid, rr.tier_id, :uid, :auto, :why,
                 COALESCE(rr.actions_template, '{}'::jsonb)
          FROM response_rules rr
          JOIN incidents i ON i.org_id = rr.org_id
          WHERE rr.tier_id = :tid AND i.id = :iid
          RETURNING id
        """), {"iid": incident_id, "tid": tier_id, "uid": uid, "auto": bool(auto), "why": why}).scalar()

        # Advance state (and record events if you track them)
        conn.execute(text("UPDATE incidents SET state='ResponsePlanning' WHERE id=:iid"), {"iid": incident_id})
        conn.execute(text("UPDATE incidents SET state='ResponseActive'   WHERE id=:iid"), {"iid": incident_id})

        return plan_id, tier_id, area_km2, dist_km
    
def auto_plan_response_area_dist(incident_id, decided_by=None, auto=True,
                                 fallback_probability=None, fallback_consequence=None):
    """
    Pick a response_tier via response_rules using:
      - area_km2: ST_Area(footprint) km², fallback to est_area_sqkm
      - dist_km : distance of (footprint centroid or centroid) to shoreline, if shorelines table exists
    Then create a response_plans row using the rule's actions_template (JSON).
    """
    uid = _as_uuid_or_none(decided_by)  # avoid uuid cast errors

    with ENGINE.begin() as conn:
        # Pull org_id + est_area + any geometries we can use
        inc = conn.execute(text("""
            SELECT org_id,
                   est_area_sqkm,
                   footprint,
                   centroid
            FROM incidents
            WHERE id = :iid
        """), {"iid": incident_id}).mappings().one_or_none()
        if not inc:
            raise RuntimeError("Incident not found")
        
        #print(inc)

        inc_sites = conn.execute(text("""
            SELECT incident_id,
                   distance_m,
                   snapshot
            FROM incident_sites
            WHERE incident_id = :iid
            ORDER BY distance_m ASC NULLS LAST
            LIMIT 1
        """), {"iid": incident_id}).mappings().one_or_none()
        if not inc_sites:
            raise RuntimeError("Incident site not found")
        
        # This throws an error as possible multiple rows are present for inc_sites - .mappings().one_or_none()
        print(inc_sites)

        # Compute area_km2: prefer footprint area, else est_area_sqkm
        area_km2 = conn.execute(text("""
            SELECT COALESCE(
                    CASE WHEN footprint IS NOT NULL
                          THEN ST_Area(footprint::geography)/1e6
                    END,
                    est_area_sqkm
                  ) AS area_km2
            FROM incidents
            WHERE id = :iid
        """), {"iid": incident_id}).scalar()

        if area_km2 is None:
            raise RuntimeError("Incident has no area (no footprint and no est_area_sqkm)")

        # Compute distance to shoreline (km) using footprint centroid if present, else centroid
        dist_km = conn.execute(text("""
          WITH coast AS (
            SELECT ST_Collect(geom)::geography AS g FROM shorelines
          ),
          i AS (
            SELECT CASE
                    WHEN footprint IS NOT NULL THEN ST_Centroid(footprint)::geography
                    WHEN centroid  IS NOT NULL THEN centroid::geography
                    ELSE NULL
                  END AS g
            FROM incidents
            WHERE id = :iid
          )
          SELECT CASE
                  WHEN (SELECT g FROM coast) IS NULL OR (SELECT g FROM i) IS NULL
                  THEN NULL
                  ELSE ST_Distance((SELECT g FROM i), (SELECT g FROM coast)) / 1000.0
                END
        """), {"iid": incident_id}).scalar()

        # Compute consequence
        
        auto_consequence = _compute_consequence_from_incident(inc_sites)
        


        # if caller passed an explicit consequence, take the stricter / higher
        if fallback_consequence is not None:
            consequence = max(int(fallback_consequence), auto_consequence)
        else:
            consequence = auto_consequence
        
        
        # Find matching rule for this org
        row = conn.execute(text("""
          WITH i AS (SELECT org_id FROM incidents WHERE id = :iid)
          SELECT rr.id AS rule_id, rr.tier_id
          FROM response_rules rr
          JOIN response_tiers rt ON rt.id = rr.tier_id
          WHERE rr.org_id = (SELECT org_id FROM i)

            -- Area band (optional)
            AND (
              :a IS NULL OR
              ((rr.min_area_km2 IS NULL OR :a >= rr.min_area_km2) AND
              (rr.max_area_km2 IS NULL OR :a <  rr.max_area_km2))
            )

            -- Distance band (optional)
            AND (
              :d IS NULL OR
              ((rr.min_dist_shore_km IS NULL OR :d >= rr.min_dist_shore_km) AND
              (rr.max_dist_shore_km IS NULL OR :d <  rr.max_dist_shore_km))
            )

            -- Probability band (optional)
            AND (
              :p IS NULL OR
              ((rr.min_probability IS NULL OR :p >= rr.min_probability) AND
              (rr.max_probability IS NULL OR :p <  rr.max_probability))
            )

            -- Consequence band (optional)
            AND (
              :c IS NULL OR
              ((rr.min_consequence IS NULL OR :c >= rr.min_consequence) AND
              (rr.max_consequence IS NULL OR :c <  rr.max_consequence))
            )

          ORDER BY
            rt.tier_order ASC,  -- your policy priority first (1 = highest)
            (
              (rr.min_probability  IS NOT NULL)::int + (rr.max_probability  IS NOT NULL)::int +
              (rr.min_consequence  IS NOT NULL)::int + (rr.max_consequence  IS NOT NULL)::int +
              (rr.min_area_km2     IS NOT NULL)::int + (rr.max_area_km2     IS NOT NULL)::int +
              (rr.min_dist_shore_km IS NOT NULL)::int + (rr.max_dist_shore_km IS NOT NULL)::int
            ) DESC,
            rr.id ASC
          LIMIT 1
        """), {
          "iid": incident_id,
          "a": area_km2,                # float or None
          "d": dist_km,                 # float or None
          "p": fallback_probability,    # float or None
          "c": consequence     # int(1..5) or None
        }).mappings().first()

        if not row:
            raise RuntimeError("No matching response rule for area/dist (and no fallback matched).")

        tier_id = row["tier_id"]

        # Insert plan; scope the rule by org to be safe; default actions_template -> {}
        why = (f"Auto by area={area_km2:.3f} km², dist={dist_km:.2f} km, consequence={consequence:.0f}"
               if dist_km is not None else
               f"Auto by area={area_km2:.3f} km² (no coastline loaded)")
        
        # pick action template for this tier
        act = conn.execute(text("""
          SELECT id, name, plan_json
          FROM response_actions
          WHERE org_id = :org_id AND tier_id = :tier_id
          ORDER BY created_at ASC
          LIMIT 1
        """), {"org_id": inc["org_id"], "tier_id": tier_id}).mappings().first()

        if not act:
          # fallback: we will just use rr.actions_template below
          action_id = None
          action_snapshot = None
        else:
            action_id = act["id"]
            action_snapshot = act["plan_json"] or {}
        


        # 2) insert the incident-scoped plan
        plan_id = conn.execute(text("""
          INSERT INTO response_plans (
              incident_id,
              tier_id,
              decided_by,
              auto_generated,
              rationale,
              snapshot,
              response_action_id
          )
          SELECT
              i.id AS incident_id,
              rr.tier_id,
              :uid AS decided_by,
              :auto AS auto_generated,
              :why AS rationale,
              -- priority: action.plan_json → rule.actions_template → {}
              COALESCE(ra.plan_json, rr.actions_template, '{}'::jsonb) AS snapshot,
              ra.id AS response_action_id
          FROM incidents i
          -- pick the rule that matches this org + tier
          JOIN response_rules rr
            ON rr.org_id = i.org_id
          AND rr.tier_id = :tid
          -- pick ONE action for this org + tier (the earliest one)
          LEFT JOIN LATERAL (
            SELECT rax.id, rax.plan_json
            FROM response_actions rax
            WHERE rax.org_id = i.org_id
              AND rax.tier_id = rr.tier_id
            ORDER BY rax.created_at ASC
            LIMIT 1
          ) ra ON TRUE
          WHERE i.id = :iid
          RETURNING id
        """), {
            "iid": incident_id,
            "tid": tier_id,
            "uid": uid,
            "auto": bool(auto),
            "why": why,
        }).scalar()

        # Advance state (and record events if you track them)
        conn.execute(text("UPDATE incidents SET state='ResponsePlanning' WHERE id=:iid"), {"iid": incident_id})
        conn.execute(text("UPDATE incidents SET state='ResponseActive'   WHERE id=:iid"), {"iid": incident_id})

        return plan_id, tier_id, area_km2, dist_km, consequence

def complete_response(incident_id):
    with _tx() as conn:
        conn.execute(text("UPDATE tasks SET status='done' WHERE incident_id=:id AND status<>'done'"), {"id": incident_id})
        conn.execute(text("UPDATE missions SET status='complete' WHERE incident_id=:id AND status<>'complete'"), {"id": incident_id})
        conn.execute(text("UPDATE incidents SET state='ResponseComplete' WHERE id=:id"), {"id": incident_id})

