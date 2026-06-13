/**
 * SafeRoute AI — Climate Resilience Platform
 * Dual-mode (Heat/Cold) visualization using Cyvl Point Cloud
 * + Open-Meteo weather + intervention feasibility analysis
 */

// ─── Config ─────────────────────────────────────────────────────
const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const DATA_BASE = '../data/';
const SOMERVILLE = [-71.0995, 42.3876];

// ─── State ──────────────────────────────────────────────────────
let map;
let stressData, assetsData, weatherData, summaryData;
let siteHot, siteCold;
let weatherChart = null;
let currentMode = 'cold'; // 'cold' or 'heat'

// ─── Asset Type Icons ───────────────────────────────────────────
const ASSET_ICONS = {
  TREE: '🌳', HYDRANT: '🔴', UTILITY_POLE: '🔶', TRAFFIC_SIGNAL_POLE: '🚦',
  LUMINARIES: '💡', SIGN: '🪧', SIDEWALK: '🟦', CURB: '⬜', RAMP: '♿',
  CATCH_BASIN: '🕳️', MANHOLE_COVER: '⚫', TRAFFIC_SIGNAL: '🚦',
  STAND_ALONE_PEDESTRIAN_HEAD: '🚶', PEDESTRIAN_PUSH_BUTTON: '🔘',
  BIKE_RACK: '🚲', CCTV: '📷', FLASHING_BEACONS: '⚠️', GUARDRAILS: '🛡️',
};

// ─── Risk Colors ────────────────────────────────────────────────
const COLD_COLORS = ['#1a5276', '#2e86c1', '#5dade2', '#00e5ff'];
const HEAT_COLORS = ['#5d4037', '#e65100', '#ff6d00', '#ff1744'];

function riskColor(score) {
  const colors = currentMode === 'heat' ? HEAT_COLORS : COLD_COLORS;
  if (score >= 0.70) return colors[3];
  if (score >= 0.55) return colors[2];
  if (score >= 0.40) return colors[1];
  return colors[0];
}

function stressKey() {
  return currentMode === 'heat' ? 'heat_stress' : 'cold_stress';
}

// ─── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  bindModeToggle();
  bindDetailClose();
});

function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: MAP_STYLE,
    center: SOMERVILLE,
    zoom: 13.5,
    pitch: 15,
    bearing: -10,
    antialias: true,
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'imperial' }), 'bottom-left');

  map.on('load', onMapLoad);
}

// ─── Load All Data ──────────────────────────────────────────────
async function loadData() {
  const [stressRes, assetsRes, weatherRes, summaryRes, hotRes, coldRes] = await Promise.all([
    fetch(DATA_BASE + 'climate_stress.geojson'),
    fetch(DATA_BASE + 'assets_all.geojson'),
    fetch(DATA_BASE + 'weather.json'),
    fetch(DATA_BASE + 'summary.json'),
    fetch(DATA_BASE + 'sites/site_hot.json'),
    fetch(DATA_BASE + 'sites/site_cold.json'),
  ]);
  stressData  = await stressRes.json();
  assetsData  = await assetsRes.json();
  weatherData = await weatherRes.json();
  summaryData = await summaryRes.json();
  siteHot     = await hotRes.json();
  siteCold    = await coldRes.json();
}

// ─── On Map Load ────────────────────────────────────────────────
async function onMapLoad() {
  try {
    await loadData();
  } catch (err) {
    console.error('Data load error:', err);
    alert('Failed to load data. Run: python3 -m http.server 8000 from the project root.');
    return;
  }

  addMapSources();
  addMapLayers();
  populateSidebar();
  populateSiteCards();
  bindLayerToggles();
  bindMapEvents();
  updateMapStats();

  // Hide loading screen
  setTimeout(() => {
    const ls = document.getElementById('loading-screen');
    if (ls) {
      ls.classList.add('hidden');
      setTimeout(() => ls.remove(), 600);
    }
  }, 800);
}

// ─── Map Sources ────────────────────────────────────────────────
function addMapSources() {
  map.addSource('stress', { type: 'geojson', data: stressData });

  // Split assets by type for separate layers
  const trees = { type: 'FeatureCollection', features: assetsData.features.filter(f => f.properties.asset_type === 'TREE') };
  const obstacles = { type: 'FeatureCollection', features: assetsData.features.filter(f =>
    ['HYDRANT', 'UTILITY_POLE', 'TRAFFIC_SIGNAL_POLE', 'LUMINARIES', 'SIGN'].includes(f.properties.asset_type)
  )};
  const infra = { type: 'FeatureCollection', features: assetsData.features.filter(f =>
    ['SIDEWALK', 'CURB', 'RAMP', 'CATCH_BASIN', 'MANHOLE_COVER'].includes(f.properties.asset_type)
  )};

  // Critical segments
  const critical = {
    type: 'FeatureCollection',
    features: stressData.features.filter(f => f.properties.risk_label === 'CRITICAL'),
  };

  map.addSource('trees', { type: 'geojson', data: trees });
  map.addSource('obstacles', { type: 'geojson', data: obstacles });
  map.addSource('infra', { type: 'geojson', data: infra });
  map.addSource('critical', { type: 'geojson', data: critical });

  // Site markers
  const siteMarkers = {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', geometry: { type: 'Point', coordinates: siteHot.center }, properties: { id: 'site_hot', type: '☀️ Heat', address: siteHot.address } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: siteCold.center }, properties: { id: 'site_cold', type: '❄️ Cold', address: siteCold.address } },
    ],
  };
  map.addSource('site-markers', { type: 'geojson', data: siteMarkers });
}

