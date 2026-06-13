#!/usr/bin/env python3
"""
SafeRoute AI — Climate Stress & Infrastructure Pipeline (Somerville, MA)

Reads Cyvl point-cloud assets (17 types) + pavement inspection data, fetches
both WINTER and SUMMER historical weather from Open-Meteo, and computes a
season-aware 5-factor Climate Stress Priority Score for every road segment.

Outputs to ../data/:
  risk_map.geojson   — road segments, each with winter_* AND summer_* scores
  trees.geojson      — tree canopy points (shade)
  sidewalks.geojson  — pedestrian corridors (LineStrings, with Type)
  curbs.geojson      — curb lines (LineStrings)
  ramps.geojson      — ADA ramp points (accessibility / feasibility)
  obstacles.geojson  — poles / hydrants / signals / catch basins / luminaries
  transit.geojson    — pedestrian heads + push buttons (transit/crossing proxy)
  weather.json       — winter + summer daily series
  summary.json       — stats + the TWO selected high-priority sites

NOTE on data limits (surfaced honestly in the UI):
  • Sidewalk WIDTH is not in the dataset — physical feasibility uses
    sidewalk presence/type + ramp density + curb presence as proxies.
  • There is no dedicated bus-stop layer — pedestrian heads + push buttons
    (signalised crossings) are used as the transit / pedestrian-exposure proxy.
"""

import json
import struct
import math
import ssl
import urllib.request
import urllib.parse
from pathlib import Path

import pandas as pd

# macOS often needs a relaxed SSL context for the weather archive call
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# This machine's pyarrow (19.x) can't read these particular parquet files
# ("Repetition level histogram size mismatch"); fastparquet handles them.
PARQUET_ENGINE = "fastparquet"

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
ASSETS_PARQUET = BASE / "downloads" / "604dc248eac474f2d7498ba9_aboveGroundAssets.parquet"
PAVEMENTS_PARQUET = BASE / "downloads" / "19f11df61df2e8fc86f70320_pavements.parquet"
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)

# Somerville bounding box (sanity filter for parsed coordinates)
LON_MIN, LON_MAX = -72.0, -70.0
LAT_MIN, LAT_MAX = 42.0, 43.0

# Obstacle asset types (point geometry) we surface as conflicts / context
OBSTACLE_TYPES = {
    "UTILITY_POLE": "pole",
    "TRAFFIC_SIGNAL_POLE": "signal_pole",
    "HYDRANT": "hydrant",
    "LUMINARIES": "luminaire",
    "CATCH_BASIN": "catch_basin",
}
# Pedestrian-head / push-button = signalised crossing → transit/exposure proxy
TRANSIT_TYPES = {
    "STAND_ALONE_PEDESTRIAN_HEAD": "ped_head",
    "PEDESTRIAN_PUSH_BUTTON": "push_button",
}


# ─── WKB Geometry Parsers ─────────────────────────────────────────────────────
def parse_wkb_point(wkb):
    """WKB Point → (lon, lat)."""
    try:
        if wkb[0] == 1:  # little-endian
            x = struct.unpack_from("<d", wkb, 5)[0]
            y = struct.unpack_from("<d", wkb, 13)[0]
            return x, y
    except Exception:
        pass
    return None, None


def parse_wkb_linestring_full(wkb):
    """WKB LineString → list of [lon, lat] coordinate pairs (or None)."""
    try:
        if wkb[0] != 1:
            return None
        geom_type = struct.unpack_from("<I", wkb, 1)[0]
        if geom_type != 2:
            return None
        n = struct.unpack_from("<I", wkb, 5)[0]
        coords, off = [], 9
        for _ in range(n):
            x = struct.unpack_from("<d", wkb, off)[0]
            y = struct.unpack_from("<d", wkb, off + 8)[0]
            coords.append([x, y])
            off += 16
        return coords
    except Exception:
        return None


def in_bounds(lon, lat):
    return lon is not None and LON_MIN < lon < LON_MAX and LAT_MIN < lat < LAT_MAX


def centroid(coords):
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def write_geojson(features, name):
    path = DATA_DIR / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


