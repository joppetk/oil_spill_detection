# infer_utils.py
import numpy as np
import torch
import rasterio
from skimage.measure import label
#from shapely.geometry import shape
from shapely.ops import unary_union
from rasterio.features import shapes
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform
from shapely.geometry import box as shp_box
from rasterio.transform import from_gcps
from shapely.geometry import shape as shp_shape, mapping as shp_mapping
# from pyproj import Transformer, CRS


# ---------- model holder (set from outside) ----------
_device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
_model  = None

def set_model(model):
    """Call this once after you load weights in your script."""
    global _model
    _model = model.to(_device).eval()

# ---------- normalization ----------
def robust_norm(img2):  # H,W,2 (dB)
    x = img2.astype(np.float32, copy=True)
    for c in range(x.shape[-1]):
        v = x[..., c]
        # consider only finite values
        fm = np.isfinite(v)
        if not np.any(fm):
            # whole channel is non-finite -> zero it and continue
            x[..., c] = 0.0
            continue

        v_f = v[fm]
        # robust clip between 2–98 percentiles
        lo, hi = np.percentile(v_f, [2, 98])
        # handle degenerate (flat) ranges
        if not np.isfinite(lo): lo = np.nanmin(v_f)
        if not np.isfinite(hi): hi = np.nanmax(v_f)
        if hi <= lo:
            lo = np.min(v_f); hi = np.max(v_f)
            if hi <= lo:  # still degenerate
                x[..., c] = 0.0
                continue

        v_clipped = np.clip(v, lo, hi)

        # robust center/scale using median and MAD (finite only)
        m   = np.median(v_f)
        mad = np.median(np.abs(v_f - m))
        scale = max(1.4826 * mad, 1e-6)  # avoid zero scale
        x[..., c] = (v_clipped - m) / scale

    # replace any remaining non-finite
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(x, -6.0, 6.0).astype(np.float32)


# ---------- tiling helpers ----------
def _hann2d(n):
    w = np.hanning(n).astype(np.float32)
    W = np.outer(w, w)
    m = W.max() if W.size else 1.0
    return (W / m).astype(np.float32)

def _tile_reflect(img, top, left, tile):
    H, W, _ = img.shape
    h = min(tile, H - top)
    w = min(tile, W - left)
    patch = img[top:top+h, left:left+w, :]
    if h < tile or w < tile:
        patch = np.pad(patch, ((0, tile-h), (0, tile-w), (0, 0)), mode='reflect')
    return patch, h, w

def _tta_predict(x):
    """3-view TTA (id, H-flip, V-flip)."""
    p0 = torch.sigmoid(_model(x))
    p1 = torch.sigmoid(_model(torch.flip(x, dims=[3]))).flip(dims=[3])
    p2 = torch.sigmoid(_model(torch.flip(x, dims=[2]))).flip(dims=[2])
    return (p0 + p1 + p2) / 3.0

# ---------- main inference ----------
def predict_full_image_weighted(tif_path, tile=512, stride=448, thr=0.5, min_obj=0, tta=False, return_geo=True):
    """
    Returns (prob, mask[, transform, crs]).
    - If the source has 1 band, it duplicates it to 2 channels.
    - Set `thr` to your operating threshold (e.g., 0.70).
    - `min_obj` kept for compat (cleaning omitted here—do downstream if needed).
    """
    if _model is None:
        raise RuntimeError("infer_utils: model not set. Call set_model(model) after loading weights.")

    # read 1 or 2 bands
    with rasterio.open(tif_path) as src:
        count = src.count
        print(f"[infer]source count is {count}" )
        if count >= 2:
            v1 = src.read(1, masked=True).filled(np.nan).astype(np.float32)
            v2 = src.read(2, masked=True).filled(np.nan).astype(np.float32)
            img = np.stack([v1, v2], axis=-1)
        elif count == 1:
            v = src.read(1, masked=True).filled(np.nan).astype(np.float32)
            img = np.stack([v, v], axis=-1)  # duplicate single band
        else:
            raise ValueError(f"{tif_path}: expected 1 or 2 bands, found {count}")
        transform = src.transform
        crs = src.crs
        gcps, gcps_crs = src.gcps

        # NEW: pick a transform that is usable by polygonization
        if gcps and len(gcps) > 0:
            # approximate affine from tie-points (good enough for shapes())
            poly_transform = from_gcps(gcps)
            poly_crs = gcps_crs
        else:
            poly_transform = transform
            poly_crs = crs

        # NEW: Normalize transform to ensure negative y-scale (top-down orientation)
        original_e = poly_transform.e
        if poly_transform.e > 0:
            H, W, _ = img.shape
            poly_transform = rasterio.transform.Affine(
                poly_transform.a, poly_transform.b, poly_transform.c,
                poly_transform.d, -poly_transform.e, poly_transform.f + (H - 1) * poly_transform.e
            )

    H, W, _ = img.shape
    win = _hann2d(tile)
    num = np.zeros((H, W), np.float32)
    den = np.zeros((H, W), np.float32)
    #print(f"[infer]H = {H}, W = {W}" )

    for top in range(0, H, stride):
        for left in range(0, W, stride):
            patch, h, w = _tile_reflect(img, top, left, tile)
            x = robust_norm(patch)
            x = torch.from_numpy(np.moveaxis(x, -1, 0)[None]).float().to(_device)
            with torch.no_grad(), torch.amp.autocast(device_type='cuda', enabled=torch.cuda.is_available()):
                p = (_tta_predict(x) if tta else torch.sigmoid(_model(x)))[0, 0].detach().cpu().numpy()
            w2 = win[:h, :w]
            num[top:top+h, left:left+w] += p[:h, :w] * w2
            den[top:top+h, left:left+w] += w2

    prob = num / np.maximum(den, 1e-6)
    mask = (prob >= float(thr)).astype(np.uint8)

    # NEW: Flip prob and mask if the original transform had positive y-scale
    if original_e > 0:
        prob = np.flipud(prob)
        mask = np.flipud(mask)

    return (prob, mask, poly_transform, poly_crs) if return_geo else (prob, mask)