// ─── Map Layers ─────────────────────────────────────────────────
function addMapLayers() {
  // ── Heatmap Layer ─────────────────────────────────────────
  map.addLayer({
    id: 'stress-heatmap',
    type: 'heatmap',
    source: 'stress',
    maxzoom: 17,
    paint: {
      'heatmap-weight': ['get', stressKey()],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 15, 1.5],
      'heatmap-color': currentMode === 'cold'
        ? ['interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(0,0,0,0)', 0.2, '#1a5276', 0.4, '#2e86c1',
            0.6, '#5dade2', 0.8, '#00e5ff', 1, '#ffffff']
        : ['interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(0,0,0,0)', 0.2, '#5d4037', 0.4, '#e65100',
            0.6, '#ff6d00', 0.8, '#ff1744', 1, '#ffffff'],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 15, 15, 30],
      'heatmap-opacity': 0.7,
    },
  });

  // ── Stress Circles ────────────────────────────────────────
  map.addLayer({
    id: 'stress-circles',
    type: 'circle',
    source: 'stress',
    minzoom: 13,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 3, 17, 8],
      'circle-color': ['interpolate', ['linear'], ['get', stressKey()],
        0.0, COLD_COLORS[0], 0.4, COLD_COLORS[1],
        0.55, COLD_COLORS[2], 0.7, COLD_COLORS[3]],
      'circle-opacity': 0.75,
      'circle-stroke-width': 0.5,
      'circle-stroke-color': 'rgba(255,255,255,0.15)',
    },
  });

  // ── Tree Layer ────────────────────────────────────────────
  map.addLayer({
    id: 'tree-circles',
    type: 'circle',
    source: 'trees',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2, 17, 5],
      'circle-color': '#69f0ae',
      'circle-opacity': 0.6,
      'circle-stroke-width': 0.5,
      'circle-stroke-color': 'rgba(105,240,174,0.3)',
    },
  });

  // ── Pavement/infra Layer (off by default) ─────────────────
  map.addLayer({
    id: 'infra-circles',
    type: 'circle',
    source: 'infra',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 3,
      'circle-color': ['match', ['get', 'asset_type'],
        'SIDEWALK', '#6c8ebf', 'CURB', '#aaaaaa', 'RAMP', '#b388ff',
        'CATCH_BASIN', '#795548', 'MANHOLE_COVER', '#616161', '#555555'],
      'circle-opacity': 0.5,
    },
  });

  // ── Critical Sites (pulsing) ──────────────────────────────
  map.addLayer({
    id: 'critical-glow',
    type: 'circle',
    source: 'critical',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 8, 17, 20],
      'circle-color': currentMode === 'cold' ? '#00e5ff' : '#ff1744',
      'circle-opacity': 0.15,
      'circle-stroke-width': 0,
    },
  });
  map.addLayer({
    id: 'critical-dots',
    type: 'circle',
    source: 'critical',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 3, 17, 6],
      'circle-color': currentMode === 'cold' ? '#00e5ff' : '#ff1744',
      'circle-opacity': 0.9,
      'circle-stroke-width': 1,
      'circle-stroke-color': '#ffffff',
    },
  });

  // ── Selected Site Markers ─────────────────────────────────
  map.addLayer({
    id: 'site-markers-glow',
    type: 'circle',
    source: 'site-markers',
    paint: {
      'circle-radius': 18,
      'circle-color': '#3d8bff',
      'circle-opacity': 0.2,
    },
  });
  map.addLayer({
    id: 'site-markers-dot',
    type: 'circle',
    source: 'site-markers',
    paint: {
      'circle-radius': 7,
      'circle-color': '#3d8bff',
      'circle-opacity': 1,
      'circle-stroke-width': 2,
      'circle-stroke-color': '#ffffff',
    },
  });

  // Animate critical glow
  let phase = 0;
  const animateGlow = () => {
    phase = (phase + 0.02) % 1;
    const r = 8 + Math.sin(phase * Math.PI * 2) * 6;
    const o = 0.12 + Math.sin(phase * Math.PI * 2) * 0.08;
    if (map.getLayer('critical-glow')) {
      map.setPaintProperty('critical-glow', 'circle-radius', r + (['interpolate', ['linear'], ['zoom'], 13, 0, 17, 12][0] || 0));
      map.setPaintProperty('critical-glow', 'circle-opacity', o);
    }
    requestAnimationFrame(animateGlow);
  };
  animateGlow();
}

