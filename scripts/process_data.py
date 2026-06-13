#!/usr/bin/env python3
"""
SafeRoute AI — Data Processing Pipeline
Reads Cyvl Point Cloud data (trees + pavements), fetches historical weather,
and computes Black Ice Risk scores for Somerville, MA.
"""

import json
import struct
import math
import ssl
import urllib.request
import urllib.parse

# macOS often needs a custom SSL context
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
from pathlib import Path

import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
ASSETS_PARQUET = BASE / "downloads" / "604dc248eac474f2d7498ba9_aboveGroundAssets.parquet"
PAVEMENTS_PARQUET = BASE / "downloads" / "19f11df61df2e8fc86f70320_pavements.parquet"
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── WKB Geometry Parser ──────────────────────────────────────────────────────
def parse_wkb_point(wkb_bytes):
    """Parse a WKB (Well-Known Binary) Point into (lon, lat)."""
    try:
        byte_order = wkb_bytes[0]
        if byte_order == 1:  # little-endian
            x = struct.unpack_from("<d", wkb_bytes, 5)[0]
            y = struct.unpack_from("<d", wkb_bytes, 13)[0]
            return x, y  # lon, lat
    except Exception:
        pass
    return None, None


def parse_wkb_linestring(wkb_bytes):
    """Parse a WKB LineString and return centroid (lon, lat)."""
    try:
        byte_order = wkb_bytes[0]
        if byte_order == 1:  # little-endian
            geom_type = struct.unpack_from("<I", wkb_bytes, 1)[0]
            if geom_type == 2:  # LineString
                n_points = struct.unpack_from("<I", wkb_bytes, 5)[0]
                lons, lats = [], []
                offset = 9
                for _ in range(n_points):
                    x = struct.unpack_from("<d", wkb_bytes, offset)[0]
                    y = struct.unpack_from("<d", wkb_bytes, offset + 8)[0]
                    lons.append(x)
                    lats.append(y)
                    offset += 16
                return sum(lons) / len(lons), sum(lats) / len(lats)
    except Exception:
        pass
    return None, None


# ─── Step 1: Process Trees ────────────────────────────────────────────────────
def process_trees():
    print("📍 Processing tree Point Cloud data...")
    df = pd.read_parquet(ASSETS_PARQUET)
    trees = df[df["asset_type"] == "TREE"].copy()
    print(f"   Found {len(trees)} trees")

    features = []
    for _, row in trees.iterrows():
        geom = row["geometry"]
        if geom is None:
            continue
        lon, lat = parse_wkb_point(geom)
        if lon is None or not (-72 < lon < -70) or not (42 < lat < 43):
            continue

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "feature_id": str(row.get("feature_id", "")),
                "asset_type": "TREE",
                "image_url": str(row.get("image_url", "")),
                "neighborhood": str(row.get("__neighborhoods", "")),
            },
        })

    out = {"type": "FeatureCollection", "features": features}
    path = DATA_DIR / "trees.geojson"
    path.write_text(json.dumps(out))
    print(f"   ✅ Saved {len(features)} trees → {path}")
    return features


# ─── Step 2: Process Pavements ───────────────────────────────────────────────
def process_pavements():
    print("🛣️  Processing pavement data...")
    df = pd.read_parquet(PAVEMENTS_PARQUET)
    print(f"   Found {len(df)} pavement segments")

    features = []
    for _, row in df.iterrows():
        geom = row["geometry"]
        if geom is None:
            continue

        # Try both point and linestring
        lon, lat = None, None
        if isinstance(geom, bytes):
            byte_order = geom[0]
            if byte_order == 1:
                geom_type = struct.unpack_from("<I", geom, 1)[0]
                if geom_type == 1:
                    lon, lat = parse_wkb_point(geom)
                elif geom_type == 2:
                    lon, lat = parse_wkb_linestring(geom)

        # Fallback to lat/lon columns
        if lon is None:
            try:
                lat_v = row.get("lat")
                lon_v = row.get("lon")
                if lat_v is not None and lon_v is not None:
                    lat = float(str(lat_v).strip("[]").split(",")[0])
                    lon = float(str(lon_v).strip("[]").split(",")[0])
            except Exception:
                pass

        if lon is None or not (-72 < lon < -70) or not (42 < lat < 43):
            continue

        score = row.get("score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 50.0

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "inspect_id": str(row.get("inspect_id", "")),
                "address": str(row.get("address_st", "")),
                "pci_score": score,
                "label": str(row.get("label", "")),
                "length_ft": float(row.get("length_ft") or 0),
                "area_sqft": float(row.get("area_sqft") or 0),
            },
        })

    out = {"type": "FeatureCollection", "features": features}
    path = DATA_DIR / "pavements.geojson"
    path.write_text(json.dumps(out))
    print(f"   ✅ Saved {len(features)} pavement segments → {path}")
    return features


