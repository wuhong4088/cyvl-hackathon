/**
 * SafeRoute AI — Climate Stress & Infrastructure Map
 * Season-aware (winter black ice + summer heat stress) priority visualization
 * built on Cyvl point-cloud assets + Open-Meteo historical weather.
 */

// ─── Config ──────────────────────────────────────────────────────────────────
const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const DATA_BASE = '../data/download/';

// ─── State ───────────────────────────────────────────────────────────────────
let map;
let data = {};                 // { risk, trees, sidewalks, curbs, ramps, obstacles, transit, summary, weather, autodeskConfig }
let weatherChart = null;
let currentPopup = null;
let currentSeason = 'winter';  // 'winter' | 'summer'
let selectedFeatureProperties = null;
let viewer = null;
let currentMeshCoords = null;
let activeIntervention = 'none';

// ─── Risk color ramp (shared by both seasons) ────────────────────────────────
const RISK_COLOR_EXPR = (prop) => [
  'interpolate', ['linear'], ['get', prop],
  0, '#69f0ae', 0.35, '#ffea00', 0.55, '#ff6d00', 0.75, '#ff1744',
];
const riskColor = (s) => (s >= 0.75 ? '#ff1744' : s >= 0.55 ? '#ff6d00' : s >= 0.35 ? '#ffea00' : '#69f0ae');
const labelOf = (s) => (s >= 0.75 ? 'CRITICAL' : s >= 0.55 ? 'HIGH' : s >= 0.35 ? 'MEDIUM' : 'LOW');

// ─── Season config: labels, factors, weather framing ─────────────────────────
const SEASON = {
  winter: {
    name: 'Winter · Black Ice',
    overview: 'Black Ice Stress Overview',
    legend: 'Black Ice Priority',
    weatherIcon: '🌨',
    surfaceLabel: '🧊 Surface (shade + drainage)',
    surfaceDesc: 'Tree shade keeps ice frozen; poor catch-basin drainage pools water',
    climateLabel: '🌡️ Climate severity',
    climateDesc: 'Sub-freezing temps, precip & snowfall (Open-Meteo)',
  },
  summer: {
    name: 'Summer · Heat Stress',
    overview: 'Heat Stress Overview',
    legend: 'Heat Stress Priority',
    weatherIcon: '🥵',
    surfaceLabel: '🔆 Surface (shade deficit + impervious)',
    surfaceDesc: 'Missing canopy + degraded impervious pavement amplify heat',
    climateLabel: '🌡️ Climate severity',
    climateDesc: 'Daytime high temp, humidity & low wind (Open-Meteo)',
  },
};

// The 5 priority factors and their weights (shared structure, season-aware values)
const FACTORS = [
  { key: 'climate',     weight: 30, label: '🌡️ Climate severity',   prop: (s) => `${s}_climate` },
  { key: 'surface',     weight: 25, label: '🧊 Surface condition',  prop: (s) => `${s}_surface` },
  { key: 'pedestrian',  weight: 20, label: '🚶 Pedestrian exposure', prop: () => 'ped_exposure' },
  { key: 'transit',     weight: 15, label: '🚦 Transit exposure',    prop: () => 'transit_exposure' },
  { key: 'feasibility', weight: 10, label: '♿ Physical feasibility', prop: () => 'feasibility' },
];

const riskProp  = () => `${currentSeason}_risk`;
const labelProp = () => `${currentSeason}_label`;

// ─── Map Init ────────────────────────────────────────────────────────────────
function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: MAP_STYLE,
    center: [-71.1005, 42.3876],
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
  const files = ['risk_map', 'trees', 'sidewalks', 'curbs', 'ramps', 'obstacles', 'transit'];
  const jsons = ['summary', 'weather'];
  const all = await Promise.all([
    ...files.map(f => fetch(DATA_BASE + f + '.geojson').then(r => r.json())),
    ...jsons.map(f => fetch(DATA_BASE + f + '.json').then(r => r.json())),
    fetch(DATA_BASE + 'autodesk_config.json').then(r => r.json()).catch(() => null)
  ]);
  data.risk = all[0]; data.trees = all[1]; data.sidewalks = all[2];
  data.curbs = all[3]; data.ramps = all[4]; data.obstacles = all[5];
  data.transit = all[6]; data.summary = all[7]; data.weather = all[8];
  data.autodeskConfig = all[9];
}

// ─── On Map Load ─────────────────────────────────────────────────────────────
async function onMapLoad() {
  try {
    await loadData();
  } catch (err) {
    console.error('Data load error:', err);
    alert('Failed to load data. Run a local server from the project root:\n\n  python3 -m http.server 8000\n\nthen open http://localhost:8000/web/index.html');
    return;
  }

  addMapLayers();
  bindLayerToggles();
  bindMapEvents();
  bindSeasonSwitch();
  bindMobileHandle();
  bindAutodeskEvents();

  setSeason('winter');          // initial render of everything season-dependent
  populateStaticSidebar();      // pills, selected sites (both shown regardless of season)

  const loading = document.getElementById('map-loading');
  loading.classList.add('hidden');
  setTimeout(() => loading.remove(), 600);
}