// ─── Update Mode ────────────────────────────────────────────────
function updateMapForMode() {
  const key = stressKey();
  const isCold = currentMode === 'cold';
  const colors = isCold ? COLD_COLORS : HEAT_COLORS;

  // Heatmap
  map.setPaintProperty('stress-heatmap', 'heatmap-weight', ['get', key]);
  map.setPaintProperty('stress-heatmap', 'heatmap-color',
    isCold
      ? ['interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(0,0,0,0)', 0.2, '#1a5276', 0.4, '#2e86c1',
          0.6, '#5dade2', 0.8, '#00e5ff', 1, '#ffffff']
      : ['interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(0,0,0,0)', 0.2, '#5d4037', 0.4, '#e65100',
          0.6, '#ff6d00', 0.8, '#ff1744', 1, '#ffffff']
  );

  // Circles
  map.setPaintProperty('stress-circles', 'circle-color',
    ['interpolate', ['linear'], ['get', key],
      0.0, colors[0], 0.4, colors[1], 0.55, colors[2], 0.7, colors[3]]);

  // Critical glow & dots
  const critColor = isCold ? '#00e5ff' : '#ff1744';
  map.setPaintProperty('critical-glow', 'circle-color', critColor);
  map.setPaintProperty('critical-dots', 'circle-color', critColor);

  // Legend
  document.getElementById('legend-title').textContent = isCold ? '❄️ Black Ice Risk' : '☀️ Heat Stress';

  // Sidebar stats
  updateGauge();
  updateRiskDistribution();
  updateFactorBars();
  updateMapStats();
}

// ─── Mode Toggle ────────────────────────────────────────────────
function bindModeToggle() {
  document.getElementById('btn-cold').addEventListener('click', () => setMode('cold'));
  document.getElementById('btn-heat').addEventListener('click', () => setMode('heat'));
}

function setMode(mode) {
  if (mode === currentMode) return;
  currentMode = mode;

  // Toggle body class
  document.body.classList.toggle('heat-mode', mode === 'heat');

  // Update buttons
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });

  updateMapForMode();
}

// ─── Populate Sidebar ───────────────────────────────────────────
function populateSidebar() {
  // Topbar pills
  const pills = document.getElementById('topbar-pills');
  const dist = summaryData.combined_priority.distribution;
  pills.innerHTML = `
    <div class="topbar-pill">🗺️ <span class="pill-value">${summaryData.total_pavement_segments.toLocaleString()}</span> Segments</div>
    <div class="topbar-pill">🌳 <span class="pill-value">${summaryData.asset_breakdown.TREE.toLocaleString()}</span> Trees</div>
    <div class="topbar-pill">🚨 <span class="pill-value">${dist.CRITICAL}</span> Critical</div>
  `;

  // Weather badge
  const avgTemp = weatherData.winter_daily.reduce((s, d) => s + d.avg_temp_c, 0) / weatherData.winter_daily.length;
  document.getElementById('weather-text').textContent = `${avgTemp.toFixed(1)}°C avg · Nov 17–24`;

  // Gauge + distribution
  updateGauge();
  updateRiskDistribution();
  updateFactorBars();

  // Weather chart
  buildWeatherChart();
}

// ─── Gauge ──────────────────────────────────────────────────────
function updateGauge() {
  const key = currentMode === 'heat' ? 'heat_stress' : 'cold_stress';
  const stats = summaryData[key];
  const avg = stats.avg;

  document.getElementById('gauge-value').textContent = avg.toFixed(3);
  document.getElementById('gauge-label').textContent =
    currentMode === 'heat' ? 'Avg Heat Stress' : 'Avg Cold Stress';

  // Draw semi-circle gauge
  const canvas = document.getElementById('gauge-canvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const cx = W / 2, cy = H - 10, r = Math.min(cx, cy) - 10;
  const startAngle = Math.PI;
  const endAngle = 2 * Math.PI;

  // Background arc
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, endAngle);
  ctx.lineWidth = 12;
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineCap = 'round';
  ctx.stroke();

  // Value arc
  const pct = Math.min(1, avg);
  const valueAngle = startAngle + pct * Math.PI;
  const grad = ctx.createLinearGradient(cx - r, cy, cx + r, cy);
  if (currentMode === 'cold') {
    grad.addColorStop(0, '#1a5276');
    grad.addColorStop(0.5, '#2e86c1');
    grad.addColorStop(1, '#00e5ff');
  } else {
    grad.addColorStop(0, '#5d4037');
    grad.addColorStop(0.5, '#ff6d00');
    grad.addColorStop(1, '#ff1744');
  }
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, valueAngle);
  ctx.lineWidth = 12;
  ctx.strokeStyle = grad;
  ctx.lineCap = 'round';
  ctx.stroke();
}

