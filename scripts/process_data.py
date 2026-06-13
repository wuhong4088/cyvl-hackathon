#!/usr/bin/env python3
import json
import math
import ssl
import urllib.request
import urllib.parse
from pathlib import Path
import zipfile
import tempfile
import geopandas as gpd

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data"
DOWNLOAD_DIR = DATA_DIR / "download"
ZIP_PATH = DOWNLOAD_DIR / "drive-download-20260613T191614Z-3-001.zip"

LON_MIN, LON_MAX = -72.0, -70.0
LAT_MIN, LAT_MAX = 42.0, 43.0

OBSTACLE_TYPES = {
    "UTILITY_POLE": "pole",
    "TRAFFIC_SIGNAL_POLE": "signal_pole",
    "HYDRANT": "hydrant",
    "LUMINARIES": "luminaire",
    "CATCH_BASIN": "catch_basin",
}
TRANSIT_TYPES = {
    "STAND_ALONE_PEDESTRIAN_HEAD": "ped_head",
    "PEDESTRIAN_PUSH_BUTTON": "push_button",
}

def in_bounds(lon, lat):
    return lon is not None and LON_MIN < lon < LON_MAX and LAT_MIN < lat < LAT_MAX

def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def calculate_line_length_ft(coords):
    if not coords or len(coords) < 2:
        return 0.0
    total_m = 0.0
    for i in range(len(coords) - 1):
        total_m += haversine_m(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
    return total_m * 3.28084

def write_geojson(features, name):
    path = DOWNLOAD_DIR / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path

# ─── Load Assets from ZIP ─────────────────────────────────────────────────────
def load_assets_from_zip():
    print("📦 Reading aboveGroundAssets from zip...")
    with zipfile.ZipFile(ZIP_PATH, 'r') as outer_zip:
        with outer_zip.open('CityofSomervilleMAMarketingDemo-aboveGroundAssets.geojson') as f:
            return json.loads(f.read().decode('utf-8'))

def process_assets(assets_json):
    print("📍 Processing point & line assets from loaded JSON...")
    trees, ramps, obstacles, transit = [], [], [], []
    sidewalks, curbs = [], []
    
    tree_coords, transit_coords, obstacle_coords, catch_coords, ramp_coords = [], [], [], [], []
    sidewalk_centroids = []

    features = assets_json.get("features", [])
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if not geom:
            continue
            
        at = props.get("asset_type")
        gtype = geom.get("type")
        coords = geom.get("coordinates")

        if gtype == "Point":
            lon, lat = coords[0], coords[1]
            if not in_bounds(lon, lat):
                continue
            
            is_tree = at == "TREE"
            is_ramp = at == "RAMP"
            ob_kind = OBSTACLE_TYPES.get(at)
            tr_kind = TRANSIT_TYPES.get(at)

            if is_tree:
                tree_coords.append((lon, lat))
                trees.append(feat)
            elif is_ramp:
                ramp_coords.append((lon, lat))
                ramps.append(feat)
            elif ob_kind:
                obstacle_coords.append((lon, lat))
                if at == "CATCH_BASIN":
                    catch_coords.append((lon, lat))
                obstacles.append(feat)
            elif tr_kind:
                transit_coords.append((lon, lat))
                transit.append(feat)

        elif gtype == "LineString":
            if not coords:
                continue
            # Calculate centroid
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            clon, clat = sum(xs) / len(xs), sum(ys) / len(ys)
            if not in_bounds(clon, clat):
                continue

            if at == "SIDEWALK":
                sw_type = str(props.get("Type") or props.get("sidewalk_type") or "")
                has_walk = sw_type in ("Sidewalk", "Shared Path")
                sidewalk_centroids.append((clon, clat, has_walk))
                # Normalize properties
                feat["properties"]["has_walk"] = has_walk
                feat["properties"]["sidewalk_type"] = sw_type
                sidewalks.append(feat)
            elif at == "CURB":
                curbs.append(feat)

    write_geojson(trees, "trees.geojson")
    write_geojson(ramps, "ramps.geojson")
    write_geojson(obstacles, "obstacles.geojson")
    write_geojson(transit, "transit.geojson")
    write_geojson(sidewalks, "sidewalks.geojson")
    write_geojson(curbs, "curbs.geojson")

    print(f"   ✅ trees={len(trees)} ramps={len(ramps)} obstacles={len(obstacles)} transit={len(transit)}")
    print(f"   ✅ sidewalks={len(sidewalks)} curbs={len(curbs)}")

    return {
        "tree_coords": tree_coords,
        "ramp_coords": ramp_coords,
        "obstacle_coords": obstacle_coords,
        "catch_coords": catch_coords,
        "transit_coords": transit_coords,
        "sidewalk_centroids": sidewalk_centroids,
    }

# ─── Load Pavement Shapefile from ZIP ──────────────────────────────────────────
def load_pavements_from_zip():
    print("🛣️  Reading pavement shapefile from zip...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        with zipfile.ZipFile(ZIP_PATH, 'r') as outer_zip:
            pavement_zip_data = outer_zip.read('CityofSomervilleMAMarketingDemo-Segment-to-Segment Pavement Scores.zip')
            pavement_zip_path = tmpdir_path / 'pavement.zip'
            pavement_zip_path.write_bytes(pavement_zip_data)
            
            with zipfile.ZipFile(pavement_zip_path, 'r') as inner_zip:
                inner_zip.extractall(tmpdir_path / 'shp')
                
            gdf = gpd.read_file(tmpdir_path / 'shp' / 'layer_zip.shp')
            
            pavements = []
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                
                # Get coordinates
                if geom.geom_type == 'LineString':
                    coords = list(geom.coords)
                    xs = [c[0] for c in coords]
                    ys = [c[1] for c in coords]
                    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
                    length_ft = calculate_line_length_ft(coords)
                else:
                    continue
                    
                if not in_bounds(lon, lat):
                    continue
                    
                pci = float(row.get("score") or 50.0)
                pavements.append({
                    "lon": lon, "lat": lat,
                    "address": str(row.get("client_seg") or "Unnamed segment"),
                    "pci_score": pci,
                    "label": str(row.get("label") or "Medium"),
                    "length_ft": length_ft,
                })
            print(f"   ✅ {len(pavements)} pavement segments loaded")
            return pavements

# ─── Weather ──────────────────────────────────────────────────────────────────
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
    print("🌡️  Fetching weather...")
    # Nov 2025 Winter fallback or fetch
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
    except Exception:
        winter_daily = _winter_fallback()

    # Jul 2025 Summer fallback or fetch
    summer_daily = []
    try:
        days = _daily_rollup(_fetch_archive("2025-07-15", "2025-07-22"))
        for day, v in sorted(days.items()):
            avg_t = sum(v["temp"]) / len(v["temp"])
            max_t = max(v["temp"])
            avg_h = sum(v["humid"]) / len(v["humid"])
            avg_w = sum(v["wind"]) / len(v["wind"])
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
    except Exception:
        summer_daily = _summer_fallback()

    w_risk = sum(d["risk"] for d in winter_daily) / len(winter_daily)
    s_risk = sum(d["risk"] for d in summer_daily) / len(summer_daily)
    result = {
        "location": "Somerville, MA",
        "winter": {"period": "2025-11-17 to 2025-11-24", "note": "Matches Cyvl scan period", "overall_risk": round(w_risk, 3), "daily": winter_daily},
        "summer": {"period": "2025-07-15 to 2025-07-22", "note": "Summer heat-stress week", "overall_risk": round(s_risk, 3), "daily": summer_daily},
    }
    (DOWNLOAD_DIR / "weather.json").write_text(json.dumps(result, indent=2))
    return result

def _winter_fallback():
    base = [("2025-11-17", 3.2, -0.5, 0.75), ("2025-11-18", 4.1, 1.2, 0.55), ("2025-11-19", 1.8, -2.1, 0.85), ("2025-11-20", 2.5, -1.0, 0.70),
            ("2025-11-21", 5.0, 2.0, 0.55), ("2025-11-22", 3.8, 0.5, 0.65), ("2025-11-23", 2.1, -3.0, 0.80), ("2025-11-24", 4.5, 1.8, 0.60)]
    return [{"date": d, "avg_temp_c": a, "min_temp_c": m, "avg_humidity": 78, "total_precip_mm": 1.0, "total_snow_cm": 0.2, "avg_wind_kmh": 12, "risk": r} for d, a, m, r in base]

def _summer_fallback():
    base = [("2025-07-15", 25.7, 30.2, 0.75), ("2025-07-16", 26.7, 30.8, 0.80), ("2025-07-17", 26.8, 29.8, 0.70), ("2025-07-18", 24.4, 27.2, 0.55),
            ("2025-07-19", 23.0, 28.0, 0.55), ("2025-07-20", 24.8, 30.0, 0.70), ("2025-07-21", 21.7, 25.0, 0.45), ("2025-07-22", 20.2, 25.6, 0.45)]
    return [{"date": d, "avg_temp_c": a, "max_temp_c": mx, "avg_humidity": 65, "avg_wind_kmh": 9, "risk": r} for d, a, mx, r in base]

# ─── Risk Calculation ─────────────────────────────────────────────────────────
def compute_risk(pavements, ctx, weather):
    print("🧮 Computing risk...")
    tree_coords = ctx["tree_coords"]
    transit_coords = ctx["transit_coords"]
    ramp_coords = ctx["ramp_coords"]
    catch_coords = ctx["catch_coords"]
    sidewalk_centroids = ctx["sidewalk_centroids"]

    w_weather = weather["winter"]["overall_risk"]
    s_weather = weather["summer"]["overall_risk"]

    R = 60
    TREE_FULL, TRANSIT_FULL, RAMP_FULL, CATCH_FULL, SIDEWALK_FULL = 8, 3, 3, 3, 4

    def near(coords, lon, lat):
        return sum(1 for cx, cy in coords if haversine_m(lon, lat, cx, cy) <= R)

    feats = []
    for seg in pavements:
        lon, lat = seg["lon"], seg["lat"]
        n_trees = near(tree_coords, lon, lat)
        n_transit = near(transit_coords, lon, lat)
        n_ramps = near(ramp_coords, lon, lat)
        n_catch = near(catch_coords, lon, lat)
        n_walk = sum(1 for cx, cy, has in sidewalk_centroids if has and haversine_m(lon, lat, cx, cy) <= R)

        shade_score = min(1.0, n_trees / TREE_FULL)
        shade_deficit = 1.0 - shade_score
        pci = seg["pci_score"]
        pavement_risk = (1.0 - min(100, pci) / 100.0) if pci > 0 else 0.5
        drainage_deficit = 1.0 - min(1.0, n_catch / CATCH_FULL)

        ped_exposure = min(1.0, n_walk / SIDEWALK_FULL)
        transit_exposure = min(1.0, n_transit / TRANSIT_FULL)
        feasibility = round(min(1.0, (min(1.0, n_walk / SIDEWALK_FULL) + min(1.0, n_ramps / RAMP_FULL)) / 2), 3)

        winter_surface = round(min(1.0, shade_score * 0.6 + drainage_deficit * 0.4), 3)
        winter_risk = round(min(1.0, w_weather * 0.30 + winter_surface * 0.25 + ped_exposure * 0.20 + transit_exposure * 0.15 + feasibility * 0.10), 4)

        summer_surface = round(min(1.0, shade_deficit * 0.6 + pavement_risk * 0.4), 3)
        summer_risk = round(min(1.0, s_weather * 0.30 + summer_surface * 0.25 + ped_exposure * 0.20 + transit_exposure * 0.15 + feasibility * 0.10), 4)

        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "address": seg["address"], "pci_score": pci, "length_ft": seg["length_ft"],
                "nearby_trees": n_trees, "nearby_transit": n_transit, "nearby_ramps": n_ramps, "nearby_catch_basins": n_catch, "nearby_sidewalks": n_walk,
                "shade_score": round(shade_score, 3), "shade_deficit": round(shade_deficit, 3), "pavement_risk": round(pavement_risk, 3),
                "ped_exposure": round(ped_exposure, 3), "transit_exposure": round(transit_exposure, 3), "feasibility": feasibility,
                "winter_climate": round(w_weather, 3), "winter_surface": winter_surface, "winter_risk": winter_risk, "winter_label": _label(winter_risk),
                "summer_climate": round(s_weather, 3), "summer_surface": summer_surface, "summer_risk": summer_risk, "summer_label": _label(summer_risk),
            }
        })
    write_geojson(feats, "risk_map.geojson")
    return feats

