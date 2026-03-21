# service.py  (Option A: surgical fix)
# - Closes each temporary telemetry subscription after one sample to avoid
#   flooding MAVSDK's user-callback queue.
# - Sets explicit telemetry rates once on startup.
# - Keeps your handlers and smoothing logic intact.

import asyncio
import json
import time
import argparse
import contextlib
from time import monotonic
from typing import List, Tuple
from aiohttp import web
from px4_ops import DroneOps
from companion_ops import CompanionOps

# ----- CLI / Config -----
ap = argparse.ArgumentParser()
ap.add_argument("--bind", default="0.0.0.0")
ap.add_argument("--port", type=int, default=8088)
ap.add_argument("--api-key", default="SUPERSECRET")

# IMPORTANT: UI should pass: --sys-addr udp://0.0.0.0:<SIM_PORT>
ap.add_argument("--sys-addr", default="udp://0.0.0.0:14541")

# ✅ NEW: drone id passed in from UI
ap.add_argument("--drone-id", default="PH_UAV_002")

ap.add_argument("--telemetry-url", default=None)
ap.add_argument("--telemetry-hz", type=float, default=2.0, help="Position/in_air update rate (Hz)")
ap.add_argument("--allow-get", action="store_true", help="Enable GET for quick testing")

ap.add_argument("--deploy-pin", type=int, default=18, help="GPIO pin for chemical deploy output")
ap.add_argument("--dry-gpio", action="store_true", help="Do not access real GPIO, just log actions")
ap.add_argument("--deploy-active-low", action="store_true", help="Use active-low logic for deploy pin")

args = ap.parse_args()

API_KEY = args.api_key
ops: "DroneOps | None" = None
companion: "CompanionOps | None" = None
lock = asyncio.Lock()

# ----- Status smoothing -----
_STATUS = {}                # {droneId: {"status": str, "ts": float, "last_seen": float}}
GRACE_AIRBORNE_S = 5.0      # keep airborne-ish status if we saw in_air True within this window
MIN_DWELL_S       = 2.0     # minimum time to stay in a status before switching

def _want(inst_status, armed, in_air, flight_mode):
    """Compute instantaneous status from raw bits (no smoothing)."""
    if in_air:
        fm = (flight_mode or "").upper()
        if fm.startswith("TAKEOFF"):
            return "taking_off"
        if fm in ("LAND", "LANDING"):
            return "landing"
        if fm in ("RETURN_TO_LAUNCH", "RTL"):
            return "rtl"
        return "enroute"
    if armed:
        return "armed"
    return "idle"

def _upgrade_rank(status):
    """Higher means 'more active'; upgrades can apply immediately."""
    order = {
        "idle": 0, "armed": 1, "hover": 2,
        "taking_off": 3, "enroute": 3, "rtl": 3, "landing": 3
    }
    return order.get(status, 0)

def smooth_status(drone_id, inst_status, armed, in_air):
    now = monotonic()
    s = _STATUS.get(drone_id, {"status": "idle", "ts": now, "last_seen": 0.0})

    # Update "airborne last seen" timestamp if currently in_air
    if in_air:
        s["last_seen"] = now

    prev = s["status"]
    can_change = (now - s["ts"]) >= MIN_DWELL_S

    # Don’t drop to ground states if we recently saw in_air True
    if inst_status in ("idle", "armed") and (now - s.get("last_seen", 0.0)) < GRACE_AIRBORNE_S:
        inst_status = prev  # stay airborne/transition status

    # Only commit a change if dwell time satisfied (except upgrades)
    is_upgrade = _upgrade_rank(inst_status) > _upgrade_rank(prev)
    if (can_change or is_upgrade) and inst_status != prev:
        s["status"] = inst_status
        s["ts"] = now

    _STATUS[drone_id] = s
    return s["status"]

# ----- Auth (optional) -----
def require_key(req: web.Request):
    # Uncomment to enforce API key if needed:
    # if req.headers.get("X-API-Key") != API_KEY:
    #     raise web.HTTPUnauthorized(text="bad api key")
    return

# ----- Helper: one sample then close (Option A core fix) -----
async def one_sample(agen, timeout=0.5):
    """
    Pull exactly one item from an async generator returned by MAVSDK telemetry,
    then close the generator to avoid leaking subscriptions.
    """
    try:
        return await asyncio.wait_for(agen.__anext__(), timeout)
    except Exception:
        return None
    finally:
        # Not all async iters implement aclose(); suppress if absent.
        with contextlib.suppress(Exception):
            await agen.aclose()

# ----- CORS middleware -----
@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp

# ----- Handlers -----
async def handle_health(req):
    return web.json_response({"ok": True, "api": req.headers.get("X-API-Key")})

