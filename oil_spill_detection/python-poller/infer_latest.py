# python-poller/infer_latest.py
import os, sys, json, argparse, math
from pathlib import Path
import numpy as np, torch, rasterio
from shapely.geometry import shape, Polygon, mapping
from shapely.wkt import loads as load_wkt
from rasterio.mask import mask as rio_mask
import json
from shapely.ops import transform as shp_transform
from shapely.geometry import box as shp_box
# from pyproj import Transformer, CRS
from rasterio.warp import transform_geom
from affine import Affine
from rasterio.warp import transform_bounds
from torchvision.models.segmentation import deeplabv3_resnet50
from torchvision.models.segmentation import deeplabv3_resnet101
import torch.nn as nn


# import your earlier inference utilities
from infer_utils import robust_norm, predict_full_image_weighted, polygons_with_confidence, set_model

def raster_center_lat(path):
    with rasterio.open(path) as src:
        b = src.bounds; crs = src.crs
        if crs and crs.to_string().upper() != "EPSG:4326":
            xmin, ymin, xmax, ymax = transform_bounds(crs, "EPSG:4326", b.left, b.bottom, b.right, b.top, densify_pts=8)
        else:
            xmin, ymin, xmax, ymax = b.left, b.bottom, b.right, b.top
    return 0.5 * (ymin + ymax)



def read_georef(path):
    """Return dict for writing georeferenced outputs that match the source."""
    with rasterio.open(path) as src:
        crs = src.crs
        gcps, gcps_crs = src.gcps
        has_gcps = bool(gcps)
        has_affine = (src.transform != Affine.identity)
        if not crs:
            raise RuntimeError(f"{path} has no CRS")
        if has_gcps:
            return {"mode":"gcps", "crs": gcps_crs, "gcps": gcps}
        if has_affine:
            return {"mode":"affine", "crs": crs, "transform": src.transform}
        raise RuntimeError(f"{path} has no valid georeferencing (no gcps and identity transform)")


def bbox_from_fc(fc):
    xs, ys = [], []
    for f in fc.get("features", []):
        g = f.get("geometry")
        if not g: continue
        if g["type"] == "Polygon":
            for x,y in g["coordinates"][0]:
                xs.append(x); ys.append(y)
        elif g["type"] == "MultiPolygon":
            for poly in g["coordinates"]:
                for x,y in poly[0]:
                    xs.append(x); ys.append(y)
    if not xs: return None
    return [min(xs), min(ys), max(xs), max(ys)]

def clean_mask(mask_in: np.ndarray, min_obj: int = 500) -> np.ndarray:
    try:
        # scikit-image >=0.20 uses 'footprint'; older used 'selem'
        from skimage.morphology import remove_small_objects, binary_opening, disk
        m = (mask_in.astype(np.uint8) > 0)
        m = remove_small_objects(m, min_size=min_obj)
        m = binary_opening(m, footprint=disk(2))
        return m.astype(np.uint8)
    except Exception as e:
        print(f"[post] warn: mask cleanup skipped ({e})")
        return (mask_in > 0).astype(np.uint8)
    
def normalize_2ch_numpy(img2, profile):
    """
    img2: (H,W,2) float32 in native dB
    profile: 'robust' for UNet (your robust_norm),
             'deeplab_clip' for DeepLab (your training normalization)
    """
    if profile == "robust":
        x = robust_norm(img2)                         # your existing robust normalization
        x = np.moveaxis(x, -1, 0)[None].astype(np.float32)  # (1,2,H,W)
        return torch.from_numpy(x)

    elif profile == "deeplab_clip":
        # your DeepLab training normalization:
        # clip to [-50, 5] dB, then affine scale around -22.5 with width 12.5
        x = np.clip(img2, -50.0, 5.0).astype(np.float32)
        x = (x - (-22.5)) / 12.5
        x = np.moveaxis(x, -1, 0)[None].astype(np.float32)  # (1,2,H,W)
        return torch.from_numpy(x)

    else:
        raise ValueError(f"unknown normalization profile: {profile}")