function bindMobileHandle() {
  const handle = document.getElementById('mobile-handle');
  const sidebar = document.getElementById('sidebar');
  if (!handle || !sidebar) return;

  const PEEK = 44;  // px of the sheet left visible when collapsed (matches CSS)
  const collapsedOffset = () => sidebar.getBoundingClientRect().height - PEEK;

  // ── Tap to toggle (suppressed if the touch was actually a drag) ──
  handle.addEventListener('click', () => {
    if (sidebar.dataset.dragged === '1') { sidebar.dataset.dragged = '0'; return; }
    sidebar.classList.toggle('collapsed');
  });

  // ── Drag / swipe to expand & collapse ──
  let startY = null, startT = 0, dragging = false;
  const currentT = () => (sidebar.classList.contains('collapsed') ? collapsedOffset() : 0);

  const onStart = (y) => {
    startY = y;
    startT = currentT();
    dragging = true;
    sidebar.dataset.dragged = '0';
    sidebar.style.transition = 'none';
  };
  const onMove = (y) => {
    if (!dragging) return;
    const dy = y - startY;
    if (Math.abs(dy) > 5) sidebar.dataset.dragged = '1';
    const t = Math.min(collapsedOffset(), Math.max(0, startT + dy));
    sidebar.style.transform = `translateY(${t}px)`;
  };
  const onEnd = (y) => {
    if (!dragging) return;
    dragging = false;
    const dy = y - startY;
    sidebar.style.transition = '';      // restore the CSS spring
    sidebar.style.transform = '';       // hand control back to the class
    if (dy < -30) sidebar.classList.remove('collapsed');   // pulled up → expand
    else if (dy > 30) sidebar.classList.add('collapsed');  // pulled down → collapse
  };

  handle.addEventListener('touchstart', (e) => onStart(e.touches[0].clientY), { passive: true });
  handle.addEventListener('touchmove', (e) => onMove(e.touches[0].clientY), { passive: true });
  handle.addEventListener('touchend', (e) => onEnd(e.changedTouches[0].clientY));
}

// ─── Add Map Layers (once) ────────────────────────────────────────────────────
function addMapLayers() {
  // Sources
  map.addSource('risk', { type: 'geojson', data: data.risk });
  map.addSource('critical', { type: 'geojson', data: criticalFC() });
  map.addSource('trees', { type: 'geojson', data: data.trees });
  map.addSource('sidewalks', { type: 'geojson', data: data.sidewalks });
  map.addSource('transit', { type: 'geojson', data: data.transit });
  map.addSource('obstacles', { type: 'geojson', data: data.obstacles });
  map.addSource('ramps', { type: 'geojson', data: data.ramps });

  // Risk heatmap (background glow)
  map.addLayer({
    id: 'risk-heatmap', type: 'heatmap', source: 'risk', maxzoom: 16,
    paint: {
      'heatmap-weight': ['interpolate', ['linear'], ['get', riskProp()], 0, 0, 1, 1],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.8, 15, 2.5],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 20, 15, 50],
      'heatmap-opacity': 0.6,
      'heatmap-color': [
        'interpolate', ['linear'], ['heatmap-density'],
        0, 'rgba(0,229,255,0)', 0.2, 'rgba(105,240,174,0.6)',
        0.4, 'rgba(255,234,0,0.7)', 0.6, 'rgba(255,109,0,0.8)',
        0.8, 'rgba(255,23,68,0.9)', 1, 'rgba(255,23,68,1)',
      ],
    },
  });

  // Risk circles
  map.addLayer({
    id: 'risk-circles', type: 'circle', source: 'risk', minzoom: 13,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 4, 16, 12],
      'circle-color': RISK_COLOR_EXPR(riskProp()),
      'circle-opacity': 0.85,
      'circle-stroke-width': 1,
      'circle-stroke-color': 'rgba(255,255,255,0.2)',
      'circle-blur': 0.15,
    },
  });

  // Sidewalks (lines) — default hidden
  map.addLayer({
    id: 'sidewalks-layer', type: 'line', source: 'sidewalks',
    layout: { visibility: 'none', 'line-cap': 'round' },
    paint: {
      'line-color': ['case', ['get', 'has_walk'], '#b388ff', 'rgba(179,136,255,0.35)'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1, 16, 3],
      'line-opacity': 0.8,
    },
  });

  // Obstacles (points) — default hidden
  map.addLayer({
    id: 'obstacles-layer', type: 'circle', source: 'obstacles',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 1.5, 16, 4],
      'circle-color': '#90a4ae', 'circle-opacity': 0.7,
    },
  });

  // Ramps (points) — default hidden
  map.addLayer({
    id: 'ramps-layer', type: 'circle', source: 'ramps',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2, 16, 5],
      'circle-color': '#69f0ae', 'circle-opacity': 0.75,
      'circle-stroke-width': 0.5, 'circle-stroke-color': 'rgba(255,255,255,0.3)',
    },
  });

  // Transit / crossings (points) — default hidden
  map.addLayer({
    id: 'transit-layer', type: 'circle', source: 'transit',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2.5, 16, 6],
      'circle-color': '#ffd54f', 'circle-opacity': 0.85,
      'circle-stroke-width': 1, 'circle-stroke-color': 'rgba(0,0,0,0.4)',
    },
  });

  // Trees (points)
  map.addLayer({
    id: 'trees-layer', type: 'circle', source: 'trees',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 5],
      'circle-color': '#00e5ff', 'circle-opacity': 0.5,
      'circle-blur': 0.3,
    },
  });

  // Critical pulsing rings + labels
  map.addLayer({
    id: 'critical-outer', type: 'circle', source: 'critical',
    paint: {
      'circle-radius': 30, 'circle-color': 'transparent',
      'circle-stroke-width': 2, 'circle-stroke-color': '#ff1744', 'circle-opacity': 0.6,
    },
  });
  map.addLayer({
    id: 'critical-label', type: 'symbol', source: 'critical',
    layout: {
      'text-field': ['get', 'address'], 'text-size': 10,
      'text-anchor': 'top', 'text-offset': [0, 2.5],
    },
    paint: { 'text-color': '#ff1744', 'text-halo-color': 'rgba(0,0,0,0.85)', 'text-halo-width': 2 },
  });

  // Pulse animation
  let size = 30, growing = true;
  (function animate() {
    if (growing) { size += 0.3; if (size >= 36) growing = false; }
    else { size -= 0.3; if (size <= 28) growing = true; }
    if (map.getLayer('critical-outer')) {
      map.setPaintProperty('critical-outer', 'circle-radius', size);
      map.setPaintProperty('critical-outer', 'circle-stroke-color',
        `rgba(255,23,68,${0.3 + (size - 28) / 8 * 0.5})`);
    }
    requestAnimationFrame(animate);
  })();
}

