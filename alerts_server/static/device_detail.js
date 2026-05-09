// Сторінка деталей пристрою: специфікація, межі, графіки, історія тривог.

let chartPower, chartTemp;

function initCharts() {
  const dsBase = { tension: 0.25, spanGaps: true, pointRadius: 2, pointHoverRadius: 5,
                   borderWidth: 2 };
  const common = {
    type: "line",
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: "#8b949e", maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
             grid: { color: "#21262d" } },
        y: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" }, beginAtZero: false },
      },
      plugins: {
        legend: { labels: { color: "#e6edf3" } },
        tooltip: { backgroundColor: "#161b22", borderColor: "#30363d", borderWidth: 1 },
      },
    },
  };

  chartPower = new Chart(document.getElementById("chart-power"), {
    ...common,
    data: {
      labels: [],
      datasets: [
        { ...dsBase, label: "Потужність, кВт", data: [],
          borderColor: "#58a6ff", backgroundColor: "rgba(88,166,255,.12)",
          fill: true, yAxisID: "y" },
        { ...dsBase, label: "COP", data: [],
          borderColor: "#3fb950", backgroundColor: "rgba(63,185,80,.08)",
          yAxisID: "y1" },
      ],
    },
    options: {
      ...common.options,
      scales: {
        ...common.options.scales,
        y:  { ...common.options.scales.y, position: "left",
              title: { display: true, text: "кВт", color: "#8b949e" } },
        y1: { ticks: { color: "#3fb950" }, grid: { drawOnChartArea: false }, position: "right",
              title: { display: true, text: "COP", color: "#3fb950" } },
      },
    },
  });

  chartTemp = new Chart(document.getElementById("chart-temp"), {
    ...common,
    data: {
      labels: [],
      datasets: [
        { ...dsBase, label: "Подача, °C",   data: [],
          borderColor: "#f85149", backgroundColor: "rgba(248,81,73,.10)" },
        { ...dsBase, label: "Зворотка, °C", data: [],
          borderColor: "#d29922", backgroundColor: "rgba(210,153,34,.08)" },
        { ...dsBase, label: "Надворі, °C",  data: [],
          borderColor: "#79c0ff", backgroundColor: "rgba(121,192,255,.08)" },
      ],
    },
  });
}

// Зберігаємо снапшоти метрик у пам'яті, щоб мати ковзне вікно.
const HISTORY_LEN = 60;
const history = { ts: [], labels: [], power: [], cop: [], flow: [], ret: [], outdoor: [] };

function _formatLabel(iso) {
  return new Date(iso).toLocaleTimeString("uk-UA", { hour12: false }).slice(0, 5);
}

function _renderCharts() {
  chartPower.data.labels = history.labels;
  chartPower.data.datasets[0].data = history.power;
  chartPower.data.datasets[1].data = history.cop;
  chartPower.update("none");

  chartTemp.data.labels = history.labels;
  chartTemp.data.datasets[0].data = history.flow;
  chartTemp.data.datasets[1].data = history.ret;
  chartTemp.data.datasets[2].data = history.outdoor;
  chartTemp.update("none");
}

function pushSnapshot(metrics, ts) {
  // Не дублюємо точку з тим самим timestamp
  if (history.ts.length && history.ts[history.ts.length - 1] === ts) return;
  history.ts.push(ts);
  history.labels.push(_formatLabel(ts));
  history.power.push(metrics.power_kw   ?? null);
  history.cop.push(metrics.cop          ?? null);
  history.flow.push(metrics.flow_temp_c ?? null);
  history.ret.push(metrics.return_temp_c ?? null);
  history.outdoor.push(metrics.outdoor_temp_c ?? null);
  for (const k of Object.keys(history)) {
    if (history[k].length > HISTORY_LEN) history[k].shift();
  }
  _renderCharts();
}

async function seedHistory() {
  try {
    const data = await api(`/api/devices/${encodeURIComponent(DEVICE_ID)}/history?minutes=60`);
    if (!data.points || !data.points.length) return;
    history.ts.length = 0; history.labels.length = 0;
    history.power.length = 0; history.cop.length = 0;
    history.flow.length = 0; history.ret.length = 0; history.outdoor.length = 0;
    for (const p of data.points) {
      history.ts.push(p.timestamp);
      history.labels.push(_formatLabel(p.timestamp));
      history.power.push(p.power_kw       ?? null);
      history.cop.push(p.cop              ?? null);
      history.flow.push(p.flow_temp_c     ?? null);
      history.ret.push(p.return_temp_c    ?? null);
      history.outdoor.push(p.outdoor_temp_c ?? null);
    }
    _renderCharts();
  } catch (e) {
    console.warn("history seed failed:", e.message);
  }
}

let lastTs = "";
async function refresh() {
  try {
    const d = await api(`/api/devices/${encodeURIComponent(DEVICE_ID)}`);
    setLive(true);

    document.getElementById("dev-title").textContent = d.specs?.label || DEVICE_ID;
    document.getElementById("dev-model").textContent =
      [d.specs?.manufacturer, d.specs?.model || d.specs?.modelSeries].filter(Boolean).join(" · ");

    const stale = d.stale;
    const stEl  = document.getElementById("dev-state");
    stEl.textContent = stateText(d.current_state || "unknown", stale);
    stEl.className   = `state-badge ${stale ? 'stale' : (d.current_state || 'unknown')}`;

    renderMetrics(d.metrics);
    document.getElementById("last-seen").textContent =
      d.last_seen ? `Останнє оновлення: ${fmtTime(d.last_seen)}` : "Метрик ще не отримано.";

    renderBounds(d.bounds, d.metrics);
    renderSpecs(d.specs);
    renderTimeline(d.alerts);

    if (Object.keys(d.metrics || {}).length && d.last_seen && d.last_seen !== lastTs) {
      pushSnapshot(d.metrics, d.last_seen);
      lastTs = d.last_seen;
    }
  } catch (err) {
    setLive(false);
    console.error(err);
  }
}

