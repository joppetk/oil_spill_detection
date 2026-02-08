# python-poller/scan_download_asf_b.py
import os, sys
from pathlib import Path
from datetime import datetime, timedelta

import asf_search as asf
from requests.adapters import HTTPAdapter, Retry

# ---- config via env ----
AOI   = os.getenv("OPS_AOI_WKT")              # required
OUT   = Path(os.getenv("ASF_OUT", "python-poller/data/s1"))
LIMIT = int(os.getenv("ASF_LIMIT", "3"))      # max new downloads per scan
DAYS  = int(os.getenv("ASF_DAYS",  "7"))      # lookback window in days
EDL_USER = os.getenv("EDL_USER")              # optional; if omitted, _netrc is used
EDL_PASS = os.getenv("EDL_PASS")              # optional
EDL_TOKEN="YOUR-TOKEN-HERE"
EDL_TOKEN="eyJ0eXAiOiJKV1QiLCJvcmlnaW4iOiJFYXJ0aGRhdGEgTG9naW4iLCJzaWciOiJlZGxqd3RwdWJrZXlfb3BzIiwiYWxnIjoiUlMyNTYifQ.eyJ0eXBlIjoiVXNlciIsInVpZCI6ImpvcHBldC5xdWlub25lcyIsImV4cCI6MTc3MzMzMDUwNywiaWF0IjoxNzY4MTQ2NTA3LCJpc3MiOiJodHRwczovL3Vycy5lYXJ0aGRhdGEubmFzYS5nb3YiLCJpZGVudGl0eV9wcm92aWRlciI6ImVkbF9vcHMiLCJhY3IiOiJlZGwiLCJhc3N1cmFuY2VfbGV2ZWwiOjN9.Dn7KzG-bpUhfuqffZHhT82ZpthrcanD-FkXSnGI1f8g-mbaEkQOOQHdJ7pXosRRCXPxh4WLTXCPHV8bWBXw5VpgUStaKcwUZUGtnsoWyPKsDw7Ae6xzYfLy5vQhBVjf6eCobbdxaXKm2z_yKSUd2uKapuNRcmxcYZo_KcOdz2MpqodgSuVf5vasDN_3lxc5ClgA4r_jAkIK6GewMLlwwjFWYqYiFTuOEhN-ogZ5_i4R1ed-lfMn6ruqfSONalfO5jI3yaomz0KkmmwM-Xbc92gXHfpmiroTSVlZd7YRjC6xA7Wvu7QmdR8bFmsItwWQKOJvUxAkbyIPYU5yV8eEDNQ"
#Expires at:  12- 3-2026 9:48pm EST


def mk_session():
    # Use ASFSession so auth flows match asf_search expectations.
    sess = asf.ASFSession()
    

    sess = asf.ASFSession().auth_with_token(EDL_TOKEN)
    retries = Retry(
        total=5, connect=5, read=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        raise_on_status=False,
    )
    sess.mount("https://", HTTPAdapter(max_retries=retries, pool_maxsize=8))
    sess.trust_env = True  # honors proxy env and _netrc
    sess.headers.update({"User-Agent": "oilspill-poller/1.0"})
    return sess

def product_url(p):
    return (p.properties.get("url")
         or p.properties.get("downloadUrl")
         or p.properties.get("fileURL"))

def expected_size(session, url):
    r = session.head(url, allow_redirects=True, timeout=(15, 60))
    r.raise_for_status()
    return int(r.headers.get("Content-Length", "0") or 0)

def main():
    if not AOI:
        print("[asf download] ERROR: OPS_AOI_WKT not set", file=sys.stderr)
        sys.exit(2)

    OUT.mkdir(parents=True, exist_ok=True)

    start = (datetime.utcnow() - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = "NOW"
    print(f"[asf download] Scan AOI ... window {start} -> {end}")

    # ---- search ----
    try:
        results = asf.search(
            intersectsWith=AOI,
            platform=[asf.PLATFORM.SENTINEL1],
            processingLevel=asf.PRODUCT_TYPE.GRD_HD,
            start=start, end=end, maxResults=200,
        )
        items = sorted(results, key=lambda p: p.properties.get("startTime", ""), reverse=True)
        print(f"[asf download] Results: {len(items)}")
    except Exception as e:
        print(f"[asf download] Error: search failed: {e}", file=sys.stderr)
        sys.exit(1)

    sess = mk_session()
    found = 0
    downloaded = 0

    for p in items:
        name = p.properties.get("sceneName") or p.properties.get("fileID")
        if not name:
            print("[asf download] Error: product has no sceneName/fileID; skipping", file=sys.stderr)
            continue

        url = product_url(p)
        if not url:
            print(f"[asf download] Error: no download URL for {name}", file=sys.stderr)
            continue

        dst = OUT / f"{name}.zip"

        # ---- check remote size so we can detect partials
        remote = 0
        try:
            remote = expected_size(sess, url)
        except Exception as e:
            print(f"[asf download] Warning: Head failed for {name}: {e}; continuing without size check")

        if dst.exists():
            local = dst.stat().st_size
            if remote and local == remote:
                print(f"[asf download] Skip existing complete: {name} ({local} bytes)")
                continue
            else:
                print(f"[asf download] Partial or unknown size for {name}: local={local}, remote={remote}; deleting and re-downloading")
                try:
                    dst.unlink()
                except Exception as e:
                    print(f"[asf download] Warning: could not delete {dst}: {e}")

        # ---- download via asf_search built-in ----
        found += 1
        print(f"[asf download] FOUND NEW SCENE: {name}")
        print(f"[asf download] Downloading {name} -> {dst}")
        try:
            asf.download_url(url=url, path=str(OUT), session=sess)  # saves with original filename
            # verify size when known
            if remote:
                new_local = dst.stat().st_size if dst.exists() else 0
                if new_local != remote:
                    raise IOError(f"size mismatch after download: have {new_local}, expected {remote}")
            print(f"[asf download] Downloaded: {dst}")
            downloaded += 1
        except Exception as e:
            print(f"[asf download] Error: download failed for {name}: {e}", file=sys.stderr)

        if downloaded >= LIMIT:
            print(f"[asf download] Limit reached: {LIMIT}")
            break

    print(f"[asf download] Done: found={found} downloaded={downloaded}")
    sys.exit(0)

if __name__ == "__main__":
    main()

