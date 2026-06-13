#!/usr/bin/env python3
"""
SafeRoute AI — Cyvl API Data Fetcher
=====================================
從 Cyvl REST API (https://i3.cyvl.app) 取得所有基礎設施資料。

使用方式:
  1. 先設定環境變數:
     export CYVL_TOKEN="你的Bearer Token"
     export CYVL_PROJECT_ID="你的Project UUID"

  2. 或者直接在這個檔案裡填入 TOKEN 和 PROJECT_ID

  3. 執行:
     python3 scripts/fetch_cyvl_api.py

API 文件: https://i3.cyvl.app/docs#
"""

import json
import os
import sys
import ssl
import urllib.request
import urllib.parse
import time

# ═══════════════════════════════════════════════════
# 🔑 在這裡填入你的認證資訊
# ═══════════════════════════════════════════════════
TOKEN = os.environ.get("CYVL_TOKEN", "")          # ← 填入你的 Bearer Token
PROJECT_ID = os.environ.get("CYVL_PROJECT_ID", "") # ← 填入你的 Project UUID

# ═══════════════════════════════════════════════════

BASE_URL = "https://i3.cyvl.app"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "api_raw")

# SSL context (macOS 需要)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def api_get(path: str, params: dict = None) -> dict:
    """呼叫 Cyvl API 並回傳 JSON"""
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{BASE_URL}{path}?{query}"
    else:
        url = f"{BASE_URL}{path}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  ❌ HTTP {e.code}: {body[:200]}")
        raise


def api_get_all_pages(path: str, params: dict, limit: int = 500) -> list:
    """
    自動處理 cursor-based 分頁，取得全部資料
    Cyvl API 每頁最多 500 筆
    """
    all_features = []
    params["limit"] = limit
    cursor = None
    page = 0

    while True:
        page += 1
        p = dict(params)
        if cursor:
            p["cursor"] = cursor

        print(f"    📄 Page {page} (已取得 {len(all_features)} 筆)...", end=" ", flush=True)
        data = api_get(path, p)

        features = data.get("features", [])
        all_features.extend(features)
        print(f"+{len(features)}")

        # 檢查是否有下一頁
        pagination = data.get("pagination", {})
        cursor = pagination.get("next_cursor")
        if not cursor or not features:
            break

        time.sleep(0.2)  # 避免 rate limit

    return all_features


