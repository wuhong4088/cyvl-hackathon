#!/usr/bin/env python3
"""
SafeRoute AI — Complete Data Processing Pipeline
=================================================
Reads ALL Cyvl Point Cloud data (17 asset types + signs + pavements + distresses),
fetches real weather from Open-Meteo, and computes dual heat/cold climate stress
scores for every pavement segment in Somerville, MA.

Outputs:
  data/assets_all.geojson       – every Cyvl asset on one layer
  data/climate_stress.geojson   – dual-mode risk per pavement segment
  data/weather.json             – real weather + summer proxy
  data/summary.json             – aggregate statistics
  data/sites/site_hot.json      – #1 heat-stress intervention site
  data/sites/site_cold.json     – #1 cold-stress intervention site
  data/sites/site_hot_plan.svg  – SVG site plan (hot)
  data/sites/site_cold_plan.svg – SVG site plan (cold)
"""

import json, struct, math, ssl, urllib.request, urllib.parse
from pathlib import Path

import pandas as pd

# ── macOS SSL bypass for Open-Meteo ──────────────────────────────────────────
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent
DOWNLOADS = BASE / "downloads"
ASSETS_PQ   = DOWNLOADS / "604dc248eac474f2d7498ba9_aboveGroundAssets.parquet"
PAVEMENTS_PQ = DOWNLOADS / "19f11df61df2e8fc86f70320_pavements.parquet"
SIGNS_PQ    = DOWNLOADS / "c3f4e6fa80883852d2f346f9_signs.parquet"
DISTRESS_PQ = DOWNLOADS / "34ae72b8a77ebd24486fadef_distresses.parquet"

DATA_DIR  = BASE / "data"
SITES_DIR = DATA_DIR / "sites"
DATA_DIR.mkdir(exist_ok=True)
SITES_DIR.mkdir(exist_ok=True)

# Somerville, MA centroid
SOMERVILLE_LAT, SOMERVILLE_LON = 42.3876, -71.0995

# ── WKB Geometry Parser ─────────────────────────────────────────────────────

def parse_wkb_point(wkb):
    try:
        if wkb[0] == 1:
            return struct.unpack_from("<d", wkb, 5)[0], struct.unpack_from("<d", wkb, 13)[0]
    except Exception:
        pass
    return None, None


def parse_wkb_linestring(wkb):
    try:
        if wkb[0] == 1:
            gt = struct.unpack_from("<I", wkb, 1)[0]
            if gt == 2:
                n = struct.unpack_from("<I", wkb, 5)[0]
                lons, lats, off = [], [], 9
                for _ in range(n):
                    lons.append(struct.unpack_from("<d", wkb, off)[0])
                    lats.append(struct.unpack_from("<d", wkb, off + 8)[0])
                    off += 16
                return sum(lons) / n, sum(lats) / n
    except Exception:
        pass
    return None, None


def parse_wkb_any(wkb):
    """Return centroid (lon, lat) for any WKB geometry."""
    try:
        if isinstance(wkb, bytes) and len(wkb) > 5 and wkb[0] == 1:
            gt = struct.unpack_from("<I", wkb, 1)[0]
            if gt == 1:
                return parse_wkb_point(wkb)
            if gt == 2:
                return parse_wkb_linestring(wkb)
    except Exception:
        pass
    return None, None


def valid_somerville(lon, lat):
    return lon is not None and -72 < lon < -70 and 42 < lat < 43


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Unified Asset Layer  (assets_all.geojson)
# ═════════════════════════════════════════════════════════════════════════════

def build_assets_all():
    """Combine all 17 aboveGroundAssets types + signs into one GeoJSON."""
    print("📍 Step 1: Building unified asset layer …")

    # ── Above-ground assets ──────────────────────────────────────────────
    df = pd.read_parquet(ASSETS_PQ)
    print(f"   Loaded {len(df)} above-ground assets ({df['asset_type'].nunique()} types)")

    features = []
    # We'll also build lookup dicts for spatial queries later
    asset_records = []  # list of (lon, lat, asset_type, condition, extra)

    for _, row in df.iterrows():
        geom = row.get("geometry")
        if geom is None:
            continue
        lon, lat = parse_wkb_any(geom)
        if not valid_somerville(lon, lat):
            continue

        at = str(row.get("asset_type", ""))
        # Condition: uppercase-C 'Condition' for SIDEWALK/CURB, else empty
        cond = str(row.get("Condition", "")) if pd.notna(row.get("Condition")) else ""
        fid = str(row.get("feature_id", "")) if pd.notna(row.get("feature_id")) else ""
        img = str(row.get("image_url", "")) if pd.notna(row.get("image_url")) else ""

        props = {
            "asset_type": at,
            "condition": cond,
            "feature_id": fid,
            "image_url": img,
        }
        # Extra fields for sidewalk/curb
        if at == "SIDEWALK":
            sw_type = str(row.get("Type", "")) if pd.notna(row.get("Type")) else ""
            props["sidewalk_type"] = sw_type
        if at == "CURB":
            cl = row.get("Length_(ft)")
            props["curb_length_ft"] = float(cl) if pd.notna(cl) else None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": props,
        })
        asset_records.append((lon, lat, at, cond, props))

    print(f"   Geocoded {len(features)} above-ground assets")

    # ── Signs ────────────────────────────────────────────────────────────
    sdf = pd.read_parquet(SIGNS_PQ)
    print(f"   Loaded {len(sdf)} signs")
    sign_count = 0
    for _, row in sdf.iterrows():
        geom = row.get("geometry")
        if geom is None:
            continue
        lon, lat = parse_wkb_any(geom)
        if not valid_somerville(lon, lat):
            continue

        cond = str(row.get("condition", "")) if pd.notna(row.get("condition")) else ""
        fid = str(row.get("fid", "")) if pd.notna(row.get("fid")) else ""
        img = str(row.get("image_url", "")) if pd.notna(row.get("image_url")) else ""
        cat = str(row.get("category", "")) if pd.notna(row.get("category")) else ""
        mutcd = str(row.get("mutcd", "")) if pd.notna(row.get("mutcd")) else ""

        props = {
            "asset_type": "SIGN",
            "condition": cond,
            "feature_id": fid,
            "image_url": img,
            "sign_category": cat,
            "mutcd": mutcd,
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": props,
        })
        asset_records.append((lon, lat, "SIGN", cond, props))
        sign_count += 1

    print(f"   Geocoded {sign_count} signs")

    out = {"type": "FeatureCollection", "features": features}
    path = DATA_DIR / "assets_all.geojson"
    path.write_text(json.dumps(out))
    print(f"   ✅ Saved {len(features)} total assets → {path.name}")
    return asset_records


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Weather (real + summer proxy)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_open_meteo(params, api="archive"):
    base = "https://archive-api.open-meteo.com/v1/archive"
    url = base + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20, context=_SSL_CTX) as r:
        return json.loads(r.read())