// ─── Risk Distribution ─────────────────────────────────────────
function updateRiskDistribution() {
  const dist = summaryData.combined_priority.distribution;
  const el = document.getElementById('risk-distribution');
  el.innerHTML = `
    <div class="risk-chip critical">
      <span class="risk-chip-count">${dist.CRITICAL}</span>
      <span class="risk-chip-label">Critical</span>
    </div>
    <div class="risk-chip high">
      <span class="risk-chip-count">${dist.HIGH}</span>
      <span class="risk-chip-label">High</span>
    </div>
    <div class="risk-chip medium">
      <span class="risk-chip-count">${dist.MEDIUM}</span>
      <span class="risk-chip-label">Medium</span>
    </div>
    <div class="risk-chip low">
      <span class="risk-chip-count">${dist.LOW}</span>
      <span class="risk-chip-label">Low</span>
    </div>
  `;
}

// ─── Factor Bars ────────────────────────────────────────────────
function updateFactorBars() {
  // Use the first (top) segment's breakdown as representative
  const top = stressData.features[0]?.properties?.priority_breakdown;
  if (!top) return;

  const factors = [
    { label: '🌡️ Climate', value: top.climate_severity },
    { label: '🚶 Pedestrian', value: top.pedestrian_exposure },
    { label: '☀️ Shade Gap', value: top.shade_deficit },
    { label: '🛣️ Pavement', value: top.pavement_vulnerability },
    { label: '🔧 Feasibility', value: top.physical_feasibility },
  ];

  const el = document.getElementById('factor-bars');
  el.innerHTML = factors.map(f => `
    <div class="factor-bar-row">
      <span class="factor-bar-label">${f.label}</span>
      <div class="factor-bar-track">
        <div class="factor-bar-fill" style="width:${(f.value * 100).toFixed(0)}%"></div>
      </div>
      <span class="factor-bar-value">${(f.value * 100).toFixed(0)}%</span>
    </div>
  `).join('');
}

// ─── Weather Chart ──────────────────────────────────────────────
function buildWeatherChart() {
  const ctx = document.getElementById('weather-chart').getContext('2d');
  const daily = weatherData.winter_daily;
  const labels = daily.map(d => d.date.slice(5));
  const temps = daily.map(d => d.avg_temp_c);
  const risks = daily.map(d => d.weather_risk);

  if (weatherChart) weatherChart.destroy();

  weatherChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Temp (°C)',
          data: temps,
          backgroundColor: 'rgba(0,229,255,0.25)',
          borderColor: '#00e5ff',
          borderWidth: 1,
          borderRadius: 4,
          yAxisID: 'y',
        },
        {
          label: 'Risk',
          data: risks,
          type: 'line',
          borderColor: '#ff6d00',
          backgroundColor: 'rgba(255,109,0,0.1)',
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: '#ff6d00',
          fill: true,
          tension: 0.3,
          yAxisID: 'y1',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: { color: 'rgba(240,242,245,0.5)', font: { size: 10 }, boxWidth: 12, padding: 8 },
        },
      },
      scales: {
        x: {
          ticks: { color: 'rgba(240,242,245,0.4)', font: { size: 9 } },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
        y: {
          position: 'left',
          title: { display: true, text: '°C', color: 'rgba(240,242,245,0.4)', font: { size: 9 } },
          ticks: { color: 'rgba(240,242,245,0.4)', font: { size: 9 } },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
        y1: {
          position: 'right',
          min: 0, max: 1,
          title: { display: true, text: 'Risk', color: 'rgba(240,242,245,0.4)', font: { size: 9 } },
          ticks: { color: 'rgba(240,242,245,0.4)', font: { size: 9 } },
          grid: { display: false },
        },
      },
    },
  });
}