def save_json(data, filename):
    """存成 JSON 檔案"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    size_kb = os.path.getsize(filepath) / 1024
    print(f"  💾 Saved: {filepath} ({size_kb:.1f} KB)")


def save_geojson(features, filename):
    """存成 GeoJSON FeatureCollection"""
    fc = {"type": "FeatureCollection", "features": features}
    save_json(fc, filename)


# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("🚀 SafeRoute AI — Cyvl API Data Fetcher")
    print("=" * 60)
    print()

    # ── 檢查認證 ──
    if not TOKEN:
        print("❌ 找不到 CYVL_TOKEN!")
        print()
        print("請用以下其中一種方式設定:")
        print()
        print("  方法 1: 環境變數")
        print('    export CYVL_TOKEN="eyJhbG..."')
        print('    export CYVL_PROJECT_ID="abc123..."')
        print()
        print("  方法 2: 直接編輯這個檔案")
        print(f"    {os.path.abspath(__file__)}")
        print('    TOKEN = "eyJhbG..."')
        print()
        print("💡 Token 來源:")
        print("   - Hackathon 主辦方提供 (Discord / Slack / email)")
        print("   - 或登入 https://cyvl.app 後從開發者設定取得")
        sys.exit(1)

    print(f"✅ Token: {TOKEN[:20]}...{TOKEN[-8:]}")
    print()

    # ── Step 0: 測試連線 ──
    print("🔗 測試 API 連線...")
    try:
        health = api_get("/health")
        print(f"  ✅ API 狀態: {health.get('status')} (v{health.get('version')})")
    except Exception as e:
        print(f"  ❌ 連線失敗: {e}")
        sys.exit(1)

    # ── Step 1: 取得 Project 列表 ──
    print()
    print("📋 Step 1: 取得 Project 列表...")
    try:
        projects = api_get("/api/v1/projects")
        proj_list = projects.get("projects", projects.get("data", []))

        if not proj_list:
            print("  ⚠️  沒有找到任何 project")
            print("  回傳資料:", json.dumps(projects, indent=2)[:500])
        else:
            print(f"  ✅ 找到 {len(proj_list)} 個 project(s):")
            for p in proj_list[:10]:
                pid = p.get("project_id", p.get("id", "?"))
                name = p.get("name", "")
                city = p.get("city", "")
                print(f"    📁 {pid}  — {name} ({city})")

        save_json(projects, "projects.json")
    except Exception as e:
        print(f"  ❌ 取得 project 列表失敗: {e}")

    if not PROJECT_ID:
        print()
        print("⚠️  找不到 CYVL_PROJECT_ID!")
        print("  請從上面的 project 列表中選一個 UUID，然後設定:")
        print('  export CYVL_PROJECT_ID="從上面複製的UUID"')
        print()
        print("  或者直接編輯這個檔案的 PROJECT_ID 變數")
        sys.exit(0)

    print(f"  🎯 使用 Project: {PROJECT_ID}")
    print()

    base_params = {"project_id": PROJECT_ID}

    # ── Step 2: Pavement Scores (路面品質) ──
    print("🛣️  Step 2: 取得 Pavement Scores...")
    try:
        bbox = "-71.15,42.37,-71.07,42.42"  # Somerville, MA 大範圍
        features = api_get_all_pages("/api/v1/pavement/scores", {**base_params, "bbox": bbox})
        print(f"  ✅ 取得 {len(features)} 個路面評分")
        save_geojson(features, "pavement_scores.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 3: Pavement Segments (路段) ──
    print()
    print("🛣️  Step 3: 取得 Pavement Segments...")
    try:
        features = api_get_all_pages("/api/v1/pavement/segments", {**base_params, "bbox": bbox})
        print(f"  ✅ 取得 {len(features)} 個路段")
        save_geojson(features, "pavement_segments.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 4: Above-Ground Assets (地面資產) ──
    print()
    print("🌳 Step 4: 取得 Above-Ground Assets...")
    try:
        features = api_get_all_pages("/api/v1/assets", {**base_params, "bbox": bbox})
        print(f"  ✅ 取得 {len(features)} 個地面資產")
        save_geojson(features, "above_ground_assets.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 5: Signs (標誌) ──
    print()
    print("🪧 Step 5: 取得 Signs...")
    try:
        features = api_get_all_pages("/api/v1/signs", {**base_params, "bbox": bbox})
        print(f"  ✅ 取得 {len(features)} 個標誌")
        save_geojson(features, "signs.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 6: Distresses (路面損壞) ──
    print()
    print("🔍 Step 6: 取得 Distresses...")
    try:
        features = api_get_all_pages("/api/v1/pavement/distresses",
                                     {**base_params, "bbox": bbox, "include_geometry": True})
        print(f"  ✅ 取得 {len(features)} 個路面損壞")
        save_geojson(features, "distresses.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 7: Markings (標線) ──
    print()
    print("🚧 Step 7: 取得 Markings...")
    try:
        features = api_get_all_pages("/api/v1/markings", {**base_params, "bbox": bbox})
        print(f"  ✅ 取得 {len(features)} 個標線")
        save_geojson(features, "markings.geojson")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

    # ── Step 8: Statistics (統計) ──
    print()
    print("📊 Step 8: 取得統計資料...")
    for endpoint, name in [
        ("/api/v1/pavement/pci-distribution", "pci_distribution"),
        ("/api/v1/pavement/distress-breakdown", "distress_breakdown"),
        ("/api/v1/assets/statistics", "asset_statistics"),
        ("/api/v1/assets/inventory", "asset_inventory"),
        ("/api/v1/signs/statistics", "sign_statistics"),
        ("/api/v1/markings/statistics", "marking_statistics"),
    ]:
        try:
            data = api_get(endpoint, base_params)
            save_json(data, f"{name}.json")
        except Exception as e:
            print(f"  ⚠️ {name}: {e}")

    # ── 完成 ──
    print()
    print("=" * 60)
    print("✅ 全部完成！")
    print(f"📁 所有資料已存到: {OUTPUT_DIR}")
    print()
    print("接下來你可以:")
    print("  1. 查看原始資料: ls -la data/api_raw/")
    print("  2. 執行分析: python3 scripts/process_data.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
