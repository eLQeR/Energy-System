// Метрики (заміна Grafana). Реюзає /api/devices та /api/devices/<id>/history.

const REFRESH_MS = 10000;
let currentDeviceId = null;
let currentMinutes  = 360;
let _timer = null;

// ── Chart.js shared defaults ────────────────────────────────────────────
const chartCommon = {
  options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    scales: {
      x: { ticks: { color: "#8b949e", maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
           grid: { color: "#21262d" } },
      y: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" }, beginAtZero: false },
    },
    plugins: {
      legend: { labels: { color: "#e6edf3", font: { size: 11 } } },
      tooltip: { backgroundColor: "#161b22", borderColor: "#30363d", borderWidth: 1 },
    },
  },
};
const dsBase = { tension: 0.25, spanGaps: true, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2 };

let chartTemps, chartCop, chartEnergyBar, chartEnergyTotal, gaugeChart;

function initCharts() {
  chartTemps = new Chart(document.getElementById("chart-temps"), {
    type: "line", ...chartCommon,
    data: { labels: [], datasets: [
      { ...dsBase, label: "Подача, °C",   data: [],
        borderColor: "#f85149", backgroundColor: "rgba(248,81,73,.10)" },
      { ...dsBase, label: "Зворотка, °C", data: [],
        borderColor: "#d29922", backgroundColor: "rgba(210,153,34,.08)" },
      { ...dsBase, label: "ΔT, °C",       data: [],
        borderColor: "#79c0ff", backgroundColor: "rgba(121,192,255,.08)",
        borderDash: [4, 3] },
    ]},
  });

  chartCop = new Chart(document.getElementById("chart-cop"), {
    type: "line", ...chartCommon,
    data: { labels: [], datasets: [
      { ...dsBase, label: "COP", data: [],
        borderColor: "#3fb950", backgroundColor: "rgba(63,185,80,.10)",
        fill: true },
    ]},
  });

  chartEnergyBar = new Chart(document.getElementById("chart-energy-bar"), {
    type: "bar", ...chartCommon,
    data: { labels: [], datasets: [
      { label: "Споживання за 1 хв, кВт·год", data: [],
        backgroundColor: "rgba(88,166,255,.55)", borderColor: "#58a6ff", borderWidth: 1 },
    ]},
  });

  chartEnergyTotal = new Chart(document.getElementById("chart-energy-total"), {
    type: "line", ...chartCommon,
    data: { labels: [], datasets: [
      { ...dsBase, label: "Накопичена енергія, кВт·год", data: [],
        borderColor: "#bc8cff", backgroundColor: "rgba(188,140,255,.10)",
        fill: true, stepped: false },
    ]},
  });

  // COP gauge (doughnut з обрізаною дугою — імітує спідометр).
  const gaugeCtx = document.getElementById("cop-gauge").getContext("2d");
  gaugeChart = new Chart(gaugeCtx, {
    type: "doughnut",
    data: {
      datasets: [{
        data: [0, 5],                     // [value, remainder]
        backgroundColor: ["#3fb950", "#21262d"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      circumference: 180, rotation: 270, cutout: "75%",
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

// ── Helpers ─────────────────────────────────────────────────────────────
function fmtLabel(iso) {
  return new Date(iso).toLocaleTimeString("uk-UA", { hour12: false }).slice(0, 5);
}

function modeUkr(m) {
  return ({ heating: "опалення", cooling: "охолодження",
            dhw: "ГВП", standby: "очікування", unknown: "—" }[m]) || m || "—";
}

function copColor(cop) {
  if (cop == null) return "#8b949e";
  if (cop >= 3.5)  return "#3fb950";   // good
  if (cop >= 2.8)  return "#d29922";   // ok
  return "#f85149";                    // bad
}

// ── Device loader ───────────────────────────────────────────────────────
async function loadDevices() {
  const devs = await api("/api/devices").catch(() => []);
  const sel = document.getElementById("dash-device");
  sel.innerHTML = devs.map(d =>
    `<option value="${esc(d.id)}">${esc(d.label || d.id)} (${esc(d.id)})</option>`
  ).join("");
  if (devs.length) {
    // Перевага пристрою з реальними метриками — інакше беремо перший.
    const live = devs.find(d => !d.stale && d.last_seen) || devs[0];
    currentDeviceId = live.id;
    sel.value = currentDeviceId;
  }
}

// ── Refresh ─────────────────────────────────────────────────────────────
async function refresh() {
  if (!currentDeviceId) return;
  const statusEl = document.getElementById("dash-status");
  try {
    const data = await api(`/api/devices/${encodeURIComponent(currentDeviceId)}/history?minutes=${currentMinutes}`);
    const pts = data.points || [];
    renderCharts(pts);

    // Поточні значення з останнього метрик-snapshot (через /api/devices/<id>)
    const live = await api(`/api/devices/${encodeURIComponent(currentDeviceId)}`).catch(() => null);
    renderStats(live, pts);

    statusEl.textContent =
      `${pts.length} точок · оновлено ${new Date().toLocaleTimeString("uk-UA", { hour12: false })}`;
    setLive(true);
  } catch (err) {
    statusEl.textContent = "Помилка: " + err.message;
    setLive(false);
  }
}

function renderCharts(pts) {
  const labels   = pts.map(p => fmtLabel(p.timestamp));
  chartTemps.data.labels                = labels;
  chartTemps.data.datasets[0].data      = pts.map(p => p.flow_temp_c);
  chartTemps.data.datasets[1].data      = pts.map(p => p.return_temp_c);
  chartTemps.data.datasets[2].data      = pts.map(p => p.delta_t_c);
  chartTemps.update("none");

  chartCop.data.labels             = labels;
  chartCop.data.datasets[0].data   = pts.map(p => p.cop);
  chartCop.update("none");

  // BarChart: різниця накопиченої енергії між сусідніми точками = споживання за хв.
  const energy = pts.map(p => p.energy_kwh);
  const diffs = energy.map((v, i) => {
    if (i === 0 || v == null || energy[i-1] == null) return null;
    const d = v - energy[i-1];
    return d >= 0 ? d : null;       // ігноруємо рестарти лічильника
  });
  chartEnergyBar.data.labels             = labels;
  chartEnergyBar.data.datasets[0].data   = diffs;
  chartEnergyBar.update("none");

  chartEnergyTotal.data.labels             = labels;
  chartEnergyTotal.data.datasets[0].data   = energy;
  chartEnergyTotal.update("none");
}

function renderStats(live, pts) {
  const last = pts.length ? pts[pts.length - 1] : null;
  const liveMetrics = live?.metrics || {};

  const cop   = liveMetrics.cop          ?? last?.cop          ?? null;
  const power = liveMetrics.power_kw     ?? last?.power_kw     ?? null;
  const mode  = liveMetrics.mode         ?? "unknown";

  // COP gauge: 0..5 шкала
  const copValue = cop != null ? Math.max(0, Math.min(5, cop)) : 0;
  gaugeChart.data.datasets[0].data = [copValue, 5 - copValue];
  gaugeChart.data.datasets[0].backgroundColor[0] = copColor(cop);
  gaugeChart.update("none");
  document.getElementById("cop-text").textContent = cop != null ? cop.toFixed(2) : "—";
  document.getElementById("cop-text").style.color = copColor(cop);

  document.getElementById("stat-power").textContent   = power != null ? power.toFixed(2) : "—";
  document.getElementById("stat-mode").textContent    = modeUkr(mode);
}

// ── Wire-up ─────────────────────────────────────────────────────────────
document.getElementById("dash-device").addEventListener("change", e => {
  currentDeviceId = e.target.value;
  refresh();
});
document.getElementById("dash-range").addEventListener("change", e => {
  currentMinutes = Number(e.target.value);
  refresh();
});

initCharts();
loadDevices().then(() => {
  refresh();
  _timer = setInterval(refresh, REFRESH_MS);
});
