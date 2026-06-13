/**
 * SafeRoute AI — Map Application
 * Black Ice Risk Visualization using Cyvl Point Cloud + Open-Meteo Weather
 */

// ─── Config ──────────────────────────────────────────────────────────────────
// Using MapLibre GL JS with free OpenStreetMap dark style (no token needed)
const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

const DATA_BASE = '../data/';

// ─── State ───────────────────────────────────────────────────────────────────
let map, riskData, treeData, summaryData, weatherData;
let riskChart = null;

// ─── Risk Colors ─────────────────────────────────────────────────────────────
const riskColor = (score) => {
  if (score >= 0.75) return '#ff1744';
  if (score >= 0.55) return '#ff6d00';
  if (score >= 0.35) return '#ffea00';
  return '#69f0ae';
};

// ─── Map Init ────────────────────────────────────────────────────────────────
function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: MAP_STYLE,
    center: [-71.1005, 42.3876],  // Somerville, MA
    zoom: 13.5,
    pitch: 20,
    bearing: -10,
    antialias: true,
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'imperial' }), 'bottom-left');

  map.on('load', onMapLoad);
}

// ─── Load All Data ───────────────────────────────────────────────────────────
async function loadData() {
  const [riskRes, treeRes, summaryRes, weatherRes] = await Promise.all([
    fetch(DATA_BASE + 'risk_map.geojson'),
    fetch(DATA_BASE + 'trees.geojson'),
    fetch(DATA_BASE + 'summary.json'),
    fetch(DATA_BASE + 'weather.json'),
  ]);
  riskData    = await riskRes.json();
  treeData    = await treeRes.json();
  summaryData = await summaryRes.json();
  weatherData = await weatherRes.json();
}

// ─── On Map Load ─────────────────────────────────────────────────────────────
async function onMapLoad() {
  try {
    await loadData();
  } catch (err) {
    console.error('Data load error:', err);
    alert('Failed to load data. Make sure you are running a local server (python3 -m http.server 8000) from the project root.');
    return;
  }

  addMapLayers();
  populateSidebar();
  bindLayerToggles();
  bindMapEvents();

  // Hide loading overlay
  const loading = document.getElementById('map-loading');
  loading.classList.add('hidden');
  setTimeout(() => loading.remove(), 600);
}

// ─── Add Map Layers ───────────────────────────────────────────────────────────
function addMapLayers() {
  // ── Source: Risk segments
  map.addSource('risk', { type: 'geojson', data: riskData });

  // ── Source: Trees
  map.addSource('trees', { type: 'geojson', data: treeData });

  // ── Layer 1: Risk heatmap (background glow)
  map.addLayer({
    id: 'risk-heatmap',
    type: 'heatmap',
    source: 'risk',
    maxzoom: 16,
    paint: {
      'heatmap-weight': ['interpolate', ['linear'], ['get', 'black_ice_risk'], 0, 0, 1, 1],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.8, 15, 2.5],
      'heatmap-radius':   ['interpolate', ['linear'], ['zoom'], 10, 20, 15, 50],
      'heatmap-opacity': 0.65,
      'heatmap-color': [
        'interpolate', ['linear'],
        ['heatmap-density'],
        0,   'rgba(0,229,255,0)',
        0.2, 'rgba(105,240,174,0.6)',
        0.4, 'rgba(255,234,0,0.7)',
        0.6, 'rgba(255,109,0,0.8)',
        0.8, 'rgba(255,23,68,0.9)',
        1,   'rgba(255,23,68,1)',
      ],
    },
  });

  // ── Layer 2: Risk circles (visible at zoom ≥ 13)
  map.addLayer({
    id: 'risk-circles',
    type: 'circle',
    source: 'risk',
    minzoom: 13,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 4, 16, 12],
      'circle-color': [
        'interpolate', ['linear'], ['get', 'black_ice_risk'],
        0, '#69f0ae', 0.35, '#ffea00', 0.55, '#ff6d00', 0.75, '#ff1744',
      ],
      'circle-opacity': 0.85,
      'circle-stroke-width': 1,
      'circle-stroke-color': 'rgba(255,255,255,0.2)',
      'circle-blur': 0.15,
    },
  });

  // ── Layer 3: Tree circles (Cyvl point cloud)
  map.addLayer({
    id: 'trees-layer',
    type: 'circle',
    source: 'trees',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 6],
      'circle-color': '#00e5ff',
      'circle-opacity': 0.6,
      'circle-stroke-width': 1,
      'circle-stroke-color': 'rgba(0,229,255,0.3)',
      'circle-blur': 0.3,
    },
  });

  // ── Layer 4: Critical zone pulsing rings
  const criticalData = {
    type: 'FeatureCollection',
    features: riskData.features.filter(f => f.properties.risk_label === 'CRITICAL'),
  };
  map.addSource('critical', { type: 'geojson', data: criticalData });

  map.addLayer({
    id: 'critical-outer',
    type: 'circle',
    source: 'critical',
    paint: {
      'circle-radius': 30,
      'circle-color': 'transparent',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#ff1744',
      'circle-opacity': 0.6,
    },
  });

  map.addLayer({
    id: 'critical-label',
    type: 'symbol',
    source: 'critical',
    layout: {
      'text-field': ['get', 'address'],
      'text-size': 10,
      'text-anchor': 'top',
      'text-offset': [0, 2.5],
      'text-font': ['DIN Pro Medium', 'Arial Unicode MS Regular'],
    },
    paint: {
      'text-color': '#ff1744',
      'text-halo-color': 'rgba(0,0,0,0.8)',
      'text-halo-width': 2,
    },
  });

  // Pulse animation for critical rings
  let size = 30;
  let growing = true;
  function animateCritical() {
    if (growing) { size += 0.3; if (size >= 36) growing = false; }
    else         { size -= 0.3; if (size <= 28) growing = true; }
    if (map.getLayer('critical-outer')) {
      map.setPaintProperty('critical-outer', 'circle-radius', size);
      map.setPaintProperty('critical-outer', 'circle-stroke-color',
        `rgba(255, 23, 68, ${0.3 + (size - 28) / 8 * 0.5})`
      );
    }
    requestAnimationFrame(animateCritical);
  }
  animateCritical();
}