function renderMetrics(m) {
  const root = document.getElementById("current-metrics");
  if (!m || !Object.keys(m).length) {
    root.innerHTML = `<div style="grid-column:1/-1;color:var(--text-dim);">Метрик не отримано.</div>`;
    return;
  }
  root.innerHTML = `
    <div class="k">Потужність</div><div class="v">${(m.power_kw ?? 0).toFixed(2)} кВт</div>
    <div class="k">Енергія</div>   <div class="v">${(m.energy_kwh ?? 0).toFixed(2)} кВт·год</div>
    <div class="k">Подача</div>    <div class="v">${(m.flow_temp_c ?? 0).toFixed(1)} °C</div>
    <div class="k">Зворотка</div>  <div class="v">${(m.return_temp_c ?? 0).toFixed(1)} °C</div>
    <div class="k">Надворі</div>   <div class="v">${m.outdoor_temp_c != null ? m.outdoor_temp_c.toFixed(1) + ' °C' : '—'}</div>
    <div class="k">COP</div>       <div class="v">${m.cop != null ? m.cop.toFixed(2) : '—'}</div>
    <div class="k">Режим</div>     <div class="v">${esc(m.mode || '—')}</div>`;
}

function renderBounds(b, m = {}) {
  const root = document.getElementById("bounds-info");
  if (!b || !Object.keys(b).length) {
    root.innerHTML = `<div style="grid-column:1/-1;color:var(--text-dim);">Межі недоступні.</div>`;
    return;
  }
  const row = (label, val, suffix, current, cmp) => {
    if (val == null) return "";
    let badge = "";
    if (current != null && cmp(current, val)) {
      badge = ` <span style="color:var(--anomaly);font-size:11px;">⚠ порушено</span>`;
    }
    return `
      <div class="k">${esc(label)}</div>
      <div class="v">${val.toFixed(2)} ${esc(suffix)}${badge}</div>`;
  };
  root.innerHTML =
    row("Макс. потужність",  b.max_power_kw, "кВт",  m.power_kw,    (cur, lim) => cur > lim) +
    row("Мін. COP",          b.min_cop,      "",     m.cop,         (cur, lim) => cur < lim) +
    row("Макс. темп. подачі",b.max_flow_c,   "°C",   m.flow_temp_c, (cur, lim) => cur > lim) +
    row("Мін. темп. подачі", b.min_flow_c,   "°C",   m.flow_temp_c, (cur, lim) => cur < lim);
}

const SPEC_LABELS = {
  manufacturer:    "Виробник",
  model:           "Модель",
  modelSeries:     "Серія",
  modelVariant:    "Варіант",
  nominalPowerKw:  "Номінальна потужність, кВт",
  maxPowerKw:      "Макс. потужність, кВт",
  nominalCOP:      "Номінальний COP",
  minCOP:          "Мін. COP",
  maxFlowTempC:    "Макс. темп. подачі, °C",
  minFlowTempC:    "Мін. темп. подачі, °C",
  refrigerant:     "Холодоагент",
  weightKg:        "Маса, кг",
  powerSupplyV:    "Напруга живлення, В",
  tankVolumeL:     "Об'єм бака ГВП, л",
  label:           "Опис",
};

function renderSpecs(specs) {
  const root = document.getElementById("specs-list");
  if (!specs || !Object.keys(specs).length) {
    root.innerHTML = `<div style="grid-column:1/-1;color:var(--text-dim);">Специфікація відсутня.</div>`;
    return;
  }
  const ordered = Object.keys(SPEC_LABELS).filter(k => specs[k] != null);
  const extras  = Object.keys(specs).filter(k => !(k in SPEC_LABELS));
  root.innerHTML = [...ordered, ...extras].map(k =>
    `<div class="k">${esc(SPEC_LABELS[k] || k)}</div><div class="v">${esc(specs[k])}</div>`
  ).join("");
}

function renderTimeline(alerts) {
  const root = document.getElementById("alerts-timeline");
  if (!alerts.length) {
    root.innerHTML = `<div style="padding:14px;color:var(--text-dim);">Жодної тривоги для цього пристрою.</div>`;
    return;
  }
  root.innerHTML = alerts.map(a => `
    <div class="timeline-item">
      <div class="when">${fmtTime(a.raised_at)}</div>
      <div class="what">
        <span style="color:${severityColor(a.severity)};font-weight:600;">${severityText(a.severity)}</span>
<span style="color:var(--normal);font-size:11px;"> · ${statusText(a.status)}</span>
<div class="codes">${a.anomaly_codes.map(code => esc(anomalyCodeText(code))).join('  ·  ')}</div>
        <div style="font-size:11px;color:var(--text-dim);">${esc(a.explanation || '')}</div>
      </div>
    </div>
  `).join("");
}

initCharts();
seedHistory().then(refresh);
setInterval(refresh, 5000);