# ─── Step 1: Point assets (trees, ramps, obstacles, transit) ──────────────────
def process_point_assets(df):
    print("📍 Processing point assets (trees, ramps, obstacles, transit)...")

    trees, ramps, obstacles, transit = [], [], [], []
    tree_coords, transit_coords, obstacle_coords, catch_coords, ramp_coords = [], [], [], [], []

    for _, row in df.iterrows():
        at = row["asset_type"]
        is_tree = at == "TREE"
        is_ramp = at == "RAMP"
        ob_kind = OBSTACLE_TYPES.get(at)
        tr_kind = TRANSIT_TYPES.get(at)
        if not (is_tree or is_ramp or ob_kind or tr_kind):
            continue

        geom = row["geometry"]
        if geom is None:
            continue
        lon, lat = parse_wkb_point(geom)
        if not in_bounds(lon, lat):
            continue

        if is_tree:
            tree_coords.append((lon, lat))
            trees.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"asset_type": "TREE",
                               "neighborhood": str(row.get("__neighborhoods", ""))},
            })
        elif is_ramp:
            ramp_coords.append((lon, lat))
            ramps.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "asset_type": "RAMP",
                    "condition": str(row.get("Condition", "") or row.get("condition", "")),
                    "truncated_domes": str(row.get("Truncated Domes", "")),
                    "wings": str(row.get("Wings", "")),
                },
            })
        elif ob_kind:
            obstacle_coords.append((lon, lat))
            if at == "CATCH_BASIN":
                catch_coords.append((lon, lat))
            obstacles.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"asset_type": at, "kind": ob_kind},
            })
        elif tr_kind:
            transit_coords.append((lon, lat))
            transit.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"asset_type": at, "kind": tr_kind},
            })

    write_geojson(trees, "trees.geojson")
    write_geojson(ramps, "ramps.geojson")
    write_geojson(obstacles, "obstacles.geojson")
    write_geojson(transit, "transit.geojson")
    print(f"   ✅ trees={len(trees)} ramps={len(ramps)} "
          f"obstacles={len(obstacles)} transit={len(transit)}")
    return {
        "tree_coords": tree_coords,
        "ramp_coords": ramp_coords,
        "obstacle_coords": obstacle_coords,
        "catch_coords": catch_coords,
        "transit_coords": transit_coords,
    }


# ─── Step 2: Line assets (sidewalks, curbs) ──────────────────────────────────
def process_line_assets(df):
    print("🛤️  Processing line assets (sidewalks, curbs)...")
    sidewalks, curbs = [], []
    sidewalk_centroids = []  # (lon, lat, has_walk) for proximity scoring

    sw = df[df.asset_type == "SIDEWALK"]
    for _, row in sw.iterrows():
        coords = parse_wkb_linestring_full(row["geometry"])
        if not coords:
            continue
        clon, clat = centroid(coords)
        if not in_bounds(clon, clat):
            continue
        sw_type = str(row.get("Type", ""))
        has_walk = sw_type in ("Sidewalk", "Shared Path")
        sidewalk_centroids.append((clon, clat, has_walk))
        sidewalks.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"asset_type": "SIDEWALK", "sidewalk_type": sw_type,
                           "has_walk": has_walk},
        })

    cb = df[df.asset_type == "CURB"]
    for _, row in cb.iterrows():
        coords = parse_wkb_linestring_full(row["geometry"])
        if not coords:
            continue
        clon, clat = centroid(coords)
        if not in_bounds(clon, clat):
            continue
        try:
            length_ft = float(row.get("Length_(ft)") or 0)
        except (TypeError, ValueError):
            length_ft = 0.0
        curbs.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "asset_type": "CURB",
                "condition": str(row.get("Condition", "")),
                "material": str(row.get("Material", "")),
                "length_ft": length_ft,
            },
        })

    write_geojson(sidewalks, "sidewalks.geojson")
    write_geojson(curbs, "curbs.geojson")
    print(f"   ✅ sidewalks={len(sidewalks)} curbs={len(curbs)}")
    return {"sidewalk_centroids": sidewalk_centroids}