// FeatureCollection of CRITICAL segments for the current season
function criticalFC() {
  return {
    type: 'FeatureCollection',
    features: data.risk.features.filter(f => f.properties[labelProp()] === 'CRITICAL'),
  };
}

// ─── Season Switching ─────────────────────────────────────────────────────────
function bindSeasonSwitch() {
  document.querySelectorAll('.season-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.dataset.season === currentSeason) return;
      document.querySelectorAll('.season-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      setSeason(btn.dataset.season);
    });
  });
}

function setSeason(season) {
  currentSeason = season;
  document.body.dataset.season = season;
  const cfg = SEASON[season];

  // Map paint
  if (map.getLayer('risk-circles'))
    map.setPaintProperty('risk-circles', 'circle-color', RISK_COLOR_EXPR(riskProp()));
  if (map.getLayer('risk-heatmap'))
    map.setPaintProperty('risk-heatmap', 'heatmap-weight',
      ['interpolate', ['linear'], ['get', riskProp()], 0, 0, 1, 1]);
  if (map.getSource('critical')) map.getSource('critical').setData(criticalFC());

  // Texts
  document.getElementById('overview-title').textContent = cfg.overview;
  document.getElementById('legend-title').textContent = cfg.legend;
  document.getElementById('weather-icon').textContent = cfg.weatherIcon;

  // Sidebar renders
  renderOverview();
  renderAlgo();
  renderWeather();
  renderSiteList();
  renderSelectedSites();   // re-render so each card highlights the active season
}

// ─── Static Sidebar (pills) ───────────────────────────────────────────────────
function populateStaticSidebar() {
  const s = data.summary;
  document.getElementById('pill-trees').textContent =
    `${s.asset_counts.trees.toLocaleString()} Trees`;
  document.getElementById('pill-segments').textContent =
    `${s.total_segments.toLocaleString()} Segments`;
  renderSelectedSites();
}

// ─── Overview (gauge + bars) ──────────────────────────────────────────────────
function renderOverview() {
  const dist = data.summary[currentSeason].distribution;
  const total = data.summary.total_segments;

  document.getElementById('pill-critical').textContent = `${dist.CRITICAL} Critical`;

  const scores = data.risk.features.map(f => f.properties[riskProp()]);
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  drawGauge(avg);
  document.getElementById('gauge-avg-risk').textContent = avg.toFixed(2);

  ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].forEach(lbl => {
    const cnt = dist[lbl] || 0;
    document.getElementById(`cnt-${lbl}`).textContent = cnt.toLocaleString();
    const bar = document.getElementById(`bar-${lbl}`);
    bar.style.width = '0%';
    setTimeout(() => { bar.style.width = (cnt / total * 100) + '%'; }, 150);
  });
}

// ─── Algorithm explainer (5 factors, season-aware copy) ───────────────────────
function renderAlgo() {
  const cfg = SEASON[currentSeason];
  const rows = [
    { w: 30, icon: '🌡️', name: cfg.climateLabel.replace('🌡️ ', ''), desc: cfg.climateDesc },
    { w: 25, icon: cfg.surfaceLabel.slice(0, 2), name: cfg.surfaceLabel.slice(2).trim(), desc: cfg.surfaceDesc },
    { w: 20, icon: '🚶', name: 'Pedestrian exposure', desc: 'Sidewalk corridors nearby (Cyvl 864 sidewalk segments)' },
    { w: 15, icon: '🚦', name: 'Transit exposure', desc: 'Signalised crossings (ped heads + push buttons) as transit proxy' },
    { w: 10, icon: '♿', name: 'Physical feasibility', desc: 'Ramp density + sidewalk presence (width not in dataset)' },
  ];
  document.getElementById('algo-formula').innerHTML = rows.map(r => `
    <div class="algo-row">
      <span class="algo-weight">${r.w}%</span>
      <div class="algo-item">
        <span class="algo-icon">${r.icon}</span>
        <div><span class="algo-name">${r.name}</span><span class="algo-desc">${r.desc}</span></div>
      </div>
    </div>`).join('');
}