""" def polygons_with_confidence(prob, mask, transform, crs,
                             min_area_m2=None, lat_hint=24.5, tif_path=None, debug=False):
    
    Robust polygonizer:
      • polygonize directly from the binary mask (no scikit-image label dependency)
      • compute mean prob per polygon (approx)
      • filter by area in m² (handles projected vs geographic)
    
    import math
    from rasterio.features import shapes
    from shapely.geometry import shape as shp_shape, mapping as shp_mapping

    # Ensure simple, contiguous 0/1 mask
    m8 = np.ascontiguousarray((mask > 0).astype(np.uint8))
    onpx = int(m8.sum())
    if debug:
        print(f"[poly] mask_on_pixels={onpx}")
    if onpx == 0:
        return {"type": "FeatureCollection", "features": []}

    # Polygonize directly from mask==1
    polys = []
    for geom, val in shapes(m8, mask=(m8 == 1), transform=transform):
        if val != 1:
            continue
        p = shp_shape(geom)
        if not p.is_empty and p.area > 0:
            polys.append(p)

    if debug:
        print(f"[poly] raw_polygons={len(polys)}")

    if not polys:
        return {"type": "FeatureCollection", "features": []}

    # Pixel area (m²)
    # replace your px_area_m2 block with this
    if crs and getattr(crs, "is_projected", False) and abs(transform.a) > 0 and abs(transform.e) > 0:
        px_area_m2 = abs(transform.a) * abs(transform.e)
    else:
        # fallback via bounds if affine step is unusable
        try:
            import rasterio
            from rasterio.warp import transform_bounds
            # infer width/height from prob/mask
            H, W = mask.shape
            with rasterio.open(str(tif_path)) as src:  # or pass bounds/size in args
                b = transform_bounds(src.crs, "EPSG:4326", src.bounds.left, src.bounds.bottom,
                                    src.bounds.right, src.bounds.top, densify_pts=8)
            lat_c = 0.5*(b[1]+b[3])
            m_per_deg_lat = 111_132.0
            m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat_c))
            px_w_deg = (b[2]-b[0]) / float(W)
            px_h_deg = (b[3]-b[1]) / float(H)
            px_area_m2 = (m_per_deg_lon * px_w_deg) * (m_per_deg_lat * px_h_deg)
        except Exception:
            px_area_m2 = 0.0  # last resort; treat as no filter outside


    # Area filter + confidence
    feats = []
    # Approximate mean prob by sampling prob where mask==1 (fast & stable)
    # If you want per-polygon means, rasterize each polygon—slower.
    global_mean = float(prob[m8 == 1].mean()) if onpx else 0.0
    min_area_m2 = float(min_area_m2) if min_area_m2 is not None else 0.0

    for p in polys:
        area_m2 = p.area if (crs is not None and getattr(crs, "is_projected", False)) else p.area * px_area_m2
        if area_m2 < min_area_m2:
            continue
        feats.append({
            "type": "Feature",
            "properties": {"class": "probable", "confidence": round(global_mean, 3)},
            "geometry": shp_mapping(p)
        })

    if debug:
        print(f"[poly] kept_polygons={len(feats)}  px_area_m2≈{px_area_m2:.2f}  min_area_m2={min_area_m2}")

    return {"type": "FeatureCollection", "features": feats} """


