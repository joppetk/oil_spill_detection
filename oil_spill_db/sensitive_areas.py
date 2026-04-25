# app_sensitive.py
import math, os, requests, json
from flask import Flask, request, jsonify

try:
    from shapely.geometry import shape, Point
    HAVE_SHAPELY = True
except Exception:
    HAVE_SHAPELY = False

app = Flask(__name__)
app.url_map.strict_slashes = False  # accept /api and /api/

@app.errorhandler(Exception)
def json_errors(e):
    code = 500
    if isinstance(e, HTTPException):
        code = e.code
    return jsonify({"ok": False, "error": str(e)}), code

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2*R*math.asin(math.sqrt(a))

def bbox_from_point(lat, lon, radius_m):
    # ~111.32 km per degree lat; lon depends on latitude
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.0001, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

def dist_to_feature_m(lat, lon, feat):
    """Distance from (lat,lon) to a GeoJSON Feature. Uses shapely if available, else center/point."""
    geom = feat.get("geometry")
    if not geom:
        c = feat.get("center") or feat.get("geometry_center")
        if c:  # [lon,lat]
            return haversine_m(lat, lon, c[1], c[0])
        return float("inf")

    # Point
    if geom["type"] == "Point":
        x, y = geom["coordinates"]
        return haversine_m(lat, lon, y, x)

    # Non-point: prefer shapely true distance if available
    if HAVE_SHAPELY:
        try:
            g = shape(geom)
            p = Point(lon, lat)
            # Approximate: project to geodesic by sampling… shapely distance is planar;
            # for small radii it's fine. Otherwise fallback to nearest vertex.
            return g.distance(p) * 111_320.0  # deg -> meters approx
        except Exception:
            pass

    # Fallback: distance to polygon/way "center"
    if "bbox" in feat:
        minx, miny, maxx, maxy = feat["bbox"]
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        return haversine_m(lat, lon, cy, cx)

    # Last resort: centroid-ish from coords
    try:
        # Take first coordinate ring's first vertex
        coords = None
        if geom["type"] == "Polygon":
            coords = geom["coordinates"][0][0]  # [lon,lat]
        elif geom["type"] == "MultiPolygon":
            coords = geom["coordinates"][0][0][0]
        if coords:
            return haversine_m(lat, lon, coords[1], coords[0])
    except Exception:
        pass
    return float("inf")

def best_one(features, lat, lon):
    if not features:
        return None
    for f in features:
        f["distance_m"] = dist_to_feature_m(lat, lon, f)
    features.sort(key=lambda f: f.get("distance_m", float("inf")))
    return features[0]