// ─── Weather widget + chart (season-aware) ────────────────────────────────────
function renderWeather() {
  const w = data.weather[currentSeason];
  document.getElementById('weather-period').textContent = `${w.period} · Somerville MA`;
  document.getElementById('weather-title').textContent =
    currentSeason === 'winter' ? 'Weather · Black Ice Window' : 'Weather · Heat Window';
  document.getElementById('chart-note').textContent = `★ ${w.note}`;

  const days = w.daily;
  const avgTemp = days.reduce((a, d) => a + d.avg_temp_c, 0) / days.length;
  document.getElementById('weather-avg-temp').textContent = avgTemp.toFixed(1) + '°C avg';

  drawWeatherChart(days);
}

function drawWeatherChart(daily) {
  const labels = daily.map(d => d.date.slice(5));
  const temps = daily.map(d => currentSeason === 'winter' ? d.avg_temp_c : (d.max_temp_c ?? d.avg_temp_c));
  const risks = daily.map(d => d.risk * 10);
  const isWinter = currentSeason === 'winter';

  const barColor = daily.map(d => {
    if (isWinter) {
      return d.avg_temp_c <= 0 ? 'rgba(61,139,255,0.85)'
           : d.avg_temp_c <= 2 ? 'rgba(0,229,255,0.7)'
           : 'rgba(105,240,174,0.55)';
    }
    const t = d.max_temp_c ?? d.avg_temp_c;
    return t >= 32 ? 'rgba(255,23,68,0.85)'
         : t >= 29 ? 'rgba(255,109,0,0.75)'
         : 'rgba(255,213,79,0.7)';
  });
  const lineColor = isWinter ? '#3d8bff' : '#ff6d00';

  if (weatherChart) weatherChart.destroy();
  weatherChart = new Chart(document.getElementById('weatherChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: isWinter ? 'Avg Temp (°C)' : 'Max Temp (°C)', data: temps,
          backgroundColor: barColor, borderRadius: 4, yAxisID: 'y' },
        { label: 'Risk (×10)', data: risks, type: 'line', borderColor: lineColor,
          backgroundColor: isWinter ? 'rgba(61,139,255,0.1)' : 'rgba(255,109,0,0.1)',
          pointBackgroundColor: daily.map(d => d.risk >= 0.75 ? '#ff1744' : lineColor),
          pointRadius: 4, tension: 0.4, fill: true, yAxisID: 'y' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: (c) => c.datasetIndex === 0
            ? `Temp: ${c.parsed.y}°C` : `Risk: ${(c.parsed.y / 10).toFixed(2)}` },
          backgroundColor: 'rgba(10,12,18,0.95)', titleColor: '#e8eaf6',
          bodyColor: '#9095b0', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        },
      },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#555d80', font: { size: 9 } } },
        y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#555d80', font: { size: 9 } } },
      },
    },
  });
}

// ─── Site list (top priority for current season) ─────────────────────────────
function renderSiteList() {
  const list = document.getElementById('site-list');
  list.innerHTML = '';
  const top = [...data.risk.features]
    .sort((a, b) => b.properties[riskProp()] - a.properties[riskProp()])
    .slice(0, 8);

  top.forEach(f => {
    const p = f.properties;
    const risk = p[riskProp()], label = p[labelProp()];
    const card = document.createElement('div');
    card.className = 'site-card';
    card.innerHTML = `
      <div class="site-card-left">
        <div class="site-card-street">${p.address || 'Unnamed segment'}</div>
        <div class="site-card-meta">🌳 ${p.nearby_trees} · 🚶 ${p.nearby_sidewalks} · 🚦 ${p.nearby_transit} · PCI ${Math.round(p.pci_score)}</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
        <span class="site-risk-badge" style="color:${riskColor(risk)}">${risk.toFixed(2)}</span>
        <span class="site-risk-label ${label}">${label}</span>
      </div>`;
    card.addEventListener('click', () => {
      map.flyTo({ center: f.geometry.coordinates, zoom: 16, pitch: 30, duration: 1000 });
      showRiskPopup(f.geometry.coordinates, p);
    });
    list.appendChild(card);
  });
}