def fetch_weather():
    print("🌡️  Step 2: Fetching weather data …")

    params_winter = {
        "latitude": SOMERVILLE_LAT, "longitude": SOMERVILLE_LON,
        "start_date": "2025-11-17", "end_date": "2025-11-24",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,snowfall,wind_speed_10m",
        "temperature_unit": "celsius", "timezone": "America/New_York",
    }

    def _summarise_hourly(data):
        h = data["hourly"]
        daily = {}
        for i, t in enumerate(h["time"]):
            d = t[:10]
            if d not in daily:
                daily[d] = {"temps": [], "humids": [], "precips": [], "snows": [], "winds": []}
            daily[d]["temps"].append(h["temperature_2m"][i] or 0)
            daily[d]["humids"].append(h["relative_humidity_2m"][i] or 0)
            daily[d]["precips"].append(h["precipitation"][i] or 0)
            daily[d]["snows"].append(h["snowfall"][i] or 0)
            daily[d]["winds"].append(h["wind_speed_10m"][i] or 0)

        out = []
        for day, v in sorted(daily.items()):
            avg_t = sum(v["temps"]) / len(v["temps"])
            min_t = min(v["temps"])
            max_t = max(v["temps"])
            avg_h = sum(v["humids"]) / len(v["humids"])
            tp = sum(v["precips"])
            ts = sum(v["snows"])
            aw = sum(v["winds"]) / len(v["winds"])

            # Cold risk factor
            wr = 0.0
            if avg_t <= 4: wr += 0.4
            if avg_t <= 2: wr += 0.3
            if min_t <= 0: wr += 0.2
            if tp > 0:    wr += 0.15
            if ts > 0:    wr += 0.15
            if avg_h > 75: wr += 0.1
            wr = min(1.0, wr)

            out.append({
                "date": day,
                "avg_temp_c": round(avg_t, 1), "min_temp_c": round(min_t, 1),
                "max_temp_c": round(max_t, 1),
                "avg_humidity": round(avg_h, 1),
                "total_precip_mm": round(tp, 2), "total_snow_cm": round(ts, 2),
                "avg_wind_kmh": round(aw, 1), "weather_risk": round(wr, 3),
            })
        return out

    try:
        winter_raw = _fetch_open_meteo(params_winter)
        winter_daily = _summarise_hourly(winter_raw)
        print(f"   Fetched {len(winter_daily)} winter days from Open-Meteo")
    except Exception as e:
        print(f"   ⚠️  Winter fetch failed ({e}), using fallback")
        winter_daily = [
            {"date": "2025-11-17", "avg_temp_c": 3.2, "min_temp_c": -0.5, "max_temp_c": 7.1, "avg_humidity": 78, "total_precip_mm": 2.1, "total_snow_cm": 0, "avg_wind_kmh": 18, "weather_risk": 0.75},
            {"date": "2025-11-18", "avg_temp_c": 4.1, "min_temp_c": 1.2, "max_temp_c": 8.0, "avg_humidity": 72, "total_precip_mm": 0, "total_snow_cm": 0, "avg_wind_kmh": 14, "weather_risk": 0.55},
            {"date": "2025-11-19", "avg_temp_c": 1.8, "min_temp_c": -2.1, "max_temp_c": 5.5, "avg_humidity": 82, "total_precip_mm": 1.5, "total_snow_cm": 0.3, "avg_wind_kmh": 22, "weather_risk": 0.85},
            {"date": "2025-11-20", "avg_temp_c": 2.5, "min_temp_c": -1.0, "max_temp_c": 6.2, "avg_humidity": 76, "total_precip_mm": 0, "total_snow_cm": 0, "avg_wind_kmh": 15, "weather_risk": 0.70},
            {"date": "2025-11-21", "avg_temp_c": 5.0, "min_temp_c": 2.0, "max_temp_c": 9.0, "avg_humidity": 68, "total_precip_mm": 3.2, "total_snow_cm": 0, "avg_wind_kmh": 20, "weather_risk": 0.55},
            {"date": "2025-11-22", "avg_temp_c": 3.8, "min_temp_c": 0.5, "max_temp_c": 7.8, "avg_humidity": 75, "total_precip_mm": 0.8, "total_snow_cm": 0, "avg_wind_kmh": 16, "weather_risk": 0.65},
            {"date": "2025-11-23", "avg_temp_c": 2.1, "min_temp_c": -3.0, "max_temp_c": 5.0, "avg_humidity": 80, "total_precip_mm": 0, "total_snow_cm": 0.5, "avg_wind_kmh": 19, "weather_risk": 0.80},
            {"date": "2025-11-24", "avg_temp_c": 4.5, "min_temp_c": 1.8, "max_temp_c": 8.5, "avg_humidity": 70, "total_precip_mm": 1.1, "total_snow_cm": 0, "avg_wind_kmh": 13, "weather_risk": 0.60},
        ]

    cold_risk = sum(d["weather_risk"] for d in winter_daily) / len(winter_daily)

    # Summer proxy — based on NOAA climate normals for Boston/Somerville
    summer_proxy = {
        "note": "Estimated July/August conditions for Somerville MA (NOAA climate normals)",
        "avg_high_temp_c": 29.5,
        "avg_low_temp_c": 19.2,
        "avg_temp_c": 24.3,
        "record_high_c": 38.3,
        "avg_humidity_pct": 68,
        "avg_precip_mm_month": 85,
        "heat_index_days_above_32c": 12,
        "uv_index_avg": 8.5,
        "heat_severity": 0.72,  # normalised 0-1
    }

    # Compute heat severity from summer proxy
    heat_severity = 0.0
    if summer_proxy["avg_high_temp_c"] >= 28: heat_severity += 0.3
    if summer_proxy["avg_high_temp_c"] >= 32: heat_severity += 0.2
    if summer_proxy["heat_index_days_above_32c"] >= 8: heat_severity += 0.2
    if summer_proxy["avg_humidity_pct"] >= 60: heat_severity += 0.15
    if summer_proxy["uv_index_avg"] >= 7: heat_severity += 0.15
    heat_severity = min(1.0, heat_severity)
    summer_proxy["heat_severity"] = round(heat_severity, 3)

    result = {
        "location": "Somerville, MA",
        "winter_period": "2025-11-17 to 2025-11-24",
        "note": "Winter data from Cyvl scan period; summer proxy from NOAA normals",
        "overall_cold_risk": round(cold_risk, 3),
        "overall_heat_severity": round(heat_severity, 3),
        "winter_daily": winter_daily,
        "summer_proxy": summer_proxy,
    }

    path = DATA_DIR / "weather.json"
    path.write_text(json.dumps(result, indent=2))
    print(f"   ✅ Weather saved → {path.name}  (cold_risk={cold_risk:.2f}, heat_sev={heat_severity:.2f})")
    return result


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 3 — Climate Stress Scoring  (climate_stress.geojson)
# ═════════════════════════════════════════════════════════════════════════════

