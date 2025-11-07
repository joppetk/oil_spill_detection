import asyncio, math, time
from typing import List, Tuple, Optional
from mavsdk import System
import aiohttp

EARTH_R = 6371000.0  # meters

def _deg2rad(d): return d * math.pi / 180.0
def _rad2deg(r): return r * 180.0 / math.pi

def _ll_to_xy(lat, lon, lat0, lon0):
    """Equirectangular projection around (lat0, lon0). Good for small polygons."""
    lat, lon, lat0, lon0 = map(_deg2rad, (lat, lon, lat0, lon0))
    x = (lon - lon0) * math.cos(lat0) * EARTH_R
    y = (lat - lat0) * EARTH_R
    return x, y

def _xy_to_ll(x, y, lat0, lon0):
    lat0r, lon0r = map(_deg2rad, (lat0, lon0))
    lat = y / EARTH_R + lat0r
    lon = x / (EARTH_R * math.cos(lat0r)) + lon0r
    return _rad2deg(lat), _rad2deg(lon)

def _nearest_point_on_segment(px, py, ax, ay, bx, by):
    """Nearest point from P to segment AB in XY; returns (nx, ny, t in [0,1])."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    vv = vx*vx + vy*vy
    if vv == 0.0:
        return ax, ay, 0.0
    t = max(0.0, min(1.0, (wx*vx + wy*vy) / vv))
    nx, ny = ax + t*vx, ay + t*vy
    return nx, ny, t

def nearest_point_on_polygon(lat, lon, poly: List[Tuple[float,float]]):
    """Nearest point on polygon boundary (lat,lon) to (lat,lon)."""
    # reference = polygon centroid (rough)
    c_lat = sum(p[0] for p in poly)/len(poly)
    c_lon = sum(p[1] for p in poly)/len(poly)
    px, py = _ll_to_xy(lat, lon, c_lat, c_lon)

    best_d2, best_xy = float('inf'), (None, None)
    for i in range(len(poly)):
        (latA, lonA) = poly[i]
        (latB, lonB) = poly[(i+1) % len(poly)]
        ax, ay = _ll_to_xy(latA, lonA, c_lat, c_lon)
        bx, by = _ll_to_xy(latB, lonB, c_lat, c_lon)
        nx, ny, _ = _nearest_point_on_segment(px, py, ax, ay, bx, by)
        d2 = (nx-px)**2 + (ny-py)**2
        if d2 < best_d2:
            best_d2, best_xy = d2, (nx, ny)
    nlat, nlon = _xy_to_ll(best_xy[0], best_xy[1], c_lat, c_lon)
    return nlat, nlon

def polygon_centroid(poly: List[Tuple[float,float]]):
    """Area-weighted centroid via planar XY shoelace (fine for small areas)."""
    c_lat = sum(p[0] for p in poly)/len(poly)
    c_lon = sum(p[1] for p in poly)/len(poly)
    xy = [_ll_to_xy(lat, lon, c_lat, c_lon) for lat, lon in poly]
    A = 0.0; Cx = 0.0; Cy = 0.0
    for i in range(len(xy)):
        (x1,y1) = xy[i]; (x2,y2) = xy[(i+1)%len(xy)]
        cross = x1*y2 - x2*y1
        A += cross
        Cx += (x1 + x2) * cross
        Cy += (y1 + y2) * cross
    A *= 0.5
    if abs(A) < 1e-9:
        # degenerate; fallback to average
        return c_lat, c_lon
    Cx /= (6*A); Cy /= (6*A)
    return _xy_to_ll(Cx, Cy, c_lat, c_lon)

class DroneOps:
    def __init__(self, sys_addr="udp://0.0.0.0:14540",
                 telemetry_post_url: Optional[str]=None,
                 telemetry_hz: float=2.0):
        self.drone = System()
        self.sys_addr = sys_addr
        self.telemetry_post_url = telemetry_post_url
        self.telemetry_hz = telemetry_hz
        self._home_llh = None  # (lat, lon, amsl)
        self._telemetry_task = None
        self._http = None

    async def connect(self):
        await self.drone.connect(system_address=self.sys_addr)
        async for s in self.drone.core.connection_state():
            if s.is_connected:
                print("[OK] Connected")
                break
        async for h in self.drone.telemetry.health():
            if h.is_global_position_ok:
                print("[OK] Global position OK")
                break

    async def capture_home_from_current(self):
        # Wait until telemetry.home() gives something reasonable
        got_home = False
        async for hp in self.drone.telemetry.home():
            self._home_llh = (hp.latitude_deg, hp.longitude_deg, hp.absolute_altitude_m)
            got_home = True
            print(f"[HOME] {self._home_llh}")
            break
        if not got_home:
            # fallback: use current position as "software home"
            async for pos in self.drone.telemetry.position():
                self._home_llh = (pos.latitude_deg, pos.longitude_deg, pos.absolute_altitude_m)
                print(f"[HOME*] (fallback) {self._home_llh}")
                break

    async def start_telemetry_stream(self):
        if not self.telemetry_post_url:
            return
        self._http = aiohttp.ClientSession()
        period = 1.0 / max(0.1, self.telemetry_hz)
        async def _run():
            print(f"[TELEM] streaming to {self.telemetry_post_url} @ {self.telemetry_hz} Hz")
            while True:
                try:
                    pos = await self.drone.telemetry.position().__anext__()
                    
                    hdg = await self.drone.telemetry.heading().__anext__()
                    print(f"[TELEM] current position {pos} current heading {hdg} ")
                    msg = {
                        "ts": time.time(),
                        "lat": pos.latitude_deg,
                        "lon": pos.longitude_deg,
                        "abs_alt_m": pos.absolute_altitude_m,
                        "rel_alt_m": pos.relative_altitude_m,
                        "heading_deg": hdg.heading_deg
                    }
                    await self._http.post(self.telemetry_post_url, json=msg, timeout=2)
                except Exception as e:
                    # don’t crash on network hiccups
                    print(f"[TELEM] warn: {e}")
                await asyncio.sleep(period)
        self._telemetry_task = asyncio.create_task(_run())

    async def stop_telemetry_stream(self):
        if self._telemetry_task:
            self._telemetry_task.cancel()
            self._telemetry_task = None
        if self._http:
            await self._http.close()
            self._http = None

    async def arm_and_takeoff_agl(self, agl_m: float):
        # Ensure we have a home reference
        if not self._home_llh:
            await self.capture_home_from_current()
        await self.drone.action.set_takeoff_altitude(max(agl_m, 5.0))
        print("[ACT] arming…")
        await self.drone.action.arm()
        print("[ACT] takeoff…")
        await self.drone.action.takeoff()
        # wait a bit
        await asyncio.sleep(3)

    async def goto_amsl(self, lat, lon, amsl, yaw_deg: float = 0.0):
        print(f"[NAV] goto {lat:.7f},{lon:.7f} @ {amsl:.1f} m AMSL")
        await self.drone.action.goto_location(lat, lon, amsl, yaw_deg)

    async def goto_polygon_and_hover(self, polygon: List[Tuple[float,float]],
                                     agl_m: float = 12.0,
                                     strategy: str = "centroid"):
        """
        strategy:
          - 'centroid' : enter at nearest edge point, then hover at centroid
          - 'edge'     : enter & hover at nearest edge point
        """
        # get current position (entry point is nearest from here)
        async for pos in self.drone.telemetry.position():
            cur = pos; break

        # entry: nearest boundary point
        entry_lat, entry_lon = nearest_point_on_polygon(cur.latitude_deg,
                                                        cur.longitude_deg,
                                                        polygon)
        # hover target:
        if strategy == "centroid":
            hov_lat, hov_lon = polygon_centroid(polygon)
        else:
            hov_lat, hov_lon = entry_lat, entry_lon

        # AGL→AMSL
        if not self._home_llh:
            await self.capture_home_from_current()
        home_amsl = self._home_llh[2]
        entry_amsl = home_amsl + agl_m
        hover_amsl = home_amsl + agl_m

        # fly
        await self.goto_amsl(entry_lat, entry_lon, entry_amsl, 0.0)
        print("[NAV] entering polygon…")
        # small settle
        await asyncio.sleep(1.5)
        if (hov_lat, hov_lon) != (entry_lat, entry_lon):
            await self.goto_amsl(hov_lat, hov_lon, hover_amsl, 0.0)
            print("[NAV] hovering above polygon center")
        else:
            print("[NAV] hovering at polygon edge (nearest)")

    async def go_home(self, use_rtl=True, agl_m: float = 10.0):
        if use_rtl:
            print("[RTL] return-to-launch…")
            await self.drone.action.return_to_launch()
        else:
            if not self._home_llh:
                await self.capture_home_from_current()
            lat, lon, amsl = self._home_llh
            target_amsl = amsl + agl_m
            await self.goto_amsl(lat, lon, target_amsl, 0.0)

    async def deploy_chemicals(self, duration_s: float = 3.0, report_url: Optional[str] = None):
        """
        Simulated deploy:
         - hold position for duration_s
         - optionally POST an event to your server
        """
        print(f"[PAYLOAD] deploying chemicals for {duration_s}s…")
        t0 = time.time()
        while time.time() - t0 < duration_s:
            await asyncio.sleep(0.2)
        if report_url:
            try:
                if not self._http:
                    self._http = aiohttp.ClientSession()
                await self._http.post(report_url, json={"ts": time.time(), "event": "deploy"})
            except Exception as e:
                print(f"[PAYLOAD] report warn: {e}")
        print("[PAYLOAD] done")