# ─── Step 3: Pavement segments ────────────────────────────────────────────────
def process_pavements(df):
    print("🛣️  Processing pavement segments...")
    feats = []
    for _, row in df.iterrows():
        geom = row["geometry"]
        lon = lat = None
        if isinstance(geom, (bytes, bytearray)) and len(geom) > 5:
            if geom[0] == 1:
                gt = struct.unpack_from("<I", geom, 1)[0]
                if gt == 1:
                    lon, lat = parse_wkb_point(geom)
                elif gt == 2:
                    coords = parse_wkb_linestring_full(geom)
                    if coords:
                        lon, lat = centroid(coords)
        if lon is None:
            try:
                lat = float(str(row.get("lat")).strip("[]").split(",")[0])
                lon = float(str(row.get("lon")).strip("[]").split(",")[0])
            except Exception:
                pass
        if not in_bounds(lon, lat):
            continue
        try:
            pci = float(row.get("score"))
        except (TypeError, ValueError):
            pci = 50.0
        feats.append({
            "lon": lon, "lat": lat,
            "address": str(row.get("address_st", "")),
            "pci_score": pci,
            "label": str(row.get("label", "")),
            "length_ft": float(row.get("length_ft") or 0),
        })
    print(f"   ✅ {len(feats)} pavement segments")
    return feats


# ─── Step 4: Weather (winter + summer) ───────────────────────────────────────
def _fetch_archive(start, end):
    p = {
        "latitude": 42.3876, "longitude": -71.0995,
        "start_date": start, "end_date": end,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,snowfall,wind_speed_10m",
        "temperature_unit": "celsius", "timezone": "America/New_York",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=20, context=_SSL_CTX) as r:
        return json.loads(r.read())["hourly"]


def _daily_rollup(hourly):
    days = {}
    for i, t in enumerate(hourly["time"]):
        d = t[:10]
        days.setdefault(d, {"temp": [], "humid": [], "precip": [], "snow": [], "wind": []})
        days[d]["temp"].append(hourly["temperature_2m"][i] or 0)
        days[d]["humid"].append(hourly["relative_humidity_2m"][i] or 0)
        days[d]["precip"].append(hourly["precipitation"][i] or 0)
        days[d]["snow"].append(hourly["snowfall"][i] or 0)
        days[d]["wind"].append(hourly["wind_speed_10m"][i] or 0)
    return days


def fetch_weather():
    """Winter (black-ice) + Summer (heat-stress) historical weather."""
    print("🌡️  Fetching weather — winter (Nov 2025) + summer (Jul 2025)...")

    # ----- WINTER: black-ice conditions (matches Cyvl scan period) -----
    winter_daily = []
    try:
        days = _daily_rollup(_fetch_archive("2025-11-17", "2025-11-24"))
        for day, v in sorted(days.items()):
            avg_t = sum(v["temp"]) / len(v["temp"])
            min_t = min(v["temp"])
            avg_h = sum(v["humid"]) / len(v["humid"])
            tot_p = sum(v["precip"])
            tot_s = sum(v["snow"])
            avg_w = sum(v["wind"]) / len(v["wind"])
            risk = 0.0
            if avg_t <= 4: risk += 0.4
            if avg_t <= 2: risk += 0.3
            if min_t <= 0: risk += 0.2
            if tot_p > 0: risk += 0.15
            if tot_s > 0: risk += 0.15
            if avg_h > 75: risk += 0.10
            winter_daily.append({
                "date": day, "avg_temp_c": round(avg_t, 1), "min_temp_c": round(min_t, 1),
                "avg_humidity": round(avg_h, 1), "total_precip_mm": round(tot_p, 2),
                "total_snow_cm": round(tot_s, 2), "avg_wind_kmh": round(avg_w, 1),
                "risk": round(min(1.0, risk), 3),
            })
    except Exception as e:
        print(f"   ⚠️ winter fetch failed ({e}); using fallback")
        winter_daily = _winter_fallback()

    # ----- SUMMER: heat-stress conditions -----
    summer_daily = []
    try:
        days = _daily_rollup(_fetch_archive("2025-07-15", "2025-07-22"))
        for day, v in sorted(days.items()):
            avg_t = sum(v["temp"]) / len(v["temp"])
            max_t = max(v["temp"])
            avg_h = sum(v["humid"]) / len(v["humid"])
            avg_w = sum(v["wind"]) / len(v["wind"])
            # Heat risk: high daytime temp + humidity, low wind = worse
            risk = 0.0
            if max_t >= 27: risk += 0.35
            if max_t >= 30: risk += 0.30
            if max_t >= 33: risk += 0.15
            if avg_h >= 60: risk += 0.10
            if avg_w < 10: risk += 0.10
            summer_daily.append({
                "date": day, "avg_temp_c": round(avg_t, 1), "max_temp_c": round(max_t, 1),
                "avg_humidity": round(avg_h, 1), "avg_wind_kmh": round(avg_w, 1),
                "risk": round(min(1.0, risk), 3),
            })
    except Exception as e:
        print(f"   ⚠️ summer fetch failed ({e}); using fallback")
        summer_daily = _summer_fallback()

    w_risk = sum(d["risk"] for d in winter_daily) / len(winter_daily)
    s_risk = sum(d["risk"] for d in summer_daily) / len(summer_daily)
    result = {
        "location": "Somerville, MA",
        "winter": {"period": "2025-11-17 to 2025-11-24",
                   "note": "Matches Cyvl scan period", "overall_risk": round(w_risk, 3),
                   "daily": winter_daily},
        "summer": {"period": "2025-07-15 to 2025-07-22",
                   "note": "Summer heat-stress week", "overall_risk": round(s_risk, 3),
                   "daily": summer_daily},
    }
    (DATA_DIR / "weather.json").write_text(json.dumps(result, indent=2))
    print(f"   ✅ winter risk={w_risk:.2f} · summer risk={s_risk:.2f}")
    return result