// ─── Bind Map Click Events ────────────────────────────────────────────────────
function bindMapEvents() {
  // Click on risk circles
  map.on('click', 'risk-circles', (e) => {
    const props = e.features[0].properties;
    const coords = e.features[0].geometry.coordinates;
    showRiskPopup(coords, props);
  });

  map.on('mouseenter', 'risk-circles', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'risk-circles', () => { map.getCanvas().style.cursor = ''; });

  // Click on critical outer ring
  map.on('click', 'critical-outer', (e) => {
    const props = e.features[0].properties;
    const coords = e.features[0].geometry.coordinates;
    showRiskPopup(coords, props);
  });
  map.on('mouseenter', 'critical-outer', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'critical-outer', () => { map.getCanvas().style.cursor = ''; });
}

// ─── Show Risk Popup ──────────────────────────────────────────────────────────
function showRiskPopup(coords, props) {
  const tpl = document.getElementById('popup-tpl');
  const node = tpl.content.cloneNode(true);

  const risk   = +props.black_ice_risk;
  const shade  = +props.shade_score;
  const weather= +props.weather_risk;
  const pave   = +props.pavement_risk;
  const addr   = props.address || 'Unknown Street';
  const label  = props.risk_label || 'MEDIUM';

  node.querySelector('.popup-risk-badge').setAttribute('data-label', label);
  node.querySelector('.popup-risk-badge').textContent = label;
  node.querySelector('.popup-address').textContent = addr;
  node.querySelector('.popup-score-val').textContent = risk.toFixed(2);

  node.querySelector('.factor-bar[data-shade]').style.width = (shade * 100) + '%';
  node.querySelector('.factor-pct[data-shade-pct]').textContent = Math.round(shade * 100) + '%';

  node.querySelector('.factor-bar[data-weather]').style.width = (weather * 100) + '%';
  node.querySelector('.factor-pct[data-weather-pct]').textContent = Math.round(weather * 100) + '%';

  node.querySelector('.factor-bar[data-pavement]').style.width = (pave * 100) + '%';
  node.querySelector('.factor-pct[data-pave-pct]').textContent = Math.round(pave * 100) + '%';

  node.querySelector('[data-trees]').textContent = props.nearby_trees || 0;
  node.querySelector('[data-pci]').textContent = Math.round(+props.pci_score || 0);

  const div = document.createElement('div');
  div.appendChild(node);

  new maplibregl.Popup({ closeOnClick: true, maxWidth: '300px' })
    .setLngLat(coords)
    .setDOMContent(div)
    .addTo(map);
}