async def handle_status(req):
    d = ops.drone

    # ✅ CHANGED: stop hardcoding drone_id
    drone_id = getattr(args, "drone_id", "UNKNOWN_DRONE")

    pos, armed, in_air, batt, gps, flight_mode = await asyncio.gather(
        one_sample(d.telemetry.position(), 1.0),
        one_sample(d.telemetry.armed(),   1.0),
        one_sample(d.telemetry.in_air(),  1.0),
        one_sample(d.telemetry.battery(), 1.0),
        one_sample(d.telemetry.gps_info(),1.0),
        one_sample(d.telemetry.flight_mode(), 1.0) if hasattr(d.telemetry, "flight_mode") else asyncio.sleep(0, result=None)
    )

    # Coerce booleans: keep None as False for instantaneous calc, but they won’t force a downshift due to smoothing
    armed_b = bool(armed) if armed is not None else False
    in_air_b = bool(in_air) if in_air is not None else False
    fm = str(flight_mode).split(".")[-1] if flight_mode is not None else None

    inst = _want(None, armed_b, in_air_b, fm)
    status = smooth_status(drone_id, inst, armed_b, in_air_b)

    resp = {
        "droneId": drone_id,
        "connected": True,
        "status": status,
        "armed": armed_b if armed is not None else None,
        "in_air": in_air_b if in_air is not None else None,
        "flight_mode": fm,
    }

    if pos:
        resp.update({
            "lat": pos.latitude_deg,
            "lon": pos.longitude_deg,
            "abs_alt_m": pos.absolute_altitude_m,
            "rel_alt_m": pos.relative_altitude_m,
        })
    if batt:
        if getattr(batt, "remaining_percent", None) is not None:
            resp["battery_pct"] = round(batt.remaining_percent * 100.0, 1)
        if getattr(batt, "voltage_v", None) is not None:
            resp["battery_v"] = round(batt.voltage_v, 2)
    if gps:
        resp["satellites_used"] = getattr(gps, "num_satellites", None)
        ft = getattr(gps, "fix_type", None)
        if ft is not None:
            resp["gps_fix"] = str(ft).split(".")[-1].lower()

    return web.json_response(resp)

async def handle_arm_takeoff(req):
    # require_key(req)
    agl = 12.0
    if req.method == "POST":
        try:
            body = await req.json()
            agl = float(body.get("agl", agl))
        except Exception:
            pass
    elif req.method == "GET" and args.allow_get:
        try:
            agl = float(req.query.get("agl", agl))
        except Exception:
            pass
    else:
        raise web.HTTPMethodNotAllowed(req.method, ["POST"] + (["GET"] if args.allow_get else []))

    async with lock:
        await ops.arm_and_takeoff_agl(agl_m=agl)
    return web.json_response({"ok": True, "agl": agl})

async def handle_goto_polygon(req):
    # require_key(req)
    if req.method == "GET" and args.allow_get:
        raise web.HTTPBadRequest(text="Use POST with JSON body for goto_polygon")
    body = await req.json()
    poly = [(float(a), float(b)) for a, b in body["polygon"]]
    agl = float(body.get("agl", 12.0))
    strat = body.get("strategy", "centroid")
    async with lock:
        await ops.goto_polygon_and_hover(poly, agl_m=agl, strategy=strat)
    return web.json_response({"ok": True})

async def handle_rtl(req):
    # require_key(req)
    if req.method == "POST":
        body = await req.json() if req.can_read_body else {}
    elif req.method == "GET" and args.allow_get:
        body = req.query
    else:
        raise web.HTTPMethodNotAllowed(req.method, ["POST"] + (["GET"] if args.allow_get else []))
    use_rtl = str(body.get("use_rtl", "true")).lower() != "false"
    agl = float(body.get("agl", 10.0))
    async with lock:
        await ops.go_home(use_rtl=use_rtl, agl_m=agl)
    return web.json_response({"ok": True})



async def handle_deploy(req):
    # require_key(req)
    if req.method == "POST":
        body = await req.json()
    elif req.method == "GET" and args.allow_get:
        body = req.query
    else:
        raise web.HTTPMethodNotAllowed(req.method, ["POST"] + (["GET"] if args.allow_get else []))

    dur = float(body.get("duration_s", 5.0))
    report = body.get("report_url")

    if companion is None:
        raise web.HTTPServiceUnavailable(text="CompanionOps not initialized")

    async with lock:
        await companion.deploy_chemicals(duration_s=dur)

    return web.json_response({
        "ok": True,
        "duration_s": dur,
        "report_url": report
    })

# ----- Lifecycle -----
async def on_start(app):
    global ops, companion

    ops = DroneOps(
        sys_addr=args.sys_addr,
        telemetry_post_url=args.telemetry_url,
        telemetry_hz=args.telemetry_hz
    )
    await ops.connect()

    companion = CompanionOps(
        deploy_pin=args.deploy_pin,
        active_high=not args.deploy_active_low,
        dry_run=args.dry_gpio
    )
    await companion.connect()

    # Set explicit telemetry rates once
    t = ops.drone.telemetry
    try:
        await asyncio.gather(
            t.set_rate_position(args.telemetry_hz),
            t.set_rate_in_air(args.telemetry_hz),
            t.set_rate_battery(1.0),
            t.set_rate_gps_info(1.0),
        )
    except Exception:
        pass

    await ops.capture_home_from_current()
    await ops.start_telemetry_stream()



async def on_cleanup(app):
    global ops, companion

    if ops is not None:
        await ops.stop_telemetry_stream()

    if companion is not None:
        await companion.close()

# ----- App wiring -----
app = web.Application(middlewares=[cors_mw])
app.on_startup.append(on_start)
app.on_cleanup.append(on_cleanup)

routes = [
    web.route("GET", "/healthz", handle_health),
    web.route("GET", "/status", handle_status),
    web.route("POST", "/arm_takeoff", handle_arm_takeoff),
    web.route("POST", "/goto_polygon", handle_goto_polygon),
    web.route("POST", "/rtl", handle_rtl),
    web.route("POST", "/deploy", handle_deploy),
    web.route("OPTIONS", "/{tail:.*}", lambda r: web.Response()),
]
if args.allow_get:
    routes += [
        web.route("GET", "/arm_takeoff", handle_arm_takeoff),
        web.route("GET", "/rtl", handle_rtl),
        web.route("GET", "/deploy", handle_deploy),
    ]
app.add_routes(routes)

web.run_app(app, host=args.bind, port=args.port)