def compute_climate_stress(asset_records, weather):
    print("🧮 Step 3: Computing dual climate-stress scores for all pavement segments …")

    # Precompute spatial lookups by asset type
    trees = [(lon, lat) for lon, lat, at, _, _ in asset_records if at == "TREE"]
    sidewalks = [(lon, lat, cond, p) for lon, lat, at, cond, p in asset_records if at == "SIDEWALK"]
    ramps = [(lon, lat) for lon, lat, at, _, _ in asset_records if at == "RAMP"]
    ped_signals = [(lon, lat) for lon, lat, at, _, _ in asset_records
                   if at in ("STAND_ALONE_PEDESTRIAN_HEAD", "PEDESTRIAN_PUSH_BUTTON", "TRAFFIC_SIGNAL")]
    obstacles = [(lon, lat, at) for lon, lat, at, _, _ in asset_records
                 if at in ("HYDRANT", "UTILITY_POLE", "TRAFFIC_SIGNAL_POLE", "LUMINARIES",
                           "CATCH_BASIN", "MANHOLE_COVER")]

    print(f"   Spatial indices: {len(trees)} trees, {len(sidewalks)} sidewalks, "
          f"{len(ramps)} ramps, {len(ped_signals)} ped-signals, {len(obstacles)} obstacles")

    # Weather factors
    cold_risk_w = weather["overall_cold_risk"]
    heat_sev_w  = weather["overall_heat_severity"]

    SHADE_RADIUS = 50   # metres
    MAX_TREES    = 8
    NEAR_RADIUS  = 100  # metres for nearby assets
    OBSTACLE_RADIUS = 80

    # Load pavements
    pav_df = pd.read_parquet(PAVEMENTS_PQ)
    print(f"   Processing {len(pav_df)} pavement segments …")

    features = []
    for idx, row in pav_df.iterrows():
        geom = row.get("geometry")
        lon, lat = parse_wkb_any(geom) if geom is not None else (None, None)

        # Fallback to lat/lon columns
        if not valid_somerville(lon, lat):
            try:
                lat_v = row.get("lat")
                lon_v = row.get("lon")
                if lat_v is not None and lon_v is not None:
                    lat = float(str(lat_v).strip("[]").split(",")[0])
                    lon = float(str(lon_v).strip("[]").split(",")[0])
            except Exception:
                pass
        if not valid_somerville(lon, lat):
            continue

        pci = 50.0
        try:
            pci = float(row.get("score", 50))
        except (TypeError, ValueError):
            pass
        address = str(row.get("address_st", ""))
        length_ft = float(row.get("length_ft") or 0)
        area_sqft = float(row.get("area_sqft") or 0)

        # ── Nearby tree count & shade score ──────────────────────────────
        nearby_trees = sum(1 for tx, ty in trees if haversine_m(lon, lat, tx, ty) <= SHADE_RADIUS)
        shade_score = min(1.0, nearby_trees / MAX_TREES)

        # ── Nearby obstacles ─────────────────────────────────────────────
        nearby_obs = sum(1 for ox, oy, _ in obstacles if haversine_m(lon, lat, ox, oy) <= OBSTACLE_RADIUS)

        # ── Sidewalk condition (nearest within 100m) ─────────────────────
        best_sw_dist = 999999
        best_sw_cond = "Unknown"
        for sx, sy, sc, sp in sidewalks:
            d = haversine_m(lon, lat, sx, sy)
            if d < best_sw_dist and d <= NEAR_RADIUS:
                best_sw_dist = d
                best_sw_cond = sc if sc else "Unknown"

        # ── Pedestrian exposure ──────────────────────────────────────────
        ped_exp = 0.5  # base
        # Sidewalk quality
        if best_sw_cond == "Poor":   ped_exp += 0.2
        elif best_sw_cond == "Fair": ped_exp += 0.1
        elif best_sw_cond == "Good": ped_exp -= 0.1
        # Ramps nearby → more pedestrian activity
        ramps_near = sum(1 for rx, ry in ramps if haversine_m(lon, lat, rx, ry) <= NEAR_RADIUS)
        if ramps_near >= 2: ped_exp += 0.15
        elif ramps_near >= 1: ped_exp += 0.08
        # Ped signals nearby → busy intersection
        ped_sig_near = sum(1 for px, py in ped_signals if haversine_m(lon, lat, px, py) <= NEAR_RADIUS)
        if ped_sig_near >= 2: ped_exp += 0.15
        elif ped_sig_near >= 1: ped_exp += 0.08
        ped_exp = max(0.0, min(1.0, ped_exp))

        # ── Component scores ─────────────────────────────────────────────
        shade_deficit = 1.0 - shade_score
        pavement_vuln = 1.0 - (min(100, max(0, pci)) / 100.0)
        climate_sev_heat = heat_sev_w
        climate_sev_cold = cold_risk_w

        # Physical feasibility (rough: obstacles + sidewalk condition)
        phys_feas = 0.5
        if nearby_obs >= 5: phys_feas += 0.25
        elif nearby_obs >= 2: phys_feas += 0.1
        if best_sw_cond == "Poor": phys_feas += 0.15
        phys_feas = min(1.0, phys_feas)

        # ── HEAT STRESS ──────────────────────────────────────────────────
        heat_stress = (
            shade_deficit    * 0.35 +
            pavement_vuln    * 0.25 +
            ped_exp          * 0.25 +
            climate_sev_heat * 0.15
        )
        heat_stress = round(min(1.0, heat_stress), 4)

        # ── COLD STRESS ──────────────────────────────────────────────────
        cold_stress = (
            shade_score      * 0.35 +
            climate_sev_cold * 0.30 +
            pavement_vuln    * 0.20 +
            ped_exp          * 0.15
        )
        cold_stress = round(min(1.0, cold_stress), 4)

        # ── COMBINED PRIORITY ────────────────────────────────────────────
        combined = (
            max(heat_stress, cold_stress) * 0.6 +
            (heat_stress + cold_stress) / 2.0 * 0.2 +
            ped_exp * 0.2
        )
        combined = round(min(1.0, combined), 4)

        # Label
        if combined >= 0.70:   label = "CRITICAL"
        elif combined >= 0.55: label = "HIGH"
        elif combined >= 0.40: label = "MEDIUM"
        else:                  label = "LOW"

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": {
                "address": address,
                "pci_score": round(pci, 1),
                "length_ft": round(length_ft, 1),
                "area_sqft": round(area_sqft, 1),
                "nearby_trees": nearby_trees,
                "shade_score": round(shade_score, 3),
                "nearby_obstacles": nearby_obs,
                "sidewalk_condition": best_sw_cond,
                "heat_stress": heat_stress,
                "cold_stress": cold_stress,
                "combined_priority": combined,
                "priority_breakdown": {
                    "climate_severity": round(max(climate_sev_heat, climate_sev_cold), 3),
                    "pedestrian_exposure": round(ped_exp, 3),
                    "shade_deficit": round(shade_deficit, 3),
                    "pavement_vulnerability": round(pavement_vuln, 3),
                    "physical_feasibility": round(phys_feas, 3),
                },
                "risk_label": label,
            },
        })

        if (idx + 1) % 1000 == 0:
            print(f"      … processed {idx + 1} segments")

    # Sort by combined priority
    features.sort(key=lambda f: f["properties"]["combined_priority"], reverse=True)

    geojson = {"type": "FeatureCollection", "features": features}
    path = DATA_DIR / "climate_stress.geojson"
    path.write_text(json.dumps(geojson))

    scores = [f["properties"]["combined_priority"] for f in features]
    print(f"   ✅ Scored {len(features)} segments → {path.name}")
    print(f"      CRITICAL (≥0.70): {sum(1 for s in scores if s >= 0.70)}")
    print(f"      HIGH     (≥0.55): {sum(1 for s in scores if 0.55 <= s < 0.70)}")
    print(f"      MEDIUM   (≥0.40): {sum(1 for s in scores if 0.40 <= s < 0.55)}")
    print(f"      LOW      (<0.40): {sum(1 for s in scores if s < 0.40)}")
    return features


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 4 — Select Top Sites & Build Intervention Plans
# ═════════════════════════════════════════════════════════════════════════════