// ─── Site Cards ─────────────────────────────────────────────────
function populateSiteCards() {
  const el = document.getElementById('site-cards');
  el.innerHTML = [siteHot, siteCold].map(site => {
    const isHot = site.type === 'HOT_CORRIDOR';
    const icon = isHot ? '☀️' : '❄️';
    const badgeClass = site.priority_score >= 0.70 ? 'badge-critical' : 'badge-high';
    const stressVal = isHot ? site.climate_data.heat_stress : site.climate_data.cold_stress;
    return `
      <div class="site-card" data-site-id="${site.id}" onclick="openSiteDetail('${site.id}')">
        <div class="site-card-header">
          <span class="site-card-name">${icon} ${site.address}</span>
          <span class="site-card-badge ${badgeClass}">${site.type.replace('_', ' ')}</span>
        </div>
        <div class="site-card-stats">
          <div class="site-card-stat">
            <span class="site-card-stat-value">${stressVal.toFixed(3)}</span>
            <span class="site-card-stat-label">${isHot ? 'Heat' : 'Cold'} Stress</span>
          </div>
          <div class="site-card-stat">
            <span class="site-card-stat-value">${site.priority_score.toFixed(3)}</span>
            <span class="site-card-stat-label">Priority</span>
          </div>
          <div class="site-card-stat">
            <span class="site-card-stat-value">${site.intervention.all_checks_pass ? '✅' : '❌'}</span>
            <span class="site-card-stat-label">Feasible</span>
          </div>
        </div>
        <div class="site-card-action">
          View Intervention Plan →
        </div>
      </div>
    `;
  }).join('');
}

// ─── Map Stats ──────────────────────────────────────────────────
function updateMapStats() {
  const dist = summaryData.combined_priority.distribution;
  document.getElementById('stat-segments').textContent = summaryData.total_pavement_segments.toLocaleString();
  document.getElementById('stat-trees').textContent = summaryData.asset_breakdown.TREE.toLocaleString();
  document.getElementById('stat-critical').textContent = dist.CRITICAL;
}

// ─── Layer Toggles ──────────────────────────────────────────────
function bindLayerToggles() {
  document.querySelectorAll('#layer-toggles input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const layer = cb.dataset.layer;
      const vis = cb.checked ? 'visible' : 'none';
      const layerMap = {
        heatmap: ['stress-heatmap'],
        circles: ['stress-circles'],
        trees: ['tree-circles'],
        pavements: ['infra-circles'],
        critical: ['critical-glow', 'critical-dots'],
      };
      (layerMap[layer] || []).forEach(lid => {
        if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', vis);
      });
    });
  });
}

// ─── Map Click Events ───────────────────────────────────────────
function bindMapEvents() {
  // Click on stress circles → popup
  map.on('click', 'stress-circles', (e) => {
    const f = e.features[0];
    const p = f.properties;
    const coords = f.geometry.coordinates.slice();

    // Parse priority_breakdown if string
    let pb = p.priority_breakdown;
    if (typeof pb === 'string') pb = JSON.parse(pb);

    const key = stressKey();
    const score = p[key] || p.combined_priority;
    const label = p.risk_label;

    const badgeStyle = label === 'CRITICAL'
      ? 'background:rgba(255,23,68,0.15);color:#ff1744;border:1px solid rgba(255,23,68,0.3)'
      : label === 'HIGH'
        ? 'background:rgba(255,109,0,0.15);color:#ff6d00;border:1px solid rgba(255,109,0,0.3)'
        : label === 'MEDIUM'
          ? 'background:rgba(255,234,0,0.15);color:#ffea00;border:1px solid rgba(255,234,0,0.3)'
          : 'background:rgba(105,240,174,0.15);color:#69f0ae;border:1px solid rgba(105,240,174,0.3)';

    const barColor = currentMode === 'cold'
      ? 'background:linear-gradient(90deg,#2e86c1,#00e5ff)'
      : 'background:linear-gradient(90deg,#e65100,#ff1744)';

    const html = `
      <div class="popup-inner">
        <div class="popup-header">
          <span class="popup-address">${p.address || 'Unknown'}</span>
          <span class="popup-badge" style="${badgeStyle};padding:3px 8px;border-radius:10px;font-size:0.6rem;font-weight:700">${label}</span>
        </div>
        <div class="popup-score">
          <div class="popup-score-value">${parseFloat(score).toFixed(3)}</div>
          <div class="popup-score-label">${currentMode === 'heat' ? 'Heat' : 'Cold'} Stress Score</div>
        </div>
        <div class="popup-bars">
          ${pb ? Object.entries(pb).map(([k, v]) => `
            <div class="popup-bar-row">
              <span class="popup-bar-label">${k.replace(/_/g, ' ')}</span>
              <div class="popup-bar-track">
                <div class="popup-bar-fill" style="width:${(v * 100).toFixed(0)}%;${barColor}"></div>
              </div>
              <span class="popup-bar-value">${(v * 100).toFixed(0)}%</span>
            </div>
          `).join('') : ''}
        </div>
        <div class="popup-meta">
          <div class="popup-meta-item">
            <span class="popup-meta-value">${p.nearby_trees}</span>
            <span class="popup-meta-label">Trees</span>
          </div>
          <div class="popup-meta-item">
            <span class="popup-meta-value">${parseFloat(p.pci_score).toFixed(0)}</span>
            <span class="popup-meta-label">PCI</span>
          </div>
          <div class="popup-meta-item">
            <span class="popup-meta-value">${p.nearby_obstacles}</span>
            <span class="popup-meta-label">Obstacles</span>
          </div>
        </div>
      </div>
    `;

    new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
      .setLngLat(coords)
      .setHTML(html)
      .addTo(map);
  });

  // Cursor
  map.on('mouseenter', 'stress-circles', () => map.getCanvas().style.cursor = 'pointer');
  map.on('mouseleave', 'stress-circles', () => map.getCanvas().style.cursor = '');
}