def _label(r):
    if r >= 0.75: return "CRITICAL"
    if r >= 0.55: return "HIGH"
    if r >= 0.35: return "MEDIUM"
    return "LOW"

def _summary_stats(risk_feats, ctx, weather):
    print("🎯 Selecting demo sites and computing summary statistics...")
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
            "kind": kind, "season": season, "address": p["address"], "coordinates": feat["geometry"]["coordinates"],
            "risk": p[f"{season}_risk"], "label": p[f"{season}_label"], "factors": factor_breakdown(p, season),
            "geometry_context": {
                "nearby_trees": p["nearby_trees"], "nearby_sidewalks": p["nearby_sidewalks"], "nearby_transit_signals": p["nearby_transit"],
                "nearby_ramps": p["nearby_ramps"], "nearby_catch_basins": p["nearby_catch_basins"], "pci_score": p["pci_score"], "shade_score": p["shade_score"],
            }
        }

    summer_pool = [f for f in risk_feats if f["properties"]["nearby_sidewalks"] > 0]
    summer_pool.sort(key=lambda f: f["properties"]["summer_risk"], reverse=True)
    summer_site = site(summer_pool[0], "summer", "Hot shade-poor sidewalk corridor") if summer_pool else None

    winter_pool = [f for f in risk_feats if f["properties"]["nearby_transit"] > 0]
    winter_pool.sort(key=lambda f: f["properties"]["winter_risk"], reverse=True)
    if not winter_pool:
        winter_pool = sorted(risk_feats, key=lambda f: f["properties"]["winter_risk"], reverse=True)
    winter_site = site(winter_pool[0], "winter", "Icy salt-priority transit crossing") if winter_pool else None

    def dist(feats, season):
        scores = [f["properties"][f"{season}_risk"] for f in feats]
        return {
            "CRITICAL": sum(1 for s in scores if s >= 0.75),
            "HIGH": sum(1 for s in scores if 0.55 <= s < 0.75),
            "MEDIUM": sum(1 for s in scores if 0.35 <= s < 0.55),
            "LOW": sum(1 for s in scores if s < 0.35),
        }
    def top5(season):
        ranked = sorted(risk_feats, key=lambda f: f["properties"][f"{season}_risk"], reverse=True)[:5]
        return [{"address": f["properties"]["address"], "risk": f["properties"][f"{season}_risk"], "label": f["properties"][f"{season}_label"], "coordinates": f["geometry"]["coordinates"]} for f in ranked]

    summary = {
        "location": "Somerville, MA",
        "total_segments": len(risk_feats),
        "asset_counts": {
            "trees": len(ctx["tree_coords"]), "ramps": len(ctx["ramp_coords"]), "obstacles": len(ctx["obstacle_coords"]),
            "transit_signals": len(ctx["transit_coords"]), "sidewalks": len(ctx["sidewalk_centroids"]),
        },
        "weather": {
            "winter_risk": weather["winter"]["overall_risk"], "summer_risk": weather["summer"]["overall_risk"],
            "winter_period": weather["winter"]["period"], "summer_period": weather["summer"]["period"],
        },
        "winter": {"distribution": dist(risk_feats, "winter"), "top5": top5("winter")},
        "summer": {"distribution": dist(risk_feats, "summer"), "top5": top5("summer")},
        "selected_sites": [s for s in (summer_site, winter_site) if s],
    }
    (DOWNLOAD_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

if __name__ == "__main__":
    assets_json = load_assets_from_zip()
    ctx = process_assets(assets_json)
    pavements = load_pavements_from_zip()
    weather = fetch_weather()
    risk_feats = compute_risk(pavements, ctx, weather)
    _summary_stats(risk_feats, ctx, weather)
    print("✨ Successfully generated all files to data/download/")