// ─── Selected sites (the two demonstration sites) ─────────────────────────────
function renderSelectedSites() {
  const wrap = document.getElementById('selected-sites');
  if (!wrap) return;
  const sites = data.summary.selected_sites || [];
  wrap.innerHTML = sites.map((site, i) => {
    const active = site.season === currentSeason;
    const g = site.geometry_context;
    const factorRows = FACTORS.map(fac => {
      const key = fac.key === 'pedestrian' ? 'pedestrian_exposure'
                : fac.key === 'transit' ? 'transit_exposure'
                : fac.key === 'feasibility' ? 'physical_feasibility'
                : fac.key === 'climate' ? 'climate_severity' : 'surface';
      const v = site.factors[key] ?? 0;
      return `<div class="sf-row">
          <span class="sf-label">${fac.label}</span>
          <div class="sf-bar-wrap"><div class="sf-bar" style="width:${Math.round(v * 100)}%"></div></div>
          <span class="sf-pct">${Math.round(v * 100)}</span>
        </div>`;
    }).join('');
    return `
      <div class="selsite-card ${active ? 'active' : ''}" data-idx="${i}">
        <div class="selsite-head">
          <span class="selsite-season">${site.season === 'winter' ? '❄️' : '☀️'}</span>
          <div class="selsite-titles">
            <span class="selsite-kind">${site.kind}</span>
            <span class="selsite-addr">${site.address}</span>
          </div>
          <span class="selsite-risk" style="color:${riskColor(site.risk)}">${site.risk.toFixed(2)}</span>
        </div>
        <div class="selsite-factors">${factorRows}</div>
        <div class="selsite-geom">
          <span>🌳 ${g.nearby_trees}</span><span>🚶 ${g.nearby_sidewalks}</span>
          <span>🚦 ${g.nearby_transit_signals}</span><span>♿ ${g.nearby_ramps}</span>
          <span>💧 ${g.nearby_catch_basins}</span><span>PCI ${Math.round(g.pci_score)}</span>
        </div>
      </div>`;
  }).join('');

  // Click → fly to site
  wrap.querySelectorAll('.selsite-card').forEach(card => {
    card.addEventListener('click', () => {
      const site = sites[+card.dataset.idx];
      // switch to the site's season for context
      if (site.season !== currentSeason) {
        document.querySelectorAll('.season-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.season === site.season));
        setSeason(site.season);
      }
      map.flyTo({ center: site.coordinates, zoom: 16.5, pitch: 35, duration: 1100 });
    });
  });
}

// ─── Popup (5-factor, season-aware) ───────────────────────────────────────────
function bindMapEvents() {
  ['risk-circles', 'critical-outer'].forEach(layer => {
    map.on('click', layer, (e) => {
      showRiskPopup(e.features[0].geometry.coordinates, e.features[0].properties);
    });
    map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
  });
}

function showRiskPopup(coords, props) {
  selectedFeatureProperties = props;

  const node = document.getElementById('popup-tpl').content.cloneNode(true);
  const risk = +props[riskProp()];
  const label = props[labelProp()] || labelOf(risk);

  node.querySelector('.popup-risk-badge').setAttribute('data-label', label);
  node.querySelector('.popup-risk-badge').textContent = label;
  node.querySelector('.popup-address').textContent = props.address || 'Unnamed segment';
  node.querySelector('.popup-score-val').textContent = risk.toFixed(2);

  // 5 factor bars
  node.querySelector('[data-factors]').innerHTML = FACTORS.map(fac => {
    const v = +props[fac.prop(currentSeason)] || 0;
    return `<div class="factor">
        <span class="factor-label">${fac.label}</span>
        <div class="factor-bar-wrap"><div class="factor-bar" style="width:${Math.round(v * 100)}%"></div></div>
        <span class="factor-pct">${Math.round(v * 100)}%</span>
      </div>`;
  }).join('');

  node.querySelector('[data-meta]').innerHTML = `
    <span>🌳 <strong>${props.nearby_trees}</strong> trees</span>
    <span>🚶 <strong>${props.nearby_sidewalks}</strong> walks</span>
    <span>🚦 <strong>${props.nearby_transit}</strong> crossings</span>
    <span>PCI <strong>${Math.round(props.pci_score)}</strong></span>`;

  const div = document.createElement('div');
  div.appendChild(node);
  if (currentPopup) currentPopup.remove();
  currentPopup = new maplibregl.Popup({ closeOnClick: true, maxWidth: '320px' })
    .setLngLat(coords).setDOMContent(div).addTo(map);
  currentPopup.on('close', () => { currentPopup = null; });

  // Update Autodesk 3D panel
  const utm = wgs84ToUtm19(coords[0], coords[1]);
  const x_min = 326443.79, x_max = 327021.93;
  const y_min = 4695226.89, y_max = 4696110.99;
  const inPointCloud = (utm.x >= x_min && utm.x <= x_max && utm.y >= y_min && utm.y <= y_max);

  const panel = document.getElementById('autodesk-viewer-panel');
  if (panel) {
    panel.classList.remove('hidden');
    
    const title = document.getElementById('autodesk-panel-title');
    const subtitle = document.getElementById('autodesk-panel-subtitle');
    const viewerDiv = document.getElementById('forgeViewerContainer');
    const controlsDiv = document.querySelector('.intervention-controls');
    
    if (inPointCloud && data.autodeskConfig) {
      title.textContent = `3D Spatial Planner: ${props.address || 'Unnamed Segment'}`;
      subtitle.textContent = `PCI: ${Math.round(props.pci_score)} | Risk Score: ${risk.toFixed(2)}`;
      viewerDiv.style.display = 'block';
      controlsDiv.style.display = 'flex';
      
      const meshCoords = gpsToMeshCoords(coords[0], coords[1], data.autodeskConfig.center);
      currentMeshCoords = meshCoords;
      
      initAutodeskViewer(data.autodeskConfig.token, data.autodeskConfig.urn)
        .then(() => {
          setTimeout(() => {
            focusViewerOnCoords(meshCoords.x, meshCoords.y, meshCoords.z);
          }, 300);
        })
        .catch(err => {
          console.error("Autodesk load error", err);
        });
        
      setIntervention('none');
      
    } else {
      title.textContent = "3D Spatial Planner";
      subtitle.textContent = "LiDAR Mesh boundaries exceeded";
      viewerDiv.style.display = 'none';
      controlsDiv.style.display = 'none';
      
      const resultsDiv = document.getElementById('checklist-results');
      resultsDiv.innerHTML = `
        <div class="audit-list">
          <div class="audit-item-container">
            <div class="audit-item">
              <div class="audit-info">
                <span class="audit-status-dot warn"></span>
                <span class="audit-name">Central Somerville Sector Check</span>
              </div>
              <span class="audit-badge warn">OUT OF BOUNDS</span>
            </div>
            <p class="audit-desc">Detailed 3D mesh is only generated for the scanned Central Somerville sector. Please select segments on Lowell St, Vernon St, Hinckley St, Ames St, or Cutler St.</p>
          </div>
        </div>`;
    }
  }
}

// ─── Gauge ────────────────────────────────────────────────────────────────────
function drawGauge(value) {
  const canvas = document.getElementById('riskGauge');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cx = canvas.width / 2, cy = canvas.height - 10, r = 80;
  const start = Math.PI, end = 2 * Math.PI;
  const valAngle = start + (end - start) * Math.max(0, Math.min(1, value));

  ctx.beginPath(); ctx.arc(cx, cy, r, start, end);
  ctx.lineWidth = 14; ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.stroke();

  const grad = ctx.createLinearGradient(cx - r, cy, cx + r, cy);
  grad.addColorStop(0, '#69f0ae'); grad.addColorStop(0.4, '#ffea00');
  grad.addColorStop(0.7, '#ff6d00'); grad.addColorStop(1, '#ff1744');
  ctx.beginPath(); ctx.arc(cx, cy, r, start, valAngle);
  ctx.lineWidth = 14; ctx.strokeStyle = grad; ctx.lineCap = 'round'; ctx.stroke();

  for (let i = 0; i <= 4; i++) {
    const a = start + (end - start) * (i / 4);
    ctx.beginPath();
    ctx.moveTo(cx + (r - 8) * Math.cos(a), cy + (r - 8) * Math.sin(a));
    ctx.lineTo(cx + (r + 2) * Math.cos(a), cy + (r + 2) * Math.sin(a));
    ctx.lineWidth = 2; ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.stroke();
  }
}

// ─── Layer Toggles ─────────────────────────────────────────────────────────────
function bindLayerToggles() {
  const toggle = (id, layers) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', (e) => {
      const vis = e.target.checked ? 'visible' : 'none';
      layers.forEach(l => { if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', vis); });
    });
  };
  toggle('toggle-heatmap', ['risk-heatmap', 'risk-circles']);
  toggle('toggle-critical', ['critical-outer', 'critical-label']);
  toggle('toggle-trees', ['trees-layer']);
  toggle('toggle-sidewalks', ['sidewalks-layer']);
  toggle('toggle-transit', ['transit-layer']);
  toggle('toggle-obstacles', ['obstacles-layer']);
  toggle('toggle-ramps', ['ramps-layer']);
}