// ─── Site Detail Panel ──────────────────────────────────────────
function bindDetailClose() {
  document.getElementById('detail-close').addEventListener('click', () => {
    document.getElementById('site-detail-panel').classList.remove('open');
  });
}

function openSiteDetail(siteId) {
  const site = siteId === 'site_hot' ? siteHot : siteCold;
  const panel = document.getElementById('site-detail-panel');
  const content = document.getElementById('detail-content');
  const isHot = site.type === 'HOT_CORRIDOR';

  // Fly to site
  map.flyTo({ center: site.center, zoom: 16, pitch: 30, duration: 1500 });

  const interv = site.intervention;
  const feas = interv.feasibility;
  const impact = site.impact_estimate;
  const geo = site.geometry_measured;

  content.innerHTML = `
    <!-- Header -->
    <div class="detail-header">
      <div class="detail-site-type">${isHot ? '☀️ Hot Corridor' : '❄️ Cold Corridor'}</div>
      <div class="detail-site-name">${site.address}</div>
      <div class="detail-score-row">
        <span class="detail-score-big">${site.priority_score.toFixed(3)}</span>
        <span class="detail-score-desc">Combined priority score from 5 factors across ${summaryData.total_pavement_segments} segments</span>
      </div>
    </div>

    <!-- Priority Breakdown -->
    <div class="detail-section">
      <div class="detail-section-title">📊 Why This Site Was Prioritized</div>
      <div class="radar-container">
        <canvas id="radar-chart" width="260" height="200"></canvas>
      </div>
    </div>

    <!-- Geometry Measured -->
    <div class="detail-section">
      <div class="detail-section-title">📐 Geometry Measured</div>
      <div class="metric-cards">
        <div class="metric-card">
          <span class="metric-card-value">${geo.pci_score?.toFixed(1) ?? '—'}</span>
          <span class="metric-card-label">PCI Score</span>
        </div>
        <div class="metric-card">
          <span class="metric-card-value">${geo.sidewalk_condition || '—'}</span>
          <span class="metric-card-label">Sidewalk</span>
        </div>
        <div class="metric-card">
          <span class="metric-card-value">${geo.curb_length_ft?.toFixed(0) ?? '—'} ft</span>
          <span class="metric-card-label">Curb Length</span>
        </div>
        <div class="metric-card">
          <span class="metric-card-value">${geo.road_area_sqft?.toFixed(0) ?? '—'} ft²</span>
          <span class="metric-card-label">Road Area</span>
        </div>
      </div>
    </div>

    <!-- Nearby Assets -->
    <div class="detail-section">
      <div class="detail-section-title">🏗️ Nearby Assets (${site.nearby_assets.length})</div>
      <div class="asset-list">
        ${site.nearby_assets.slice(0, 12).map(a => `
          <div class="asset-item">
            <span class="asset-icon">${ASSET_ICONS[a.type] || '📌'}</span>
            <span class="asset-name">${a.type.replace(/_/g, ' ')}</span>
            <span class="asset-distance">${a.distance_m.toFixed(0)}m</span>
          </div>
        `).join('')}
        ${site.nearby_assets.length > 12 ? `<div style="text-align:center;font-size:0.72rem;color:var(--text-tertiary);padding:6px 0">+ ${site.nearby_assets.length - 12} more assets</div>` : ''}
      </div>
    </div>

    <!-- Intervention -->
    <div class="detail-section">
      <div class="detail-section-title">🛠️ Proposed Intervention</div>
      <div class="intervention-card">
        <div class="intervention-name">${interv.name}</div>
        <div class="intervention-desc">${interv.description}</div>
        <div class="intervention-dims">
          <span class="intervention-dim">W: <strong>${interv.dimensions_ft.width}ft</strong></span>
          <span class="intervention-dim">D: <strong>${interv.dimensions_ft.depth}ft</strong></span>
          <span class="intervention-dim">H: <strong>${interv.dimensions_ft.height}ft</strong></span>
        </div>
      </div>
    </div>

    <!-- Feasibility -->
    <div class="detail-section">
      <div class="detail-section-title">${interv.all_checks_pass ? '✅' : '❌'} Feasibility Checks</div>
      <table class="feasibility-table">
        <thead><tr><th>Check</th><th>Req.</th><th>Actual</th><th>Pass</th></tr></thead>
        <tbody>
          ${Object.entries(feas).map(([k, v]) => `
            <tr>
              <td>${k.replace(/_/g, ' ')}</td>
              <td>${v.required_ft != null ? v.required_ft + ' ft' : '—'}</td>
              <td>${v.nearest_ft != null ? v.nearest_ft + ' ft' : v.available_ft != null ? v.available_ft + ' ft' : v.note || '—'}</td>
              <td>${v.pass ? '✅' : '❌'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>

    <!-- Site Plan SVG -->
    <div class="detail-section">
      <div class="detail-section-title">📋 Site Plan</div>
      <div style="background:var(--bg-base);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px;text-align:center">
        <img src="../data/sites/${site.id}_plan.svg" alt="Site Plan"
             style="max-width:100%;height:auto;border-radius:4px"
             onerror="this.parentElement.innerHTML='<span style=color:var(--text-tertiary)>Plan not available</span>'">
      </div>
    </div>

    <!-- Impact Estimate -->
    <div class="detail-section">
      <div class="detail-section-title">📈 Expected Impact</div>
      <div class="impact-cards">
        <div class="impact-card">
          <span class="impact-card-icon">🌿</span>
          <span class="impact-card-value">+${impact.shade_increase_pct}%</span>
          <span class="impact-card-label">Shade</span>
        </div>
        <div class="impact-card">
          <span class="impact-card-icon">🌡️</span>
          <span class="impact-card-value">-${impact.surface_temp_reduction_f}°F</span>
          <span class="impact-card-label">Temp</span>
        </div>
        <div class="impact-card">
          <span class="impact-card-icon">🧊</span>
          <span class="impact-card-value">${impact.ice_risk_change > 0 ? '+' : ''}${impact.ice_risk_change}</span>
          <span class="impact-card-label">Ice Risk</span>
        </div>
      </div>
      <div style="margin-top:10px;font-size:0.75rem;color:var(--text-secondary);line-height:1.5">
        <p>🛡️ ${impact.safety_improvement}</p>
        <p style="margin-top:4px">⚡ ${impact.energy_savings}</p>
      </div>
    </div>

    <!-- Report Button -->
    <button class="btn-report" onclick="generateReport('${site.id}')">
      📄 Download Full Report
    </button>
  `;

  // Open panel
  panel.classList.add('open');

  // Draw radar chart
  setTimeout(() => drawRadarChart(site), 100);
}

// ─── Radar Chart ────────────────────────────────────────────────
function drawRadarChart(site) {
  const canvas = document.getElementById('radar-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const pb = site.priority_breakdown;
  const labels = Object.keys(pb).map(k => k.replace(/_/g, ' '));
  const values = Object.values(pb);

  new Chart(ctx, {
    type: 'radar',
    data: {
      labels,
      datasets: [{
        label: 'Priority',
        data: values,
        backgroundColor: site.type === 'HOT_CORRIDOR'
          ? 'rgba(255,109,0,0.2)' : 'rgba(0,229,255,0.2)',
        borderColor: site.type === 'HOT_CORRIDOR'
          ? '#ff6d00' : '#00e5ff',
        borderWidth: 2,
        pointBackgroundColor: site.type === 'HOT_CORRIDOR'
          ? '#ff6d00' : '#00e5ff',
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          beginAtZero: true,
          max: 1,
          ticks: { display: false, stepSize: 0.2 },
          grid: { color: 'rgba(255,255,255,0.06)' },
          angleLines: { color: 'rgba(255,255,255,0.06)' },
          pointLabels: {
            color: 'rgba(240,242,245,0.6)',
            font: { size: 9 },
          },
        },
      },
    },
  });
}

// ─── Report Generator ───────────────────────────────────────────
function generateReport(siteId) {
  const site = siteId === 'site_hot' ? siteHot : siteCold;
  const isHot = site.type === 'HOT_CORRIDOR';
  const interv = site.intervention;
  const feas = interv.feasibility;
  const impact = site.impact_estimate;
  const geo = site.geometry_measured;
  const pb = site.priority_breakdown;

  const html = `<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>SafeRoute AI — ${site.address} Report</title>
<style>
  body{font-family:'Inter',sans-serif;max-width:800px;margin:0 auto;padding:40px;color:#1a1a2e;line-height:1.6}
  h1{color:#3d8bff;border-bottom:2px solid #3d8bff;padding-bottom:8px}
  h2{color:#333;margin-top:24px}
  .badge{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700}
  .badge-hot{background:#fff3e0;color:#e65100}
  .badge-cold{background:#e3f2fd;color:#1565c0}
  table{width:100%;border-collapse:collapse;margin:12px 0}
  th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #e0e0e0}
  th{background:#f5f5f5;font-weight:600;font-size:13px}
  .pass{color:#2e7d32}.fail{color:#c62828}
  .metric{text-align:center;padding:16px;background:#f8f9fa;border-radius:8px}
  .metric-val{font-size:28px;font-weight:800;color:#3d8bff}
  .metric-lab{font-size:11px;color:#888;text-transform:uppercase}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}
  .footer{margin-top:40px;padding-top:16px;border-top:1px solid #e0e0e0;font-size:12px;color:#999}
</style></head><body>
  <h1>SafeRoute AI — Site Intervention Report</h1>
  <p><strong>Site:</strong> ${site.address} &nbsp;
  <span class="badge ${isHot ? 'badge-hot' : 'badge-cold'}">${isHot ? '☀️ Hot Corridor' : '❄️ Cold Corridor'}</span></p>
  <p><strong>Coordinates:</strong> ${site.center[1].toFixed(5)}°N, ${Math.abs(site.center[0]).toFixed(5)}°W</p>
  <p><strong>Priority Score:</strong> ${site.priority_score.toFixed(3)} / 1.000</p>

  <h2>1. Why This Site Was Prioritized</h2>
  <table>
    <tr><th>Factor</th><th>Score</th><th>Weight</th></tr>
    ${Object.entries(pb).map(([k, v]) => `<tr><td>${k.replace(/_/g, ' ')}</td><td>${(v * 100).toFixed(1)}%</td><td>—</td></tr>`).join('')}
  </table>

  <h2>2. Geometry Measured (Cyvl Point Cloud)</h2>
  <div class="grid">
    <div class="metric"><div class="metric-val">${geo.pci_score?.toFixed(1) ?? '—'}</div><div class="metric-lab">PCI Score</div></div>
    <div class="metric"><div class="metric-val">${geo.sidewalk_condition || '—'}</div><div class="metric-lab">Sidewalk</div></div>
    <div class="metric"><div class="metric-val">${geo.curb_length_ft ?? '—'}</div><div class="metric-lab">Curb (ft)</div></div>
    <div class="metric"><div class="metric-val">${geo.road_area_sqft ?? '—'}</div><div class="metric-lab">Area (ft²)</div></div>
  </div>

  <h2>3. Proposed Intervention</h2>
  <p><strong>${interv.name}</strong></p>
  <p>${interv.description}</p>
  <p>Footprint: ${interv.dimensions_ft.width}×${interv.dimensions_ft.depth}ft, Height: ${interv.dimensions_ft.height}ft</p>

  <h2>4. Feasibility Checks</h2>
  <table>
    <tr><th>Check</th><th>Required</th><th>Actual</th><th>Result</th></tr>
    ${Object.entries(feas).map(([k, v]) => `
      <tr>
        <td>${k.replace(/_/g, ' ')}</td>
        <td>${v.required_ft != null ? v.required_ft + ' ft' : '—'}</td>
        <td>${v.nearest_ft != null ? v.nearest_ft + ' ft' : v.available_ft != null ? v.available_ft + ' ft' : v.note || '—'}</td>
        <td class="${v.pass ? 'pass' : 'fail'}">${v.pass ? '✅ PASS' : '❌ FAIL'}</td>
      </tr>
    `).join('')}
  </table>
  <p><strong>Overall: ${interv.all_checks_pass ? '✅ ALL CHECKS PASS' : '❌ SOME CHECKS FAILED'}</strong></p>

  <h2>5. Expected Impact</h2>
  <div class="grid">
    <div class="metric"><div class="metric-val">+${impact.shade_increase_pct}%</div><div class="metric-lab">Shade</div></div>
    <div class="metric"><div class="metric-val">-${impact.surface_temp_reduction_f}°F</div><div class="metric-lab">Surface Temp</div></div>
    <div class="metric"><div class="metric-val">${impact.ice_risk_change}</div><div class="metric-lab">Ice Risk Δ</div></div>
    <div class="metric"><div class="metric-val">🛡️</div><div class="metric-lab">Safety</div></div>
  </div>
  <p>${impact.pedestrian_comfort}</p>
  <p>${impact.energy_savings}</p>
  <p>${impact.safety_improvement}</p>

  <div class="footer">
    <p>Generated by SafeRoute AI | Cyvl Physical AI Hackathon | ${new Date().toISOString().slice(0, 10)}</p>
    <p>Data: Cyvl Point Cloud (Somerville, MA) • Open-Meteo Historical Weather • ${summaryData.total_assets.toLocaleString()} assets analyzed</p>
  </div>
</body></html>`;

  const win = window.open('', '_blank');
  win.document.write(html);
  win.document.close();
}