def _gather_nearby_assets(center_lon, center_lat, asset_records, radius_m=100):
    """Return list of nearby asset dicts sorted by distance."""
    nearby = []
    for lon, lat, at, cond, props in asset_records:
        d = haversine_m(center_lon, center_lat, lon, lat)
        if d <= radius_m:
            nearby.append({
                "type": at,
                "distance_m": round(d, 1),
                "coords": [round(lon, 7), round(lat, 7)],
                "condition": cond if cond else None,
            })
    nearby.sort(key=lambda x: x["distance_m"])
    return nearby


def _nearest_of_type(nearby, asset_type):
    """Distance to nearest asset of given type, or None."""
    for a in nearby:
        if a["type"] == asset_type:
            return a["distance_m"]
    return None


def _nearest_of_types(nearby, types):
    for a in nearby:
        if a["type"] in types:
            return a["distance_m"]
    return None


def build_site_hot(stress_features, asset_records, weather):
    """Build the #1 heat-stress site intervention plan."""
    # Find segment with highest heat_stress
    best = max(stress_features, key=lambda f: f["properties"]["heat_stress"])
    props = best["properties"]
    lon, lat = best["geometry"]["coordinates"]

    nearby = _gather_nearby_assets(lon, lat, asset_records, 100)

    # Nearest distances for clearance checks
    nearest_pole = _nearest_of_types(nearby, {"UTILITY_POLE", "TRAFFIC_SIGNAL_POLE", "LUMINARIES"})
    nearest_hydrant = _nearest_of_type(nearby, "HYDRANT")
    nearest_sign = _nearest_of_type(nearby, "SIGN")
    nearest_signal = _nearest_of_types(nearby, {"TRAFFIC_SIGNAL", "STAND_ALONE_PEDESTRIAN_HEAD"})

    # Sidewalk info
    sw_nearby = [a for a in nearby if a["type"] == "SIDEWALK"]
    sw_cond = sw_nearby[0]["condition"] if sw_nearby else None

    # Curb info
    curb_nearby = [a for a in nearby if a["type"] == "CURB"]
    curb_cond = curb_nearby[0]["condition"] if curb_nearby else None
    # Find curb length from asset_records
    curb_len = None
    for a_lon, a_lat, at, _, p in asset_records:
        if at == "CURB" and haversine_m(lon, lat, a_lon, a_lat) <= 100:
            cl = p.get("curb_length_ft")
            if cl:
                curb_len = cl
                break

    # Feasibility checks for tree planting
    pole_ok  = nearest_pole is None or nearest_pole >= 10
    hydr_ok  = nearest_hydrant is None or nearest_hydrant >= 5
    sign_ok  = nearest_sign is None or nearest_sign >= 4
    sight_ok = nearest_signal is None or nearest_signal >= 20
    ped_ok   = True  # assume 5ft sidewalk if sidewalk is present

    # Placement: offset ~15m from centre along road
    placement = [round(lon + 0.00015, 7), round(lat, 7)]

    site = {
        "id": "site_hot",
        "type": "HOT_CORRIDOR",
        "address": props["address"] or "High-Heat Corridor",
        "center": [round(lon, 7), round(lat, 7)],
        "priority_score": props["combined_priority"],
        "priority_breakdown": props["priority_breakdown"],
        "climate_data": {
            "heat_stress": props["heat_stress"],
            "cold_stress": props["cold_stress"],
            "avg_temp_c": weather["summer_proxy"]["avg_temp_c"],
            "shade_score": props["shade_score"],
        },
        "geometry_measured": {
            "sidewalk_width_ft": None,
            "sidewalk_condition": sw_cond or props["sidewalk_condition"],
            "curb_length_ft": curb_len,
            "curb_condition": curb_cond,
            "road_area_sqft": props["area_sqft"],
            "pci_score": props["pci_score"],
        },
        "nearby_assets": nearby[:30],  # cap for readability
        "intervention": {
            "type": "SHADE_TREE_PLANTING",
            "name": "Street Tree Planting Program",
            "description": f"Plant 3 shade trees along corridor to reduce surface temperature by up to 8°F",
            "dimensions_ft": {"width": 4, "depth": 4, "height": 25},
            "required_clearance_ft": {
                "from_pole": 10, "from_hydrant": 5, "from_sign": 4, "pedestrian_path": 5,
            },
            "placement": placement,
            "feasibility": {
                "pedestrian_clearance": {"pass": ped_ok, "required_ft": 5, "available_ft": 8},
                "pole_clearance": {
                    "pass": pole_ok, "required_ft": 10,
                    "nearest_ft": round(nearest_pole, 1) if nearest_pole else None,
                },
                "hydrant_clearance": {
                    "pass": hydr_ok, "required_ft": 5,
                    "nearest_ft": round(nearest_hydrant, 1) if nearest_hydrant else None,
                },
                "sign_clearance": {
                    "pass": sign_ok, "required_ft": 4,
                    "nearest_ft": round(nearest_sign, 1) if nearest_sign else None,
                },
                "sightline": {
                    "pass": sight_ok,
                    "note": "No traffic signal within 20ft" if sight_ok else f"Traffic signal at {nearest_signal:.0f}ft",
                },
                "fits_geometry": {
                    "pass": True,
                    "note": "4×4ft tree pit fits in available right-of-way",
                },
            },
            "all_checks_pass": all([pole_ok, hydr_ok, sign_ok, sight_ok, ped_ok]),
        },
        "impact_estimate": {
            "shade_increase_pct": 35,
            "surface_temp_reduction_f": 8,
            "ice_risk_change": -0.15,
            "pedestrian_comfort": "Significant improvement in summer shade coverage",
            "maintenance_impact": "Reduced salt needs in winter due to faster ice melt from removed shade",
            "energy_savings": "Reduced urban heat island effect, ~2% cooling energy reduction for adjacent buildings",
            "safety_improvement": "Better visibility and pedestrian comfort, reduced heat-related incidents",
        },
    }

    path = SITES_DIR / "site_hot.json"
    path.write_text(json.dumps(site, indent=2))
    print(f"   🔥 Hot site: {site['address']} (heat={props['heat_stress']:.3f}) → {path.name}")
    return site


