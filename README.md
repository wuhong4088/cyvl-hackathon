# SafeRoute AI 🧊
### Black Ice Predictor — CYVL Physical AI Hackathon

> **Moving cities from reactive maintenance to proactive defense using 3D spatial intelligence.**

---

## 🎯 The Problem

Every winter, black ice causes thousands of slip-and-fall injuries and vehicle accidents on city streets. Municipal crews salt roads blindly — wasting 15–30% of budget on streets that don't need treatment while missing the dangerous ones.

**Key insight**: Persistent shade from trees and buildings prevents solar melting of ice, creating invisible danger zones. Cyvl's LiDAR point cloud data tells us exactly where every tree is.

---

## 🧠 How It Works

```
Cyvl Point Cloud (2,357 trees)
         ↓  [tree density within 50m of each road segment]
    Shade Score (40%)
         +
Open-Meteo Historical Weather API (Nov 17–24, 2025)
         ↓  [temp ≤ 2°C + precipitation + snowfall]  
   Weather Risk (40%)
         +
Cyvl Pavement PCI Score
         ↓  [cracks/depressions trap water → ice pockets]
   Pavement Risk (20%)
         =
   BLACK ICE RISK SCORE (0.0 – 1.0)
```

### Real Data from the Scan Period

| Metric | Value |
|--------|-------|
| 🌳 Trees in point cloud | 2,357 |
| 🛣️ Road segments analyzed | 5,080 |
| 🌡️ Avg temperature (Nov 17–24) | 2.3°C |
| 🌡️ Minimum temperature | -2.6°C |
| ❄️ Critical risk zones | 2 |
| 🔴 High risk zones | 82 |

---

## 🚀 Running the Demo

```bash
# 1. Process data (trees + pavements + weather)
cd cyvl-hackathon
python3 scripts/process_data.py

# 2. Start local server from project root
python3 -m http.server 8080

# 3. Open browser
open http://localhost:8080/web/
```

---

## 📁 Project Structure

```
cyvl-hackathon/
├── downloads/              # Cyvl parquet files (raw data)
│   ├── *_aboveGroundAssets.parquet   ← 2,357 trees (USED!)
│   ├── *_pavements.parquet           ← 5,080 PCI segments
│   └── ...
├── scenes/somerville/parquet/
│   └── frames.parquet      # 311k frames from Cyvl scanner
├── scripts/
│   └── process_data.py     # ETL pipeline → GeoJSON output
├── data/                   # Generated output
│   ├── trees.geojson       # 2,357 tree locations
│   ├── pavements.geojson   # 5,080 pavement segments
│   ├── weather.json        # Historical weather Nov 17–24
│   ├── risk_map.geojson    # Final risk scores
│   └── summary.json        # Statistics
└── web/
    ├── index.html           # Main app
    ├── style.css            # Dark-mode UI
    └── app.js               # MapLibre visualization
```

---

## 🗺️ Map Features

- **🔥 Risk Heatmap** — color gradient from green → orange → red
- **🌳 Tree Layer** — cyan dots showing every Cyvl-detected tree
- **❄️ Critical Pulse** — animated rings on the 2 highest-risk zones
- **📊 Sidebar** — risk distribution, weather chart, top sites list
- **🖱️ Click popup** — per-segment breakdown: shade + weather + PCI scores

---

## 💼 Business Value

| Customer | Problem | Value |
|----------|---------|-------|
| **City DPW** | Blind salting wastes budget | Target only HIGH/CRITICAL zones → **15-30% cost reduction** |
| **Risk/Insurance** | Liability from ice falls | Data-backed defense + proactive fixes |
| **Accessibility** | Elderly/disabled pedestrians | ADA-compliant route safety alerts |
| **Emergency Services** | Delayed response on icy roads | Pre-prioritized route clearance |

---

## 🔧 Tech Stack

| Component | Technology |
|-----------|-----------|
| Data ETL | Python (pandas, struct) |
| Map visualization | MapLibre GL JS |
| Charts | Chart.js |
| Weather | Open-Meteo Archive API (free) |
| Base map | CARTO Dark Matter |
| Point Cloud source | **Cyvl API** (`aboveGroundAssets`, `pavements`) |

---

## 🏆 Judging Criteria Alignment

| Criterion | Our Response |
|-----------|-------------|
| **Business Strength** | Real buyer: municipal DPW. Clear ROI: 15-30% salt budget savings. |
| **Technical Innovation** | Novel use of LiDAR tree canopy as shade proxy for ice persistence |
| **Use of Cyvl Data** | Tree locations from point cloud ARE the core algorithm input |
| **Presentation** | Live interactive map with real Somerville data from Nov 2025 |