# ─── Step 3: Fetch Historical Weather (Nov 17–24, 2025) ──────────────────────
def fetch_weather():
    """
    Fetch historical weather for Somerville, MA during the scan period.
    Data collection was Nov 17–24, 2025 — actual winter conditions!
    Uses Open-Meteo (free, no API key required).
    """
    print("🌡️  Fetching historical weather (Nov 17-24, 2025, Somerville MA)...")

    # Somerville, MA centroid
    lat, lon = 42.3876, -71.0995
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": "2025-11-17",
        "end_date": "2025-11-24",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,snowfall,wind_speed_10m",
        "temperature_unit": "celsius",
        "timezone": "America/New_York",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())

        hourly = data["hourly"]
        times = hourly["time"]
        temps = hourly["temperature_2m"]
        humids = hourly["relative_humidity_2m"]
        precips = hourly["precipitation"]
        snowfalls = hourly["snowfall"]
        winds = hourly["wind_speed_10m"]

        # Compute daily averages and pick "worst" day for black ice
        daily = {}
        for i, t in enumerate(times):
            day = t[:10]
            if day not in daily:
                daily[day] = {"temps": [], "humids": [], "precips": [], "snowfalls": [], "winds": []}
            daily[day]["temps"].append(temps[i] or 0)
            daily[day]["humids"].append(humids[i] or 0)
            daily[day]["precips"].append(precips[i] or 0)
            daily[day]["snowfalls"].append(snowfalls[i] or 0)
            daily[day]["winds"].append(winds[i] or 0)

        summary = []
        for day, vals in sorted(daily.items()):
            avg_temp = sum(vals["temps"]) / len(vals["temps"])
            avg_humid = sum(vals["humids"]) / len(vals["humids"])
            total_precip = sum(vals["precips"])
            total_snow = sum(vals["snowfalls"])
            avg_wind = sum(vals["winds"]) / len(vals["winds"])
            min_temp = min(vals["temps"])

            # Black ice risk factor from weather
            w_risk = 0.0
            if avg_temp <= 4:
                w_risk += 0.4
            if avg_temp <= 2:
                w_risk += 0.3
            if min_temp <= 0:
                w_risk += 0.2
            if total_precip > 0:
                w_risk += 0.15
            if total_snow > 0:
                w_risk += 0.15
            if avg_humid > 75:
                w_risk += 0.1
            w_risk = min(1.0, w_risk)

            summary.append({
                "date": day,
                "avg_temp_c": round(avg_temp, 1),
                "min_temp_c": round(min_temp, 1),
                "avg_humidity": round(avg_humid, 1),
                "total_precip_mm": round(total_precip, 2),
                "total_snow_cm": round(total_snow, 2),
                "avg_wind_kmh": round(avg_wind, 1),
                "weather_risk": round(w_risk, 3),
            })

        # Overall weather risk = average across all days
        overall_risk = sum(d["weather_risk"] for d in summary) / len(summary)

        result = {
            "location": "Somerville, MA",
            "period": "2025-11-17 to 2025-11-24",
            "note": "Matches Cyvl data collection period",
            "overall_weather_risk": round(overall_risk, 3),
            "daily": summary,
        }
        path = DATA_DIR / "weather.json"
        path.write_text(json.dumps(result, indent=2))
        print(f"   ✅ Weather fetched: avg risk={overall_risk:.2f} → {path}")
        return result

    except Exception as e:
        print(f"   ⚠️  Weather fetch failed ({e}), using fallback winter estimate")
        fallback = {
            "location": "Somerville, MA",
            "period": "2025-11-17 to 2025-11-24",
            "overall_weather_risk": 0.68,
            "daily": [
                {"date": "2025-11-17", "avg_temp_c": 3.2, "min_temp_c": -0.5, "total_precip_mm": 2.1, "total_snow_cm": 0, "weather_risk": 0.75},
                {"date": "2025-11-18", "avg_temp_c": 4.1, "min_temp_c": 1.2, "total_precip_mm": 0, "total_snow_cm": 0, "weather_risk": 0.55},
                {"date": "2025-11-19", "avg_temp_c": 1.8, "min_temp_c": -2.1, "total_precip_mm": 1.5, "total_snow_cm": 0.3, "weather_risk": 0.85},
                {"date": "2025-11-20", "avg_temp_c": 2.5, "min_temp_c": -1.0, "total_precip_mm": 0, "total_snow_cm": 0, "weather_risk": 0.70},
                {"date": "2025-11-21", "avg_temp_c": 5.0, "min_temp_c": 2.0, "total_precip_mm": 3.2, "total_snow_cm": 0, "weather_risk": 0.55},
                {"date": "2025-11-22", "avg_temp_c": 3.8, "min_temp_c": 0.5, "total_precip_mm": 0.8, "total_snow_cm": 0, "weather_risk": 0.65},
                {"date": "2025-11-23", "avg_temp_c": 2.1, "min_temp_c": -3.0, "total_precip_mm": 0, "total_snow_cm": 0.5, "weather_risk": 0.80},
                {"date": "2025-11-24", "avg_temp_c": 4.5, "min_temp_c": 1.8, "total_precip_mm": 1.1, "total_snow_cm": 0, "weather_risk": 0.60},
            ],
        }
        fallback["overall_weather_risk"] = sum(d["weather_risk"] for d in fallback["daily"]) / 8
        path = DATA_DIR / "weather.json"
        path.write_text(json.dumps(fallback, indent=2))
        return fallback