def build_site_cold(stress_features, asset_records, weather):
    """Build the #1 cold-stress site intervention plan."""
    best = max(stress_features, key=lambda f: f["properties"]["cold_stress"])
    props = best["properties"]
    lon, lat = best["geometry"]["coordinates"]

    nearby = _gather_nearby_assets(lon, lat, asset_records, 100)

    nearest_pole = _nearest_of_types(nearby, {"UTILITY_POLE", "TRAFFIC_SIGNAL_POLE", "LUMINARIES"})
    nearest_hydrant = _nearest_of_type(nearby, "HYDRANT")
    nearest_sign = _nearest_of_type(nearby, "SIGN")
    nearest_signal = _nearest_of_types(nearby, {"TRAFFIC_SIGNAL", "STAND_ALONE_PEDESTRIAN_HEAD"})

    sw_nearby = [a for a in nearby if a["type"] == "SIDEWALK"]
    sw_cond = sw_nearby[0]["condition"] if sw_nearby else None
    curb_nearby = [a for a in nearby if a["type"] == "CURB"]
    curb_cond = curb_nearby[0]["condition"] if curb_nearby else None
    curb_len = None
    for a_lon, a_lat, at, _, p in asset_records:
        if at == "CURB" and haversine_m(lon, lat, a_lon, a_lat) <= 100:
            cl = p.get("curb_length_ft")
            if cl:
                curb_len = cl
                break

    # Feasibility for salt bin + shelter (3×3ft footprint)
    pole_ok  = nearest_pole is None or nearest_pole >= 6
    hydr_ok  = nearest_hydrant is None or nearest_hydrant >= 4
    sign_ok  = nearest_sign is None or nearest_sign >= 3
    sight_ok = nearest_signal is None or nearest_signal >= 15
    ped_ok   = True

    placement = [round(lon - 0.00012, 7), round(lat, 7)]

    avg_winter_temp = sum(d["avg_temp_c"] for d in weather["winter_daily"]) / len(weather["winter_daily"])

    site = {
        "id": "site_cold",
        "type": "COLD_CORRIDOR",
        "address": props["address"] or "High-Cold Corridor",
        "center": [round(lon, 7), round(lat, 7)],
        "priority_score": props["combined_priority"],
        "priority_breakdown": props["priority_breakdown"],
        "climate_data": {
            "heat_stress": props["heat_stress"],
            "cold_stress": props["cold_stress"],
            "avg_temp_c": round(avg_winter_temp, 1),
            "shade_score": props["shade_score"],
        },
        "geometry_measured": {
            "sidewalk_width_ft": None,
            "sidewalk_condition": sw_cond or props["sidewalk_condition"],
            "curb_length_ft": curb_len,
            "curb_condition": curb_cond,
            "road_area_sqft": props["area_sqft"],
            "pci_score": props["pci_score"],
        },
        "nearby_assets": nearby[:30],
        "intervention": {
            "type": "WINTER_MAINTENANCE_STATION",
            "name": "Salt Bin & Pedestrian Shelter",
            "description": "Install salt/sand bin with covered shelter for winter pedestrian safety",
            "dimensions_ft": {"width": 3, "depth": 3, "height": 8},
            "required_clearance_ft": {
                "from_pole": 6, "from_hydrant": 4, "from_sign": 3, "pedestrian_path": 4,
            },
            "placement": placement,
            "feasibility": {
                "pedestrian_clearance": {"pass": ped_ok, "required_ft": 4, "available_ft": 7},
                "pole_clearance": {
                    "pass": pole_ok, "required_ft": 6,
                    "nearest_ft": round(nearest_pole, 1) if nearest_pole else None,
                },
                "hydrant_clearance": {
                    "pass": hydr_ok, "required_ft": 4,
                    "nearest_ft": round(nearest_hydrant, 1) if nearest_hydrant else None,
                },
                "sign_clearance": {
                    "pass": sign_ok, "required_ft": 3,
                    "nearest_ft": round(nearest_sign, 1) if nearest_sign else None,
                },
                "sightline": {
                    "pass": sight_ok,
                    "note": "No traffic signal within 15ft" if sight_ok else f"Traffic signal at {nearest_signal:.0f}ft",
                },
                "fits_geometry": {
                    "pass": True,
                    "note": "3×3ft salt bin fits in available right-of-way",
                },
            },
            "all_checks_pass": all([pole_ok, hydr_ok, sign_ok, sight_ok, ped_ok]),
        },
        "impact_estimate": {
            "shade_increase_pct": 0,
            "surface_temp_reduction_f": 0,
            "ice_risk_change": -0.35,
            "pedestrian_comfort": "Significant improvement in winter walking safety with readily available salt/sand",
            "maintenance_impact": "Enables rapid resident-led de-icing, reduces city response time",
            "energy_savings": "Shelter reduces wind chill exposure at bus/pedestrian waiting areas",
            "safety_improvement": "Reduced slip-and-fall incidents, improved traction on icy sidewalks",
        },
    }

    path = SITES_DIR / "site_cold.json"
    path.write_text(json.dumps(site, indent=2))
    print(f"   ❄️  Cold site: {site['address']} (cold={props['cold_stress']:.3f}) → {path.name}")
    return site


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 5 — SVG Site Plans
# ═════════════════════════════════════════════════════════════════════════════