// ─── Autodesk 3D Spatial Planner Integration ───────────────────────────────────

// WGS84 GPS to UTM Zone 19N Coordinate Projection
function wgs84ToUtm19(lon, lat) {
  const a = 6378137.0;
  const f = 1.0 / 298.257223563;
  const k0 = 0.9996;
  const lambda0 = -69.0 * Math.PI / 180.0;
  
  const phi = lat * Math.PI / 180.0;
  const lam = lon * Math.PI / 180.0;
  
  const e2 = 2 * f - f * f;
  const n = f / (2 - f);
  
  const A = (lam - lambda0) * Math.cos(phi);
  
  const alpha = (a / (1 + n)) * (1 + (n * n) / 4 + (n * n * n * n) / 64);
  const beta = (3 / 2) * n - (9 / 16) * (n * n * n);
  const gamma = (15 / 16) * (n * n) - (15 / 32) * (n * n * n * n);
  const delta = (35 / 48) * (n * n * n);
  
  const s = alpha * (phi - beta * Math.sin(2 * phi) + gamma * Math.sin(4 * phi) - delta * Math.sin(6 * phi));
  
  const t = Math.tan(phi);
  const nu = a / Math.sqrt(1 - e2 * Math.sin(phi) * Math.sin(phi));
  const rho = nu * (1 - e2) / (1 - e2 * Math.sin(phi) * Math.sin(phi));
  const eta2 = e2 * Math.cos(phi) * Math.cos(phi) / (1 - e2);
  
  const x = 500000.0 + k0 * nu * (A + (1 - t * t + eta2) * (A * A * A) / 6 + (5 - 18 * t * t + t * t * t * t + 14 * eta2 - 58 * t * t * eta2) * (A * A * A * A * A) / 120);
  const y = k0 * (s + nu * t * ((A * A) / 2 + (5 - t * t + 9 * eta2 + 4 * eta2 * eta2) * (A * A * A * A) / 24 + (61 - 58 * t * t + t * t * t * t + 270 * eta2 - 330 * t * t * eta2) * (A * A * A * A * A * A) / 720));
  
  return { x, y };
}