def query_sensitive_areas(lat: float, lon: float, radius_km: float = 30.0) -> dict:
    try:
        """ lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        radius_km = float(request.args.get("radius_km", "30")) """
        radius_m = int(radius_km * 1000)

        ports, protected, desal = [], [], []

        minx, miny, maxx, maxy = bbox_from_point(lat, lon, radius_m)

        # ---------- 1) PORTS: World Port Index (ArcGIS FeatureServer) ----------
        try:
            wpi_url = "https://services9.arcgis.com/j1CY4yzWfwptbTWN/arcgis/rest/services/WorldPortIndex_WFL1/FeatureServer/0/query"
            params = {
                "where": "1=1",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "distance": radius_km,                  # e.g. 100 for 100 km
                "units": "esriSRUnit_Kilometer",
                "returnGeodetic": "true",
                "outFields": "OBJECTID,PORT_NAME,COUNTRY,LATITUDE,LONGITUDE,ANCH_DEPTH",
                "returnGeometry": "true",
                "outSR": 4326,
                "f": "json"
            }
            
            r = requests.get(wpi_url, params=params, timeout=20)
            if r.ok:
                gj = r.json()
                print("[sensitive][ports] feature count:", len(gj.get("features", [])))

                for f in gj.get("features", []):
                    attrs = f.get("attributes", {}) or {}
                    geom = f.get("geometry", {}) or {}

                    ports.append({
                        "name": attrs.get("PORT_NAME") or "Unnamed port",
                        "type": "port",
                        "country": attrs.get("COUNTRY"),
                        "depth": attrs.get("ANCH_DEPTH"),
                        "geometry_center": [geom.get("x"), geom.get("y")] if geom else None,
                        "lat": attrs.get("LATITUDE"),
                        "lon": attrs.get("LONGITUDE"),
                        "objectid": attrs.get("OBJECTID"),
                    })

                print("[sensitive][ports] parsed ports:", ports[:3])

            print("[sensitive][ports] status:", r.status_code)
            print("[sensitive][ports] body:", r.text[:300])
        except Exception as e:
            print("[sensitive][ports] failed:", repr(e))

        # ---------- 2) PROTECTED AREAS ----------
        # 2a) Ramsar WFS by bbox (wetlands of international importance)
        try:
            minx, miny, maxx, maxy = bbox_from_point(lat, lon, radius_m)
            wfs_url = "https://rsis.ramsar.org/geoserver/ows"
            wfs_url = "https://rsis.ramsar.org/geoserver/wfs"
            params = {
                "service": "WFS",
                #"version": "2.0.0",
                "version": "1.0.0",
                "request": "GetFeature",
                "typeNames": "rsis:sites",
                "outputFormat": "application/json",
                #"bbox": f"{minx},{miny},{maxx},{maxy},EPSG:4326"
                "bbox": f"{minx},{miny},{maxx},{maxy}"
            }
            r = requests.get(wfs_url, params=params, timeout=25)
            if r.ok:
                gj = r.json()
                for f in gj.get("features", []):
                    props = f.get("properties", {})
                    protected.append({
                        "name": props.get("sitename") or "Ramsar site",
                        "type": "protected_area",
                        "designation": "Ramsar",
                        "geometry": f.get("geometry"),
                        "bbox": f.get("bbox")
                    })
            print("[sensitive][protected-a] status:", r.status_code)
            print("[sensitive][protected-a] body:", r.text[:300])
        except Exception as e:
            print("[sensitive][protected-a] failed:", repr(e))

        # 2b) OSM protected areas / nature reserves (backup)
        try:
            overpass = "https://overpass-api.de/api/interpreter"
            q_prot = f"""
            [out:json][timeout:25];
            (
              relation(around:{radius_m},{lat},{lon})["boundary"="protected_area"];
              way(around:{radius_m},{lat},{lon})["leisure"="nature_reserve"];
              relation(around:{radius_m},{lat},{lon})["protect_class"];
              relation(around:{radius_m},{lat},{lon})["marine_protected_area"="yes"];
            );
            out center tags;
            """
            #r = requests.post(overpass, data=q_prot.encode("utf-8"), timeout=30)
            r = requests.post(
                overpass,
                data={"data": q_prot},
                headers={"Accept": "application/json", "User-Agent": "oil-spill-ims/1.0"},
                timeout=30
            )
            if r.ok:
                data = r.json()
                for el in data.get("elements", []):
                    name = el.get("tags", {}).get("name") or "Protected area"
                    center = None
                    if "lat" in el and "lon" in el:
                        center = [el["lon"], el["lat"]]
                    elif "center" in el:
                        c = el["center"]; center = [c["lon"], c["lat"]]
                    protected.append({
                        "name": name,
                        "type": "protected_area",
                        "designation": el.get("tags", {}).get("protect_class") or el.get("tags", {}).get("leisure"),
                        "geometry_center": center
                    })
            print("[sensitive][protected-b] status:", r.status_code)
            print("[sensitive][protected-b body:", r.text[:300])
        except Exception as e:
            print("[sensitive][protected-b] failed:", repr(e))

        # ---------- 3) DESALINATION (OSM) ----------
        try:
            overpass = "https://overpass-api.de/api/interpreter"
            q_desal = f"""
            [out:json][timeout:25];
            (
              node(around:{radius_m},{lat},{lon})["man_made"="desalination_plant"];
              way(around:{radius_m},{lat},{lon})["man_made"="desalination_plant"];
              node(around:{radius_m},{lat},{lon})["man_made"="water_works"]["water_works"="desalination"];
              way(around:{radius_m},{lat},{lon})["man_made"="water_works"]["water_works"="desalination"];
            );
            out center tags;
            """
            #r = requests.post(overpass, data=q_desal.encode("utf-8"), timeout=30)
            r = requests.post(
                overpass,
                data={"data": q_prot},
                headers={"Accept": "application/json", "User-Agent": "oil-spill-ims/1.0"},
                timeout=30
            )
            if r.ok:
                data = r.json()
                for el in data.get("elements", []):
                    tags = el.get("tags", {})
                    name = tags.get("name") or "Desalination facility"
                    center = None
                    if "lat" in el and "lon" in el:
                        center = [el["lon"], el["lat"]]
                    elif "center" in el:
                        c = el["center"]; center = [c["lon"], c["lat"]]
                    desal.append({
                        "name": name,
                        "type": "desalination",
                        "geometry_center": center,
                        "operator": tags.get("operator"),
                        "plant_type": tags.get("water_works") or tags.get("man_made")
                    })
            print("[sensitive][desalination] status:", r.status_code)
            print("[sensitive][desalination] body:", r.text[:300])
        except Exception as e:
            print("[sensitive][desalination] failed:", repr(e))

        # ---------- Nearest picks ----------
        nearest = {
            "port": best_one(ports, lat, lon),
            "protected_area": best_one(protected, lat, lon),
            "desalination": best_one(desal, lat, lon)
        }

        print("sensitive_areas nearest",nearest)

        return {
            "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
            "nearest": nearest,
            "lists": {
                "ports": ports,
                "protected_areas": protected,
                "desalination": desal
            }
        }
    except Exception as e:
        return {
        "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "nearest": {"port": None, "protected_area": None, "desalination": None},
        "lists": {"ports": [], "protected_areas": [], "desalination": []},
        "error": str(e)
        }

# If you already have a Flask app, register the route instead of running standalone:
""" if __name__ == "__main__":
    app.run(debug=True) """