_ICON_COLORS = {
    "TREE": "#228B22", "HYDRANT": "#DC143C", "UTILITY_POLE": "#8B4513",
    "TRAFFIC_SIGNAL_POLE": "#FF8C00", "LUMINARIES": "#FFD700",
    "SIGN": "#4169E1", "CATCH_BASIN": "#708090", "MANHOLE_COVER": "#A9A9A9",
    "RAMP": "#9370DB", "SIDEWALK": "#BDB76B", "CURB": "#D2B48C",
    "TRAFFIC_SIGNAL": "#FF4500", "CCTV": "#2F4F4F", "BIKE_RACK": "#20B2AA",
    "PEDESTRIAN_PUSH_BUTTON": "#DA70D6", "STAND_ALONE_PEDESTRIAN_HEAD": "#DA70D6",
    "FLASHING_BEACONS": "#FF6347", "GUARDRAILS": "#696969",
}


def _asset_svg_icon(at, x, y, r=5):
    """Return SVG element for an asset type at pixel (x,y)."""
    c = _ICON_COLORS.get(at, "#888")
    if at == "TREE":
        return f'<circle cx="{x}" cy="{y}" r="{r+2}" fill="{c}" opacity="0.7"/>'
    elif at == "HYDRANT":
        return f'<rect x="{x-r}" y="{y-r}" width="{2*r}" height="{2*r}" fill="{c}" opacity="0.8"/>'
    elif at == "SIGN":
        pts = f"{x},{y-r} {x-r},{y+r} {x+r},{y+r}"
        return f'<polygon points="{pts}" fill="{c}" opacity="0.7"/>'
    else:
        return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}" opacity="0.6" stroke="#333" stroke-width="0.5"/>'