def _winter_fallback():
    base = [("2025-11-17", 3.2, -0.5, 0.75), ("2025-11-18", 4.1, 1.2, 0.55),
            ("2025-11-19", 1.8, -2.1, 0.85), ("2025-11-20", 2.5, -1.0, 0.70),
            ("2025-11-21", 5.0, 2.0, 0.55), ("2025-11-22", 3.8, 0.5, 0.65),
            ("2025-11-23", 2.1, -3.0, 0.80), ("2025-11-24", 4.5, 1.8, 0.60)]
    return [{"date": d, "avg_temp_c": a, "min_temp_c": m, "avg_humidity": 78,
             "total_precip_mm": 1.0, "total_snow_cm": 0.2, "avg_wind_kmh": 12,
             "risk": r} for d, a, m, r in base]


def _summer_fallback():
    base = [("2025-07-15", 25.7, 30.2, 0.75), ("2025-07-16", 26.7, 30.8, 0.80),
            ("2025-07-17", 26.8, 29.8, 0.70), ("2025-07-18", 24.4, 27.2, 0.55),
            ("2025-07-19", 23.0, 28.0, 0.55), ("2025-07-20", 24.8, 30.0, 0.70),
            ("2025-07-21", 21.7, 25.0, 0.45), ("2025-07-22", 20.2, 25.6, 0.45)]
    return [{"date": d, "avg_temp_c": a, "max_temp_c": mx, "avg_humidity": 65,
             "avg_wind_kmh": 9, "risk": r} for d, a, mx, r in base]