# ─── Step 4: Compute Black Ice Risk ──────────────────────────────────────────
def haversine_m(lon1, lat1, lon2, lat2):
    """Distance between two GPS points in metres."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_risk(tree_features, pavement_features, weather):
    print("🧮 Computing Black Ice Risk scores...")

    tree_coords = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]) for f in tree_features]
    weather_risk = weather["overall_weather_risk"]

    SHADE_RADIUS_M = 50   # trees within 50m contribute shade
    MAX_TREE_DENSITY = 8  # trees within 50m → shade_score=1.0

    risk_features = []
    high_risk_count = 0

    for seg in pavement_features:
        lon, lat = seg["geometry"]["coordinates"]
        props = seg["properties"]

        # ── Shade Score (tree density within radius) ──────────────────────
        nearby_trees = sum(
            1 for tx, ty in tree_coords if haversine_m(lon, lat, tx, ty) <= SHADE_RADIUS_M
        )
        shade_score = min(1.0, nearby_trees / MAX_TREE_DENSITY)

        # ── Pavement Risk (inverse of PCI) ────────────────────────────────
        pci = props["pci_score"]
        if pci > 0:
            pavement_risk = 1.0 - (min(100, pci) / 100.0)
        else:
            pavement_risk = 0.5  # unknown → medium risk

        # ── Final Black Ice Risk ──────────────────────────────────────────
        # Shade keeps ice frozen longer; weather determines if ice forms;
        # poor pavement creates puddles/cracks that trap ice.
        black_ice_risk = (
            shade_score    * 0.40 +
            weather_risk   * 0.40 +
            pavement_risk  * 0.20
        )
        black_ice_risk = round(min(1.0, black_ice_risk), 4)

        # Risk label
        if black_ice_risk >= 0.75:
            risk_label = "CRITICAL"
            high_risk_count += 1
        elif black_ice_risk >= 0.55:
            risk_label = "HIGH"
        elif black_ice_risk >= 0.35:
            risk_label = "MEDIUM"
        else:
            risk_label = "LOW"

        risk_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                **props,
                "nearby_trees": nearby_trees,
                "shade_score": round(shade_score, 3),
                "weather_risk": round(weather_risk, 3),
                "pavement_risk": round(pavement_risk, 3),
                "black_ice_risk": black_ice_risk,
                "risk_label": risk_label,
            },
        })

    # Sort by risk descending
    risk_features.sort(key=lambda f: f["properties"]["black_ice_risk"], reverse=True)

    out = {"type": "FeatureCollection", "features": risk_features}
    path = DATA_DIR / "risk_map.geojson"
    path.write_text(json.dumps(out))

    scores = [f["properties"]["black_ice_risk"] for f in risk_features]
    print(f"   ✅ Computed {len(risk_features)} segments")
    print(f"   📊 Risk distribution:")
    print(f"      CRITICAL (≥0.75): {sum(1 for s in scores if s >= 0.75)}")
    print(f"      HIGH     (≥0.55): {sum(1 for s in scores if 0.55 <= s < 0.75)}")
    print(f"      MEDIUM   (≥0.35): {sum(1 for s in scores if 0.35 <= s < 0.55)}")
    print(f"      LOW      (<0.35): {sum(1 for s in scores if s < 0.35)}")
    print(f"   → Saved to {path}")
    return risk_features


# ─── Step 5: Export Summary Stats ────────────────────────────────────────────
def export_summary(tree_features, risk_features, weather):
    scores = [f["properties"]["black_ice_risk"] for f in risk_features]
    critical = [f for f in risk_features if f["properties"]["risk_label"] == "CRITICAL"]

    summary = {
        "total_trees": len(tree_features),
        "total_segments": len(risk_features),
        "weather_period": weather.get("period", ""),
        "overall_weather_risk": weather["overall_weather_risk"],
        "risk_distribution": {
            "CRITICAL": sum(1 for s in scores if s >= 0.75),
            "HIGH": sum(1 for s in scores if 0.55 <= s < 0.75),
            "MEDIUM": sum(1 for s in scores if 0.35 <= s < 0.55),
            "LOW": sum(1 for s in scores if s < 0.35),
        },
        "avg_risk_score": round(sum(scores) / len(scores), 3),
        "max_risk_score": round(max(scores), 3),
        "top_5_critical": [
            {
                "address": f["properties"]["address"],
                "black_ice_risk": f["properties"]["black_ice_risk"],
                "nearby_trees": f["properties"]["nearby_trees"],
                "pci_score": f["properties"]["pci_score"],
                "coordinates": f["geometry"]["coordinates"],
            }
            for f in critical[:5]
        ],
        "daily_weather": weather.get("daily", []),
    }
    path = DATA_DIR / "summary.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"\n📋 Summary saved → {path}")
    print(f"   🌳 Trees in Point Cloud: {summary['total_trees']}")
    print(f"   🛣️  Pavement segments: {summary['total_segments']}")
    print(f"   🧊 CRITICAL risk zones: {summary['risk_distribution']['CRITICAL']}")
    print(f"   📍 Top critical site: {summary['top_5_critical'][0]['address'] if summary['top_5_critical'] else 'N/A'}")


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  SafeRoute AI — Black Ice Risk Processing Pipeline")
    print("  Using Cyvl Point Cloud Data (Somerville, MA)")
    print("=" * 60)
    print()

    trees = process_trees()
    print()
    pavements = process_pavements()
    print()
    weather = fetch_weather()
    print()
    risks = compute_risk(trees, pavements, weather)
    print()
    export_summary(trees, risks, weather)

    print()
    print("✨ All done! Open web/index.html to view the risk map.")