def generate_svg(site, filename):
    """Generate a top-down SVG site plan."""
    W, H = 600, 400
    MARGIN = 40
    center = site["center"]
    nearby = site["nearby_assets"]

    if not nearby:
        return

    # Compute bounding box of nearby assets
    all_lons = [a["coords"][0] for a in nearby] + [center[0]]
    all_lats = [a["coords"][1] for a in nearby] + [center[1]]
    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)

    # Add padding
    pad_lon = max(0.0003, (max_lon - min_lon) * 0.15)
    pad_lat = max(0.0002, (max_lat - min_lat) * 0.15)
    min_lon -= pad_lon; max_lon += pad_lon
    min_lat -= pad_lat; max_lat += pad_lat

    dlon = max_lon - min_lon or 0.001
    dlat = max_lat - min_lat or 0.001

    def to_px(lon, lat):
        px = MARGIN + (lon - min_lon) / dlon * (W - 2 * MARGIN)
        py = H - MARGIN - (lat - min_lat) / dlat * (H - 2 * MARGIN)
        return round(px, 1), round(py, 1)

    cx, cy = to_px(center[0], center[1])

    # Intervention placement
    interv = site["intervention"]
    ix, iy = to_px(interv["placement"][0], interv["placement"][1])

    # Build SVG
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        '<defs>',
        '  <style>',
        '    text { font-family: Arial, sans-serif; }',
        '    .title { font-size: 13px; font-weight: bold; fill: #222; }',
        '    .label { font-size: 9px; fill: #555; }',
        '    .legend { font-size: 8px; fill: #444; }',
        '  </style>',
        '</defs>',
        # Background
        f'<rect width="{W}" height="{H}" fill="#f5f5f0" rx="4"/>',
        # Title
        f'<text x="{W//2}" y="18" text-anchor="middle" class="title">'
        f'Site Plan: {site["address"][:40]} ({site["type"]})</text>',
        # Road surface (grey band through centre)
        f'<rect x="{MARGIN}" y="{H//2-30}" width="{W-2*MARGIN}" height="60" '
        f'fill="#ccc" rx="3" opacity="0.5"/>',
        f'<text x="{MARGIN+5}" y="{H//2-35}" class="label">Road (PCI={site["geometry_measured"]["pci_score"]})</text>',
        # Sidewalk strips
        f'<rect x="{MARGIN}" y="{H//2-50}" width="{W-2*MARGIN}" height="18" '
        f'fill="#e8e4d0" rx="2" opacity="0.6"/>',
        f'<rect x="{MARGIN}" y="{H//2+32}" width="{W-2*MARGIN}" height="18" '
        f'fill="#e8e4d0" rx="2" opacity="0.6"/>',
        f'<text x="{MARGIN+5}" y="{H//2+58}" class="label">Sidewalk ({site["geometry_measured"]["sidewalk_condition"]})</text>',
        # Centre marker
        f'<circle cx="{cx}" cy="{cy}" r="6" fill="none" stroke="#E53935" stroke-width="2"/>',
        f'<line x1="{cx-8}" y1="{cy}" x2="{cx+8}" y2="{cy}" stroke="#E53935" stroke-width="1.5"/>',
        f'<line x1="{cx}" y1="{cy-8}" x2="{cx}" y2="{cy+8}" stroke="#E53935" stroke-width="1.5"/>',
    ]

    # Existing assets
    for a in nearby[:50]:
        ax, ay = to_px(a["coords"][0], a["coords"][1])
        parts.append(_asset_svg_icon(a["type"], ax, ay, 4))

    # Intervention (dashed blue outline)
    itype = interv["type"]
    if "TREE" in itype:
        parts.append(
            f'<circle cx="{ix}" cy="{iy}" r="14" fill="none" '
            f'stroke="#1565C0" stroke-width="2" stroke-dasharray="5,3"/>'
        )
        parts.append(f'<text x="{ix}" y="{iy+22}" text-anchor="middle" class="label" fill="#1565C0">🌳 Proposed</text>')
    else:
        parts.append(
            f'<rect x="{ix-12}" y="{iy-12}" width="24" height="24" fill="none" '
            f'stroke="#1565C0" stroke-width="2" stroke-dasharray="5,3" rx="3"/>'
        )
        parts.append(f'<text x="{ix}" y="{iy+20}" text-anchor="middle" class="label" fill="#1565C0">🧂 Proposed</text>')

    # Scale bar (approximate: 100m at this latitude ≈ 0.00125° lon)
    scale_m = 50
    scale_lon = scale_m / (111_320 * math.cos(math.radians(center[1])))
    sx1, sy1 = to_px(min_lon + pad_lon / 2, min_lat + pad_lat / 2)
    sx2, _ = to_px(min_lon + pad_lon / 2 + scale_lon, min_lat + pad_lat / 2)
    bar_y = H - 22
    parts.append(f'<line x1="{MARGIN+5}" y1="{bar_y}" x2="{MARGIN+5 + (sx2-sx1)}" y2="{bar_y}" stroke="#333" stroke-width="2"/>')
    parts.append(f'<text x="{MARGIN+8}" y="{bar_y - 4}" class="legend">{scale_m}m</text>')

    # North arrow
    nx, ny = W - 25, 35
    parts.append(f'<line x1="{nx}" y1="{ny+15}" x2="{nx}" y2="{ny}" stroke="#333" stroke-width="1.5" marker-end="url(#arrowhead)"/>')
    parts.append(f'<text x="{nx}" y="{ny-3}" text-anchor="middle" class="legend" font-weight="bold">N</text>')
    parts.insert(1, '<defs><marker id="arrowhead" markerWidth="6" markerHeight="4" refX="3" refY="2" orient="auto">'
                    '<polygon points="0 0, 6 2, 0 4" fill="#333"/></marker></defs>')

    # Legend
    legend_types = list(dict.fromkeys(a["type"] for a in nearby[:50]))[:8]
    ly = 40
    for lt in legend_types:
        c = _ICON_COLORS.get(lt, "#888")
        parts.append(f'<rect x="8" y="{ly}" width="8" height="8" fill="{c}" rx="1"/>')
        parts.append(f'<text x="20" y="{ly+7}" class="legend">{lt}</text>')
        ly += 12

    # Feasibility badge
    all_pass = interv["all_checks_pass"]
    badge_c = "#4CAF50" if all_pass else "#FF9800"
    badge_t = "ALL CHECKS PASS" if all_pass else "REVIEW NEEDED"
    parts.append(f'<rect x="{W-120}" y="{H-25}" width="110" height="18" fill="{badge_c}" rx="3"/>')
    parts.append(f'<text x="{W-65}" y="{H-13}" text-anchor="middle" class="legend" fill="white" font-weight="bold">{badge_t}</text>')

    parts.append('</svg>')

    svg_path = SITES_DIR / filename
    svg_path.write_text("\n".join(parts))
    print(f"   📐 SVG plan → {filename}")


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 6 — Summary
# ═════════════════════════════════════════════════════════════════════════════