// ─── Populate Sidebar ─────────────────────────────────────────────────────────
function populateSidebar() {
  const s = summaryData;
  const w = weatherData;

  // Pills
  document.getElementById('pill-trees').textContent     = `${s.total_trees.toLocaleString()} Trees Mapped`;
  document.getElementById('pill-segments').textContent  = `${s.total_segments.toLocaleString()} Segments`;
  document.getElementById('pill-critical').textContent  = `${s.risk_distribution.CRITICAL} Critical Zones`;

  // Weather widget
  const days = w.daily;
  const avgTemp = days.reduce((a, d) => a + d.avg_temp_c, 0) / days.length;
  document.getElementById('weather-avg-temp').textContent = avgTemp.toFixed(1) + '°C avg';

  // Gauge
  drawGauge(s.avg_risk_score);
  document.getElementById('gauge-avg-risk').textContent = s.avg_risk_score.toFixed(2);

  // Risk bars
  const total = s.total_segments;
  ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].forEach(label => {
    const cnt = s.risk_distribution[label];
    document.getElementById(`cnt-${label}`).textContent = cnt.toLocaleString();
    setTimeout(() => {
      document.getElementById(`bar-${label}`).style.width = (cnt / total * 100) + '%';
    }, 300);
  });

  // Weather Chart
  drawWeatherChart(w.daily);

  // Site list
  const siteList = document.getElementById('site-list');
  siteList.innerHTML = '';

  // Show top HIGH+CRITICAL sites
  const topSites = riskData.features
    .filter(f => ['CRITICAL', 'HIGH'].includes(f.properties.risk_label))
    .slice(0, 8);

  topSites.forEach(f => {
    const p = f.properties;
    const card = document.createElement('div');
    card.className = 'site-card';
    card.innerHTML = `
      <div class="site-card-left">
        <div class="site-card-street">${p.address || 'Unknown Street'}</div>
        <div class="site-card-meta">🌳 ${p.nearby_trees} trees · PCI ${Math.round(p.pci_score)}</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
        <span class="site-risk-badge" style="color:${riskColor(p.black_ice_risk)}">${p.black_ice_risk.toFixed(2)}</span>
        <span class="site-risk-label ${p.risk_label}">${p.risk_label}</span>
      </div>
    `;
    card.addEventListener('click', () => {
      map.flyTo({ center: f.geometry.coordinates, zoom: 16, pitch: 30, duration: 1000 });
      showRiskPopup(f.geometry.coordinates, p);
    });
    siteList.appendChild(card);
  });
}

// ─── Draw Gauge ───────────────────────────────────────────────────────────────
function drawGauge(value) {
  const canvas = document.getElementById('riskGauge');
  const ctx = canvas.getContext('2d');
  const cx = canvas.width / 2;
  const cy = canvas.height - 10;
  const r = 80;
  const startAngle = Math.PI;
  const endAngle   = 2 * Math.PI;
  const valueAngle = startAngle + (endAngle - startAngle) * value;

  // Background arc
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, endAngle);
  ctx.lineWidth = 14;
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.stroke();

  // Gradient arc
  const grad = ctx.createLinearGradient(cx - r, cy, cx + r, cy);
  grad.addColorStop(0,    '#69f0ae');
  grad.addColorStop(0.4,  '#ffea00');
  grad.addColorStop(0.7,  '#ff6d00');
  grad.addColorStop(1,    '#ff1744');

  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, valueAngle);
  ctx.lineWidth = 14;
  ctx.strokeStyle = grad;
  ctx.lineCap = 'round';
  ctx.stroke();

  // Tick marks
  for (let i = 0; i <= 4; i++) {
    const angle = startAngle + (endAngle - startAngle) * (i / 4);
    const x1 = cx + (r - 8) * Math.cos(angle);
    const y1 = cy + (r - 8) * Math.sin(angle);
    const x2 = cx + (r + 2) * Math.cos(angle);
    const y2 = cy + (r + 2) * Math.sin(angle);
    ctx.beginPath();
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.stroke();
  }
}

// ─── Weather Chart ────────────────────────────────────────────────────────────
function drawWeatherChart(daily) {
  const labels = daily.map(d => d.date.slice(5));  // "11-17"
  const temps  = daily.map(d => d.avg_temp_c);
  const risks  = daily.map(d => d.weather_risk * 10); // scale to temp axis

  const ctx = document.getElementById('weatherChart').getContext('2d');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Avg Temp (°C)',
          data: temps,
          backgroundColor: daily.map(d =>
            d.avg_temp_c <= 0 ? 'rgba(61,139,255,0.8)' :
            d.avg_temp_c <= 2 ? 'rgba(0,229,255,0.7)' :
            'rgba(105,240,174,0.6)'
          ),
          borderRadius: 4,
          yAxisID: 'y',
        },
        {
          label: 'Weather Risk (×10)',
          data: risks,
          type: 'line',
          borderColor: '#ff6d00',
          backgroundColor: 'rgba(255,109,0,0.1)',
          pointBackgroundColor: daily.map(d => d.weather_risk >= 0.8 ? '#ff1744' : '#ff6d00'),
          pointRadius: 4,
          tension: 0.4,
          fill: true,
          yAxisID: 'y',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              if (ctx.datasetIndex === 0) return `Temp: ${ctx.parsed.y}°C`;
              return `Risk: ${(ctx.parsed.y / 10).toFixed(2)}`;
            },
          },
          backgroundColor: 'rgba(10,12,18,0.95)',
          titleColor: '#e8eaf6',
          bodyColor: '#9095b0',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#555d80', font: { size: 9 } },
        },
        y: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#555d80', font: { size: 9 } },
        },
      },
    },
  });
}

// ─── Layer Toggle Bindings ────────────────────────────────────────────────────
function bindLayerToggles() {
  const toggle = (id, layers) => {
    document.getElementById(id).addEventListener('change', (e) => {
      const vis = e.target.checked ? 'visible' : 'none';
      layers.forEach(l => {
        if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', vis);
      });
    });
  };

  toggle('toggle-heatmap',  ['risk-heatmap', 'risk-circles']);
  toggle('toggle-trees',    ['trees-layer']);
  toggle('toggle-critical', ['critical-outer', 'critical-label']);
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
initMap();