@torch.no_grad()
def predict_prob_tile(model, x4d_cpu, device, out_mode):
    """
    x4d_cpu: torch.FloatTensor (1,2,H,W) on CPU
    out_mode: 'sigmoid' (UNet 1ch) or 'softmax2' (DeepLab 2-class)
    returns: numpy (H,W) float32 in [0,1]
    """
    x = x4d_cpu.to(device, non_blocking=True)
    if out_mode == "sigmoid":
        logits = model(x)                    # UNet forward returns logits (N,1,H,W)
        prob = torch.sigmoid(logits)[:,0]    # (N,H,W)
    elif out_mode == "softmax2":
        logits = model(x)["out"]             # DeepLab dict output → (N,2,H,W)
        prob = torch.softmax(logits, dim=1)[:,1]  # class-1 prob (oil)
    else:
        raise ValueError(out_mode)
    return prob[0].detach().float().cpu().numpy()

def build_and_load_model(model_name, ckpt_path, device, num_classes=1):
        if model_name == "unet_b48_v1":
            from unet_b48 import UNet
            m = UNet(in_ch=2, out_ch=1, base=48).to(device)
            ckpt = torch.load(ckpt_path, map_location=device)
            # your UNet checkpoints saved as {"model": state_dict}
            m.load_state_dict(ckpt["model"], strict=True)
            return m, "sigmoid", "robust"

        elif model_name == "unet_densenet":
            from unet_densenet import UNetDictOut
            m = UNetDictOut(in_ch=2,
                            num_classes=2,
                            encoder_name="densenet201",
                            encoder_weights="imagenet",
                            dropout_p=0.0,
                            )
            
            m = m.to(device)
            ckpt  = torch.load(ckpt_path, map_location=device, weights_only=False)
            state = ckpt.get("state_dict", ckpt)
            state = {k.replace("module.", ""): v for k,v in state.items()}
            m.load_state_dict(state, strict=True)
            return m, "softmax2", "deeplab_clip"

        elif model_name == "deeplabv3_v1":
            # DeepLabV3-ResNet50 head with 2 classes (background=0, oil=1)
            # m = deeplabv3_resnet50(weights=None, num_classes=2)
            m = deeplabv3_resnet101(weights=None, num_classes=2)
            # swap first conv to accept 2 channels (VV, VH)
            # torchvision version differences: conv1 may be at backbone.conv1 OR backbone.body.conv1
            conv = None
            if hasattr(m.backbone, "conv1"):

                
                conv = m.backbone.conv1
                m.backbone.conv1 = nn.Conv2d(2, conv.out_channels, kernel_size=conv.kernel_size,
                                            stride=conv.stride, padding=conv.padding, bias=False)
            elif hasattr(m.backbone, "body") and hasattr(m.backbone.body, "conv1"):
                conv = m.backbone.body.conv1
                m.backbone.body.conv1 = nn.Conv2d(2, conv.out_channels, kernel_size=conv.kernel_size,
                                                stride=conv.stride, padding=conv.padding, bias=False)
            else:
                raise RuntimeError("Could not find backbone conv1 to change in_channels to 2.")

            m = m.to(device)

            # your DeepLab checkpoint saved as {'state_dict': ...}
            ckpt = torch.load(ckpt_path, map_location=device,weights_only=False)
            state = ckpt.get("state_dict", ckpt)
            # strip DataParallel prefix if present
            state = {k.replace("module.", ""): v for k, v in state.items()}
            m.load_state_dict(state, strict=True)

            # return: model, output mode, normalization profile
            # "softmax2" means use softmax and take channel 1 as oil probability
            # "deeplab_clip" runs the SAR normalization you used during training
            return m, "softmax2", "deeplab_clip"

        else:
            raise ValueError(f"unknown model: {model_name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tif", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--cfg",  required=True)
    ap.add_argument("--aoi-wkt", default=None)
    ap.add_argument("--model", default="unet_b48_v1", choices=["unet_b48_v1","unet_densenet","deeplabv3_v1"],
                help="which model architecture to use")
    args = ap.parse_args()
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # load model architecture + weights (your UNet b48)
    # commented out 18/09/2025
    """ from unet_b48 import UNet
    model = UNet(in_ch=2, out_ch=1, base=48).to(device)
    ckpt = torch.load(args.ckpt, map_location=device)
    model.load_state_dict(ckpt["model"]); 
    set_model(model)  # <<< important """

    tif_path = Path(args.tif)
    work_tif = tif_path
    print(tif_path)

    cfg = json.loads(open(args.cfg).read())
    thr = float(cfg.get("threshold", 0.75))


    # ---- build/load chosen model ----
    model, out_mode, norm_profile = build_and_load_model(args.model, args.ckpt, device)
    set_model(model)  # if other parts still rely on it; otherwise not necessary

    
    # Optional: clip to AOI to speed up (rasterio.mask)
    """ if args.aoi_wkt:
        try:
            geom_ll = load_wkt(args.aoi_wkt)  # assumed lon/lat (EPSG:4326)
            with rasterio.open(tif_path) as src:
                dst_crs = CRS.from_user_input(src.crs)
                # reproject AOI to raster CRS
                tfm = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True).transform
                geom_ds = shp_transform(tfm, geom_ll)

                # quick bbox overlap check
                ds_bounds = shp_box(*src.bounds)
                if not geom_ds.is_valid:
                    geom_ds = geom_ds.buffer(0)
                if not geom_ds.intersects(ds_bounds):
                    print("[clip] warning: AOI (reprojected) does not intersect raster bounds; skipping clip")
                else:
                    out_img, out_trans = rio_mask(src, [json.loads(json.dumps(mapping(geom_ds)))],
                                                crop=True, nodata=0)
                    profile = src.profile
                    profile.update({
                        "height": out_img.shape[1],
                        "width":  out_img.shape[2],
                        "transform": out_trans
                    })
                    tmp = tif_path.with_name(tif_path.stem + "_clip.tif")
                    with rasterio.open(tmp, "w", **profile) as dst:
                        dst.write(out_img)
                    work_tif = tmp
        except Exception as e:
            print(f"[clip] warning: {e}") """
    
    """ if args.aoi_wkt:
        try:
            geom_ll = load_wkt(args.aoi_wkt)  # EPSG:4326 lon/lat
            with rasterio.open(tif_path) as src:
                # Reproject AOI -> raster CRS using rasterio (no pyproj import needed)
                geom_ds = transform_geom(
                    "EPSG:4326",
                    src.crs,                              # raster CRS
                    mapping(geom_ll),                     # GeoJSON geometry
                    precision=6
                )

                # Quick overlap check in raster CRS using shapely
                from shapely.geometry import shape as shp_shape, box as shp_box
                geom_ds_shp = shp_shape(geom_ds)
                ds_bounds = shp_box(*src.bounds)

                if not geom_ds_shp.is_valid:
                    geom_ds_shp = geom_ds_shp.buffer(0)

                if not geom_ds_shp.intersects(ds_bounds):
                    print("[clip] warning: AOI (reprojected) does not intersect raster bounds; skipping clip")
                else:
                    out_img, out_trans = rio_mask(src, [geom_ds], crop=True, nodata=0)
                    profile = src.profile
                    profile.update({
                        "height": out_img.shape[1],
                        "width":  out_img.shape[2],
                        "transform": out_trans
                    })
                    tmp = tif_path.with_name(tif_path.stem + "_clip.tif")
                    with rasterio.open(tmp, "w", **profile) as dst:
                        dst.write(out_img)
                    work_tif = tmp
        except Exception as e:
            print(f"[clip] warning: {e}") """

    if args.aoi_wkt:
        try:
            geom_ll = load_wkt(args.aoi_wkt)  # EPSG:4326 lon/lat
            with rasterio.open(tif_path) as src:
                geom_ds = transform_geom("EPSG:4326", src.crs, mapping(geom_ll), precision=6)
                from shapely.geometry import shape as shp_shape, box as shp_box
                geom_ds_shp = shp_shape(geom_ds)
                ds_bounds = shp_box(*src.bounds)

                if not geom_ds_shp.is_valid:
                    geom_ds_shp = geom_ds_shp.buffer(0)

                if not geom_ds_shp.intersects(ds_bounds):
                    print("[clip] warning: AOI (reprojected) does not intersect raster bounds; skipping clip")
                else:
                    out_img, out_trans = rio_mask(src, [geom_ds], crop=True, nodata=0)
                    tmp = tif_path.with_name(tif_path.stem + "_clip.tif")

                    # --- preserve georef (gcps vs affine) ---
                    prof = src.profile.copy()
                    # We always rewrite width/height to match crop
                    prof.update(height=out_img.shape[1], width=out_img.shape[2])
                    gcps, gcps_crs = src.gcps
                    if gcps:
                        # radar geometry: keep GCPs, drop transform
                        prof.pop("transform", None)
                        with rasterio.open(tmp, "w", **prof, gcps=gcps, crs=gcps_crs) as dst:
                            dst.write(out_img)
                    else:
                        # affine: keep updated out_trans
                        prof.update(transform=out_trans)
                        with rasterio.open(tmp, "w", **prof) as dst:
                            dst.write(out_img)
                    work_tif = tmp
        except Exception as e:
            print(f"[clip] warning: {e}")



    # run full-image inference with your saved config (threshold etc.)
    # prob, mask, transform, crs = predict_full_image_weighted(str(work_tif), tile=512, stride=448, cfg_path=args.cfg)
    """ prob, mask, transform, crs = predict_full_image_weighted(str(work_tif), 
                                                             tile=512, stride=448, thr=thr, 
                                                             tta=cfg.get("tta", False), return_geo=True) """
    
    # after you've opened work_tif to get transform/crs
    with rasterio.open(str(work_tif)) as src:
        transform = src.transform
        crs = src.crs
        # read image once here if you want for both models
        v1 = src.read(1, masked=True).filled(np.nan).astype(np.float32)
        v2 = src.read(2 if src.count >= 2 else 1, masked=True).filled(np.nan).astype(np.float32)
        img = np.stack([v1, v2], axis=-1)  # (H,W,2)
    H, W, _ = img.shape

    if args.model == "unet_b48_v1":
        # your existing helper (no changes)
        prob, mask, transform, crs = predict_full_image_weighted(
            str(work_tif), tile=512, stride=448, thr=thr, tta=cfg.get("tta", False), return_geo=True
        )

    

    else:  # deeplabv3 & unet_densenet
        if args.model == "unet_densenet":
            from unet_densenet import UNetDictOut
            model = UNetDictOut(in_ch=2,
                            num_classes=2,
                            encoder_name="densenet201",
                            encoder_weights="imagenet",
                            dropout_p=0.0,
                            )
            
            
            ckpt  = torch.load(args.ckpt, map_location=device, weights_only=False)
            state = ckpt.get("state_dict", ckpt)
            state = {k.replace("module.", ""): v for k,v in state.items()}
            model.load_state_dict(state, strict=True)
            model = model.to(device).eval()

        else:    
            # build deep lab
            from torchvision.models.segmentation import deeplabv3_resnet50
            from torchvision.models.segmentation import deeplabv3_resnet101
            import torch.nn as nn

            # model = deeplabv3_resnet50(weights=None, num_classes=2)
            model = deeplabv3_resnet101(weights=None, num_classes=2)
            # swap first conv to 2ch
            if hasattr(model.backbone, "conv1"):
                conv = model.backbone.conv1
                model.backbone.conv1 = nn.Conv2d(2, conv.out_channels, kernel_size=conv.kernel_size,
                                                stride=conv.stride, padding=conv.padding, bias=False)
            elif hasattr(model.backbone, "body") and hasattr(model.backbone.body, "conv1"):
                conv = model.backbone.body.conv1
                model.backbone.body.conv1 = nn.Conv2d(2, conv.out_channels, kernel_size=conv.kernel_size,
                                                    stride=conv.stride, padding=conv.padding, bias=False)
            else:
                raise RuntimeError("can't find conv1")

            ckpt = torch.load(args.ckpt, map_location=device,weights_only=False)
            state = ckpt.get('state_dict', ckpt)
            state = {k.replace('module.', ''): v for k,v in state.items()}
            model.load_state_dict(state, strict=True)
            model = model.to(device).eval()

        # helpers
        def normalize_2ch_numpy(img2):
            x = np.clip(img2, -50.0, 5.0).astype(np.float32)
            x = (x - (-22.5)) / 12.5
            x = np.moveaxis(x, -1, 0)[None].astype(np.float32)  # (1,2,H,W)
            return torch.from_numpy(x)

        @torch.no_grad()
        def predict_prob_tile(x4d_cpu):
            with torch.autocast(device_type='cuda', enabled=torch.cuda.is_available()):
                x = x4d_cpu.to(device, non_blocking=True)
                logits = model(x)["out"]            # (1,2,h,w)
                prob = torch.softmax(logits, dim=1)[:,1]  # oil class
                return prob[0].float().cpu().numpy()

        # tiling (same indexing as your UNet version)
        tile, stride = 512, 448
        win = np.outer(np.hanning(tile), np.hanning(tile)).astype(np.float32)
        win /= max(win.max(), 1.0)

        num = np.zeros((H,W), np.float32)
        den = np.zeros((H,W), np.float32)

        for top in range(0, H, stride):
            for left in range(0, W, stride):
                h = min(tile, H - top); w = min(tile, W - left)
                patch = img[top:top+h, left:left+w, :]
                if h < tile or w < tile:
                    patch = np.pad(patch, ((0, tile-h), (0, tile-w), (0,0)), mode='reflect')
                x4d = normalize_2ch_numpy(patch)
                p = predict_prob_tile(x4d)              # (tile,tile)
                w2 = win[:h, :w]
                num[top:top+h, left:left+w] += p[:h,:w] * w2
                den[top:top+h, left:left+w] += w2

        prob = num / np.maximum(den, 1e-6)
        mask = (prob >= thr).astype(np.uint8)
    



    geo = read_georef(work_tif)  # {'mode':'gcps' or 'affine', ...}

    # --- build a base profile ---
    H, W = mask.shape
    base_profile = dict(
        driver="GTiff", height=H, width=W, count=1,
        tiled=True, blockxsize=256, blockysize=256,
        compress="deflate", predictor=2
    )

    # --- add georef according to mode ---
    writer_kwargs = base_profile.copy()
    if geo["mode"] == "gcps":
        writer_kwargs.update(crs=geo["crs"])  # gcps CRS
        extra_open = dict(gcps=geo["gcps"])   # pass via open()
    else:
        writer_kwargs.update(crs=geo["crs"], transform=geo["transform"])
        extra_open = {}


    # tune if you want via CLI later
    MIN_OBJ_PIX = 1
    mask = clean_mask(mask, min_obj=MIN_OBJ_PIX)

    # --- save outputs to <tif_dir>/infer_out ---
    out_dir = tif_path.parent / "infer_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    base = tif_path.stem  # original scene name (not _clip)
    mask_path = out_dir / f"{base}_mask.tif"
    prob_f32_path = out_dir / f"{base}_prob_f32.tif"   # true prob
    prob_u8_path  = out_dir / f"{base}_prob_u8.tif"    # quick preview

    H, W = mask.shape

    # common geo profile (use geo returned by predict_full_image_weighted)
    geo_profile = {
        "driver": "GTiff",
        "height": H,
        "width": W,
        "count": 1,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
        "predictor": 2,
    }

    """ # MASK uint8 {0,1}
    with rasterio.open(mask_path, "w", dtype="uint8", nodata=0, **geo_profile) as dst:
        dst.write(mask.astype(np.uint8), 1)

    # PROB float32 [0,1]
    with rasterio.open(prob_f32_path, "w", dtype="float32", nodata=None, **geo_profile) as dst:
        dst.write(prob.astype(np.float32), 1)

    # PROB preview uint8 [0..255]
    prob_u8 = (np.clip(prob, 0, 1) * 255.0).astype(np.uint8)
    with rasterio.open(prob_u8_path, "w", dtype="uint8", nodata=0, **geo_profile) as dst:
        dst.write(prob_u8, 1) """
    
    # MASK uint8 {0,1}
    with rasterio.open(mask_path, "w", dtype="uint8", nodata=0, **writer_kwargs, **extra_open) as dst:
        dst.write(mask.astype(np.uint8), 1)

    # PROB float32 [0,1]
    with rasterio.open(prob_f32_path, "w", dtype="float32", nodata=None, **writer_kwargs, **extra_open) as dst:
        dst.write(prob.astype(np.float32), 1)

    # PROB preview uint8
    prob_u8 = (np.clip(prob, 0, 1) * 255.0).astype(np.uint8)
    with rasterio.open(prob_u8_path, "w", dtype="uint8", nodata=0, **writer_kwargs, **extra_open) as dst:
        dst.write(prob_u8, 1)


    print("[dbg] mask sum:", int(mask.sum()))
    print("[dbg] unique(mask):", np.unique(mask)[:5], "thr used:", thr)
    print("[dbg] transform:", transform, "crs:", crs)


    lat_hint = raster_center_lat(work_tif)
    min_area_px = 10  
    # polygonize with a conservative area floor (tune this)
    fc = polygons_with_confidence(
        prob, mask, transform, crs,
        min_area_px=1000,         # ← primary filter
        min_area_m2=0,          # or omit
        lat_hint=float(lat_hint),
        debug=True
    )
    bbox = bbox_from_fc(fc) or None

    out = {
        "fc": fc,
        "bbox": bbox,
        "count": len(fc.get("features", [])),
        "prob_u8": str(prob_u8),
        
        "mask_path": str(mask_path),
    }
    print(json.dumps(out, separators=(',',':')))
    #json.dumps(out, separators=(',',':'))
    sys.exit(0)

if __name__ == "__main__":
    main()