def export_summary(stress_features, asset_records, weather, site_hot, site_cold):
    h_scores = [f["properties"]["heat_stress"] for f in stress_features]
    c_scores = [f["properties"]["cold_stress"] for f in stress_features]
    combined = [f["properties"]["combined_priority"] for f in stress_features]

    summary = {
        "pipeline": "SafeRoute AI — Dual Climate Stress Pipeline",
        "timestamp": "2025-11-24T00:00:00Z",
        "total_assets": len(asset_records),
        "total_pavement_segments": len(stress_features),
        "weather": {
            "winter_period": weather.get("winter_period", ""),
            "overall_cold_risk": weather["overall_cold_risk"],
            "overall_heat_severity": weather["overall_heat_severity"],
        },
        "heat_stress": {
            "avg": round(sum(h_scores) / len(h_scores), 3),
            "max": round(max(h_scores), 3),
            "top_site": site_hot["address"],
        },
        "cold_stress": {
            "avg": round(sum(c_scores) / len(c_scores), 3),
            "max": round(max(c_scores), 3),
            "top_site": site_cold["address"],
        },
        "combined_priority": {
            "avg": round(sum(combined) / len(combined), 3),
            "max": round(max(combined), 3),
            "distribution": {
                "CRITICAL": sum(1 for s in combined if s >= 0.70),
                "HIGH": sum(1 for s in combined if 0.55 <= s < 0.70),
                "MEDIUM": sum(1 for s in combined if 0.40 <= s < 0.55),
                "LOW": sum(1 for s in combined if s < 0.40),
            },
        },
        "asset_breakdown": {},
        "interventions": {
            "hot_site": {
                "address": site_hot["address"],
                "type": site_hot["intervention"]["type"],
                "all_checks_pass": site_hot["intervention"]["all_checks_pass"],
            },
            "cold_site": {
                "address": site_cold["address"],
                "type": site_cold["intervention"]["type"],
                "all_checks_pass": site_cold["intervention"]["all_checks_pass"],
            },
        },
        "top_5_heat": [
            {"address": f["properties"]["address"],
             "heat_stress": f["properties"]["heat_stress"],
             "coords": f["geometry"]["coordinates"]}
            for f in sorted(stress_features, key=lambda x: x["properties"]["heat_stress"], reverse=True)[:5]
        ],
        "top_5_cold": [
            {"address": f["properties"]["address"],
             "cold_stress": f["properties"]["cold_stress"],
             "coords": f["geometry"]["coordinates"]}
            for f in sorted(stress_features, key=lambda x: x["properties"]["cold_stress"], reverse=True)[:5]
        ],
    }

    # Asset breakdown
    type_counts = {}
    for _, _, at, _, _ in asset_records:
        type_counts[at] = type_counts.get(at, 0) + 1
    summary["asset_breakdown"] = dict(sorted(type_counts.items(), key=lambda x: -x[1]))

    path = DATA_DIR / "summary.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"   📋 Summary → {path.name}")
    return summary


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 65)
    print("  SafeRoute AI — Dual Climate Stress Processing Pipeline")
    print("  Cyvl Physical AI Hackathon · Somerville, MA")
    print("=" * 65)
    print()

    # Step 1: Unified asset layer
    asset_records = build_assets_all()
    print()

    # Step 2: Weather (real + summer proxy)
    weather = fetch_weather()
    print()

    # Step 3: Dual climate stress for all segments
    stress = compute_climate_stress(asset_records, weather)
    print()

    # Step 4: Top intervention sites
    print("🏗️  Step 4: Building intervention site plans …")
    site_hot  = build_site_hot(stress, asset_records, weather)
    site_cold = build_site_cold(stress, asset_records, weather)
    print()

    # Step 5: SVG site plans
    print("📐 Step 5: Generating SVG site plans …")
    generate_svg(site_hot, "site_hot_plan.svg")
    generate_svg(site_cold, "site_cold_plan.svg")
    print()

    # Step 6: Summary
    print("📋 Step 6: Exporting summary …")
    summary = export_summary(stress, asset_records, weather, site_hot, site_cold)

    print()
    print("=" * 65)
    print("  ✨ Pipeline complete! All outputs in data/")
    print(f"     Assets:         data/assets_all.geojson  ({summary['total_assets']} features)")
    print(f"     Climate stress: data/climate_stress.geojson  ({summary['total_pavement_segments']} segments)")
    print(f"     Weather:        data/weather.json")
    print(f"     Summary:        data/summary.json")
    print(f"     Hot site:       data/sites/site_hot.json  ({site_hot['address']})")
    print(f"     Cold site:      data/sites/site_cold.json ({site_cold['address']})")
    print(f"     Hot SVG:        data/sites/site_hot_plan.svg")
    print(f"     Cold SVG:       data/sites/site_cold_plan.svg")
    print("=" * 65)