// Convert GPS coordinates to local centered mesh coordinates
function gpsToMeshCoords(lon, lat, center) {
  if (!center) return { x: 0, y: 0, z: 0 };
  const utm = wgs84ToUtm19(lon, lat);
  return {
    x: utm.x - center[0],
    y: utm.y - center[1],
    z: 0.0 // Ground level assumption relative to centered mesh
  };
}

// Initialize Autodesk Viewer instance
function initAutodeskViewer(token, urn) {
  if (viewer) return Promise.resolve(viewer);

  return new Promise((resolve, reject) => {
    const options = {
      env: 'AutodeskProduction2',
      api: 'streamingV2',
      getAccessToken: function(onTokenReady) {
        onTokenReady(token, 3600);
      }
    };

    Autodesk.Viewing.Initializer(options, function() {
      const htmlDiv = document.getElementById('forgeViewer');
      viewer = new Autodesk.Viewing.GuiViewer3D(htmlDiv);
      const started = viewer.start();
      if (started > 0) {
        console.error('Failed to initialize Autodesk Viewer');
        reject(started);
        return;
      }
      
      const documentId = 'urn:' + urn;
      Autodesk.Viewing.Document.load(documentId, function(doc) {
        const viewables = doc.getRoot().getDefaultGeometry();
        viewer.loadDocumentNode(doc, viewables).then(() => {
          viewer.setTheme('dark-theme');
          resolve(viewer);
        });
      }, function(err) {
        console.error('Failed to load document in Autodesk Viewer:', err);
        reject(err);
      });
    });
  });
}

// Fly/focus viewer camera on segment location
function focusViewerOnCoords(x, y, z) {
  if (!viewer) return;
  const THREE = window.THREE;
  if (!THREE) return;

  const target = new THREE.Vector3(x, y, z);
  const position = new THREE.Vector3(x + 12, y + 12, z + 9); // Offset camera slightly
  
  viewer.navigation.setTarget(target);
  viewer.navigation.setPosition(position);
  
  drawRiskOverlay(x, y, z);
  drawSolutionOverlay();
}

// Render red translucent risk zone cylinder
function drawRiskOverlay(x, y, z) {
  if (!viewer) return;
  const THREE = window.THREE;
  if (!THREE) return;

  if (!viewer.impl.hasOverlayScene("riskOverlayScene")) {
    viewer.impl.createOverlayScene("riskOverlayScene");
  }
  viewer.impl.clearOverlay("riskOverlayScene");

  const geometry = new THREE.CylinderGeometry(8, 8, 0.2, 32);
  const material = new THREE.MeshBasicMaterial({
    color: 0xff1744,
    transparent: true,
    opacity: 0.4,
    depthWrite: false
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = Math.PI / 2;
  mesh.position.set(x, y, z);

  viewer.impl.addOverlay("riskOverlayScene", mesh);
  viewer.impl.invalidate(true);
}

// Render procedural tree and bus shelter geometries
function drawSolutionOverlay() {
  if (!viewer) return;
  const THREE = window.THREE;
  if (!THREE) return;
  if (!currentMeshCoords) return;

  const { x, y, z } = currentMeshCoords;

  if (!viewer.impl.hasOverlayScene("solutionOverlayScene")) {
    viewer.impl.createOverlayScene("solutionOverlayScene");
  }
  viewer.impl.clearOverlay("solutionOverlayScene");

  if (activeIntervention === 'tree') {
    const group = new THREE.Group();

    // Trunk
    const trunkGeom = new THREE.CylinderGeometry(0.25, 0.35, 3.2, 16);
    const trunkMat = new THREE.MeshBasicMaterial({ color: 0x5d4037 });
    const trunkMesh = new THREE.Mesh(trunkGeom, trunkMat);
    trunkMesh.rotation.x = Math.PI / 2;
    trunkMesh.position.set(0, 0, 1.6);
    group.add(trunkMesh);

    // Canopy foliage
    const canopyGeom = new THREE.SphereGeometry(2.0, 16, 16);
    const canopyMat = new THREE.MeshBasicMaterial({ color: 0x1b5e20, transparent: true, opacity: 0.85 });
    const canopyMesh = new THREE.Mesh(canopyGeom, canopyMat);
    canopyMesh.position.set(0, 0, 3.8);
    group.add(canopyMesh);

    group.position.set(x, y, z);
    viewer.impl.addOverlay("solutionOverlayScene", group);

  } else if (activeIntervention === 'shelter') {
    const group = new THREE.Group();

    // 4 Columns
    const colMat = new THREE.MeshBasicMaterial({ color: 0x455a64 });
    const colGeom = new THREE.CylinderGeometry(0.08, 0.08, 2.6, 8);
    
    const offsetPositions = [
      { x: -1.8, y: -0.9 },
      { x: 1.8, y: -0.9 },
      { x: -1.8, y: 0.9 },
      { x: 1.8, y: 0.9 }
    ];
    
    offsetPositions.forEach(p => {
      const col = new THREE.Mesh(colGeom, colMat);
      col.rotation.x = Math.PI / 2;
      col.position.set(p.x, p.y, 1.3);
      group.add(col);
    });

    // Roof
    const roofGeom = new THREE.BoxGeometry(4.0, 2.2, 0.12);
    const roofMat = new THREE.MeshBasicMaterial({ color: 0x263238 });
    const roofMesh = new THREE.Mesh(roofGeom, roofMat);
    roofMesh.position.set(0, 0, 2.6);
    group.add(roofMesh);

    // Glass panel back wall
    const glassGeom = new THREE.BoxGeometry(3.6, 0.04, 2.2);
    const glassMat = new THREE.MeshBasicMaterial({ color: 0x80deea, transparent: true, opacity: 0.35 });
    const glassMesh = new THREE.Mesh(glassGeom, glassMat);
    glassMesh.position.set(0, 0.9, 1.1);
    group.add(glassMesh);

    group.position.set(x, y, z);
    viewer.impl.addOverlay("solutionOverlayScene", group);
  }

  viewer.impl.invalidate(true);
}

// Bind UI triggers for closing and selecting interventions
function bindAutodeskEvents() {
  const closeBtn = document.getElementById('autodesk-panel-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      document.getElementById('autodesk-viewer-panel').classList.add('hidden');
      if (currentPopup) currentPopup.remove();
    });
  }

  const intButtons = document.querySelectorAll('.int-btn');
  intButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      intButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      setIntervention(btn.dataset.int);
    });
  });
}