# ─── Step 5: Season-aware 5-factor priority score ────────────────────────────
def compute_risk(pavements, ctx, weather):
    print("🧮 Computing season-aware 5-factor climate-stress scores...")
    tree_coords = ctx["tree_coords"]
    transit_coords = ctx["transit_coords"]
    ramp_coords = ctx["ramp_coords"]
    catch_coords = ctx["catch_coords"]
    sidewalk_centroids = ctx["sidewalk_centroids"]

    w_weather = weather["winter"]["overall_risk"]
    s_weather = weather["summer"]["overall_risk"]

    R = 60          # proximity radius in metres
    TREE_FULL = 8   # trees within R for shade_score = 1
    TRANSIT_FULL = 3
    RAMP_FULL = 3
    CATCH_FULL = 3
    SIDEWALK_FULL = 4

    def near(coords, lon, lat, r=R):
        return sum(1 for cx, cy in coords if haversine_m(lon, lat, cx, cy) <= r)

    feats, winter_hi, summer_hi = [], 0, 0
    for seg in pavements:
        lon, lat = seg["lon"], seg["lat"]

        n_trees = near(tree_coords, lon, lat)
        n_transit = near(transit_coords, lon, lat)
        n_ramps = near(ramp_coords, lon, lat)
        n_catch = near(catch_coords, lon, lat)
        # sidewalk presence: count only segments that actually have a walk
        n_walk = sum(1 for cx, cy, has in sidewalk_centroids
                     if has and haversine_m(lon, lat, cx, cy) <= R)

        shade_score = min(1.0, n_trees / TREE_FULL)
        shade_deficit = 1.0 - shade_score
        pci = seg["pci_score"]
        pavement_risk = (1.0 - min(100, pci) / 100.0) if pci > 0 else 0.5
        drainage_deficit = 1.0 - min(1.0, n_catch / CATCH_FULL)

        # ── shared exposure / feasibility factors ──
        ped_exposure = min(1.0, n_walk / SIDEWALK_FULL)
        transit_exposure = min(1.0, n_transit / TRANSIT_FULL)
        feasibility = round(min(1.0, (min(1.0, n_walk / SIDEWALK_FULL)
                                      + min(1.0, n_ramps / RAMP_FULL)) / 2), 3)

        # ── WINTER (black ice): shade keeps ice frozen; poor drainage pools water ──
        winter_surface = round(min(1.0, shade_score * 0.6 + drainage_deficit * 0.4), 3)
        winter_risk = round(min(1.0,
            w_weather * 0.30 +
            winter_surface * 0.25 +
            ped_exposure * 0.20 +
            transit_exposure * 0.15 +
            feasibility * 0.10), 4)

        # ── SUMMER (heat): no shade + impervious pavement = hot corridor ──
        summer_surface = round(min(1.0, shade_deficit * 0.6 + pavement_risk * 0.4), 3)
        summer_risk = round(min(1.0,
            s_weather * 0.30 +
            summer_surface * 0.25 +
            ped_exposure * 0.20 +
            transit_exposure * 0.15 +
            feasibility * 0.10), 4)

        if winter_risk >= 0.6: winter_hi += 1
        if summer_risk >= 0.6: summer_hi += 1

        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "address": seg["address"], "pci_score": pci,
                "length_ft": seg["length_ft"],
                # nearby geometry counts (for site panels)
                "nearby_trees": n_trees, "nearby_transit": n_transit,
                "nearby_ramps": n_ramps, "nearby_catch_basins": n_catch,
                "nearby_sidewalks": n_walk,
                # shared factors
                "shade_score": round(shade_score, 3),
                "shade_deficit": round(shade_deficit, 3),
                "pavement_risk": round(pavement_risk, 3),
                "ped_exposure": round(ped_exposure, 3),
                "transit_exposure": round(transit_exposure, 3),
                "feasibility": feasibility,
                # winter
                "winter_climate": round(w_weather, 3),
                "winter_surface": winter_surface,
                "winter_risk": winter_risk,
                "winter_label": _label(winter_risk),
                # summer
                "summer_climate": round(s_weather, 3),
                "summer_surface": summer_surface,
                "summer_risk": summer_risk,
                "summer_label": _label(summer_risk),
            },
        })

    write_geojson(feats, "risk_map.geojson")
    print(f"   ✅ {len(feats)} segments · winter HIGH+={winter_hi} summer HIGH+={summer_hi}")
    return feats


def _label(r):
    if r >= 0.75: return "CRITICAL"
    if r >= 0.55: return "HIGH"
    if r >= 0.35: return "MEDIUM"
    return "LOW"