def polygons_with_confidence(prob, mask, transform, crs,
                             min_area_m2=None, lat_hint=24.5,
                             min_area_px=None, debug=False):
    """
    Robust polygonizer:
      1) optionally filter by pixel-count using connected components (min_area_px)
      2) polygonize (rasterio.features.shapes) on the filtered binary mask
      3) optional m² filter (min_area_m2) using pixel->meter conversion
    """
    m8 = np.ascontiguousarray((mask > 0).astype(np.uint8))
    onpx = int(m8.sum())
    if debug:
        print(f"[poly] mask_on_pixels={onpx}")
    if onpx == 0:
        return {"type": "FeatureCollection", "features": []}

    # -------------------------
    # 1) pixel-count filtering

    # What it does:
    #Uses scipy.ndimage.label() to find connected components — contiguous regions of "on" pixels.
    #Measures the size (number of pixels) of each component.
    #Keeps only those components that meet a minimum size threshold (min_area_px).
    #Why it matters:
    #Filters out noise — tiny specks that might be false positives.
    #Ensures only meaningful regions are converted to polygons.
    #Example:
    #If min_area_px = 20, and your mask has a bunch of tiny blobs under 20 pixels, they’ll be removed before polygonization.

    # -------------------------
    if min_area_px is not None and min_area_px > 1:
        lbl = label(m8.astype(bool), connectivity=1)
        # bincount with label 0 ignored
        sizes = np.bincount(lbl.ravel())
        keep = np.zeros_like(sizes, dtype=bool)
        keep_idx = np.where(sizes >= int(min_area_px))[0]
        keep[keep_idx] = True
        keep[0] = False
        m8 = ((keep[lbl]).astype(np.uint8))
        if debug:
            kept = int(m8.sum())
            comps = (sizes[1:] >= int(min_area_px)).sum()
            print(f"[poly] after px-filter: comps>={min_area_px}px = {comps}, on_pixels={kept}")
        if int(m8.sum()) == 0:
            return {"type": "FeatureCollection", "features": []}

    # -------------------------
    # 2) polygonize mask==1
    #This step converts the filtered binary mask into vector shapes.
    #What it does:
    #Uses rasterio.features.shapes() to trace the boundaries of regions where mask==1.
    #Converts those regions into geometric polygons (e.g., shapely.geometry.Polygon).
    #Only keeps polygons that are non-empty and have positive area.
    #Why it matters:
    #Translates pixel-based detections into geospatial vector data.
    #Enables downstream tasks like area filtering, spatial joins, or GeoJSON export
    #Example:
    #If your mask has a large blob of connected pixels, this step will turn it into a clean polygon that can be mapped, measured, or stored.

    # -------------------------
    polys = []
    for geom, val in shapes(m8, mask=(m8 == 1), transform=transform):
        if val != 1:
            continue
        p = shp_shape(geom)
        if not p.is_empty and p.area > 0:
            polys.append(p)
    if debug:
        print(f"[poly] raw_polygons={len(polys)}")
    if not polys:
        return {"type": "FeatureCollection", "features": []}

    # -------------------------
    # 3) optional m² gating
    # -------------------------
    # compute per-pixel area safely (meters^2)
    px_area_m2 = None
    try:
        if crs is not None and getattr(crs, "is_projected", False) and abs(transform.a) > 0 and abs(transform.e) > 0:
            px_area_m2 = abs(transform.a) * abs(transform.e)
        else:
            import math
            m_per_deg_lat = 111_132.0
            m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat_hint))
            px_w_deg = abs(transform.a)
            px_h_deg = abs(transform.e)
            if px_w_deg == 0 or px_h_deg == 0:
                px_area_m2 = 0.0
            else:
                px_area_m2 = (m_per_deg_lon * px_w_deg) * (m_per_deg_lat * px_h_deg)
    except Exception:
        px_area_m2 = 0.0

    feats = []
    global_mean = float(prob[m8 == 1].mean()) if onpx else 0.0
    area_cut_m2 = float(min_area_m2) if (min_area_m2 is not None) else 0.0

    for p in polys:
        if area_cut_m2 > 0 and px_area_m2 > 0:
            # geographic polygons from shapes() have area in deg^2; multiply by px_area
            area_m2 = p.area * px_area_m2
            if area_m2 < area_cut_m2:
                continue
        feats.append({
            "type": "Feature",
            "properties": {"class": "probable", "confidence": round(global_mean, 3)},
            "geometry": shp_mapping(p)
        })

    if debug:
        print(f"[poly] kept_polygons={len(feats)}  px_area_m2~{(px_area_m2 or 0):.3f}  min_area_m2={area_cut_m2}")
    return {"type": "FeatureCollection", "features": feats}