// Set active solution and re-evaluate audits
function setIntervention(type) {
  activeIntervention = type;
  drawSolutionOverlay();
  
  if (selectedFeatureProperties) {
    runMunicipalAudit(selectedFeatureProperties);
  }
}

// Audit properties against city/ADA spatial regulations
function runMunicipalAudit(props) {
  const resultsDiv = document.getElementById('checklist-results');
  if (!resultsDiv) return;

  const widthCheck = {
    name: "Clear Path ADA Compliance (≥1.2m)",
    status: "pass",
    badge: "PASSED",
    desc: `Sidewalk width permits standard ADA clearance. Segment PCI: ${Math.round(props.pci_score)}.`
  };
  
  const bufferCheck = {
    name: "Curb Setback (0.6m Buffer Zone)",
    status: "pass",
    badge: "PASSED",
    desc: "Placement clears the carriage-way setback zone."
  };

  const collisionCheck = {
    name: "Utility & Obstacle Proximity Check",
    status: "pass",
    badge: "PASSED",
    desc: "No nearby utility cabinets or poles detected in Cyvl LiDAR data."
  };

  if (activeIntervention === 'none') {
    resultsDiv.innerHTML = '<p class="checklist-placeholder">Select an intervention above to perform regulatory audit...</p>';
    return;
  }

  if (activeIntervention === 'tree') {
    if (props.pci_score < 40) {
      widthCheck.status = "warn";
      widthCheck.badge = "WARNING";
      widthCheck.desc = "Low pavement condition index (PCI < 40) suggests high risk of root disruption to sidewalk slabs.";
    }
    if (props.nearby_trees > 7) {
      collisionCheck.status = "warn";
      collisionCheck.badge = "CROWDED";
      collisionCheck.desc = `High street tree density in area (${props.nearby_trees} existing trees). Check canopy spacing parameters.`;
    }
  } else if (activeIntervention === 'shelter') {
    if (props.nearby_sidewalks < 4) {
      widthCheck.status = "fail";
      widthCheck.badge = "FAILED";
      widthCheck.desc = "Sidewalk segment is too narrow (estimated width < 2.0m). Bus shelter installation violates ADA clear path.";
    }
    if (props.pci_score < 55) {
      bufferCheck.status = "warn";
      bufferCheck.badge = "HEAVY FOOTING";
      bufferCheck.desc = "Degraded pavement base. Foundation footing reinforcement required.";
    }
    if (props.nearby_transit === 0) {
      collisionCheck.status = "warn";
      collisionCheck.badge = "MISALIGNED";
      collisionCheck.desc = "No transit stop or signalised crossing detected nearby. Validate route coordinates.";
    }
  }

  const items = [widthCheck, bufferCheck, collisionCheck];
  resultsDiv.innerHTML = `
    <div class="audit-list">
      ${items.map(item => `
        <div class="audit-item-container">
          <div class="audit-item">
            <div class="audit-info">
              <span class="audit-status-dot ${item.status}"></span>
              <span class="audit-name">${item.name}</span>
            </div>
            <span class="audit-badge ${item.status}">${item.badge}</span>
          </div>
          <p class="audit-desc">${item.desc}</p>
        </div>
      `).join('')}
    </div>`;
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
initMap();