# ─── Step 6: Select two sites + summary ───────────────────────────────────────
def select_sites_and_summary(risk_feats, ctx, weather):
    print("🎯 Selecting two high-priority demonstration sites...")

    def factor_breakdown(p, season):
        return {
            "climate_severity": p[f"{season}_climate"],
            "surface": p[f"{season}_surface"],
            "pedestrian_exposure": p["ped_exposure"],
            "transit_exposure": p["transit_exposure"],
            "physical_feasibility": p["feasibility"],
        }

    def site(feat, season, kind):
        p = feat["properties"]
        return {
            "kind": kind, "season": season,
            "address": p["address"] or "Unnamed segment",
            "coordinates": feat["geometry"]["coordinates"],
            "risk": p[f"{season}_risk"], "label": p[f"{season}_label"],
            "factors": factor_breakdown(p, season),
            "geometry_context": {
                "nearby_trees": p["nearby_trees"],
                "nearby_sidewalks": p["nearby_sidewalks"],
                "nearby_transit_signals": p["nearby_transit"],
                "nearby_ramps": p["nearby_ramps"],
                "nearby_catch_basins": p["nearby_catch_basins"],
                "pci_score": p["pci_score"],
                "shade_score": p["shade_score"],
            },
        }

    # Summer site: hottest shade-poor PEDESTRIAN corridor (must have a sidewalk nearby)
    summer_pool = [f for f in risk_feats if f["properties"]["nearby_sidewalks"] > 0]
    summer_pool.sort(key=lambda f: f["properties"]["summer_risk"], reverse=True)
    summer_site = site(summer_pool[0], "summer", "Hot shade-poor sidewalk corridor") \
        if summer_pool else None

    # Winter site: iciest segment near a signalised crossing (transit exposure)
    winter_pool = [f for f in risk_feats if f["properties"]["nearby_transit"] > 0]
    winter_pool.sort(key=lambda f: f["properties"]["winter_risk"], reverse=True)
    if not winter_pool:  # fall back to plain highest winter risk
        winter_pool = sorted(risk_feats, key=lambda f: f["properties"]["winter_risk"],
                             reverse=True)
    winter_site = site(winter_pool[0], "winter", "Icy salt-priority transit crossing") \
        if winter_pool else None

    def dist(feats, season):
        scores = [f["properties"][f"{season}_risk"] for f in feats]
        return {
            "CRITICAL": sum(1 for s in scores if s >= 0.75),
            "HIGH": sum(1 for s in scores if 0.55 <= s < 0.75),
            "MEDIUM": sum(1 for s in scores if 0.35 <= s < 0.55),
            "LOW": sum(1 for s in scores if s < 0.35),
        }

    def top5(season):
        ranked = sorted(risk_feats, key=lambda f: f["properties"][f"{season}_risk"],
                        reverse=True)[:5]
        return [{"address": f["properties"]["address"] or "Unnamed segment",
                 "risk": f["properties"][f"{season}_risk"],
                 "label": f["properties"][f"{season}_label"],
                 "coordinates": f["geometry"]["coordinates"]} for f in ranked]

    summary = {
        "location": "Somerville, MA",
        "total_segments": len(risk_feats),
        "asset_counts": {
            "trees": len(ctx["tree_coords"]),
            "ramps": len(ctx["ramp_coords"]),
            "obstacles": len(ctx["obstacle_coords"]),
            "transit_signals": len(ctx["transit_coords"]),
            "sidewalks": len(ctx["sidewalk_centroids"]),
        },
        "weather": {
            "winter_risk": weather["winter"]["overall_risk"],
            "summer_risk": weather["summer"]["overall_risk"],
            "winter_period": weather["winter"]["period"],
            "summer_period": weather["summer"]["period"],
        },
        "winter": {"distribution": dist(risk_feats, "winter"), "top5": top5("winter")},
        "summer": {"distribution": dist(risk_feats, "summer"), "top5": top5("summer")},
        "selected_sites": [s for s in (summer_site, winter_site) if s],
        "data_notes": [
            "Sidewalk width not provided by dataset — feasibility uses sidewalk "
            "presence/type + ramp density as proxies.",
            "No dedicated bus-stop layer — pedestrian heads + push buttons "
            "(signalised crossings) used as transit/exposure proxy.",
        ],
    }
    (DATA_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"   ☀️  Summer site: {summary['selected_sites'][0]['address'] if summary['selected_sites'] else 'N/A'}")
    if len(summary["selected_sites"]) > 1:
        print(f"   ❄️  Winter site: {summary['selected_sites'][1]['address']}")
    return summary


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 64)
    print("  SafeRoute AI — Climate Stress & Infrastructure Pipeline")
    print("  Somerville, MA · winter (black ice) + summer (heat stress)")
    print("=" * 64, "\n")

    assets_df = pd.read_parquet(ASSETS_PARQUET, engine=PARQUET_ENGINE)
    pave_df = pd.read_parquet(PAVEMENTS_PARQUET, engine=PARQUET_ENGINE)

    point_ctx = process_point_assets(assets_df)
    line_ctx = process_line_assets(assets_df)
    ctx = {**point_ctx, **line_ctx}
    print()
    pavements = process_pavements(pave_df)
    print()
    weather = fetch_weather()
    print()
    risk_feats = compute_risk(pavements, ctx, weather)
    print()
    select_sites_and_summary(risk_feats, ctx, weather)

    print("\n✨ Done. Open web/index.html to view the dual-season map.")
