let lastAlertCount = 0;

async function refresh() {
  try {
    const [stats, devices, activeAlerts, history] = await Promise.all([
      api("/api/stats"),
      api("/api/devices"),
      api("/api/alerts?status=active&limit=50"),
      api("/api/alerts?limit=20"),
    ]);
    setLive(true);
    renderStats(stats);
    renderActiveAlerts(activeAlerts);
    renderDevices(devices);
    renderHistory(history);

    if (activeAlerts.length > lastAlertCount) {
      toast(`Нова тривога: ${activeAlerts[0].device_id} — ${severityText(activeAlerts[0].severity)}`,
            "danger");
    }
    lastAlertCount = activeAlerts.length;
  } catch (err) {
    setLive(false);
    console.error(err);
  }
}

function renderStats(s) {
  document.getElementById("stat-active").textContent   = s.active;
  document.getElementById("stat-ack").textContent      = s.acknowledged;
  document.getElementById("stat-resolved").textContent = s.resolved_24h;
  document.getElementById("stat-devices").textContent  = s.devices_total;

  const sub = [];
  if (s.by_severity?.anomaly) sub.push(`<span style="color:var(--anomaly)">${s.by_severity.anomaly} аномалій</span>`);
  if (s.by_severity?.warning) sub.push(`<span style="color:var(--warning)">${s.by_severity.warning} попереджень</span>`);
  document.getElementById("stat-active-sub").innerHTML = sub.join(" · ") || "потребують уваги";
}

function renderActiveAlerts(alerts) {
  const root = document.getElementById("active-alerts");
  document.getElementById("alerts-count").textContent = `${alerts.length} активних`;

  if (!alerts.length) {
    root.innerHTML = `<div class="empty">
      <div class="icon">✓</div>
      <div>Немає активних тривог. Усе обладнання працює в межах онтологічних специфікацій.</div>
    </div>`;
    return;
  }

  root.innerHTML = alerts.map(a => `
    <div class="alert ${a.severity}">
      <div class="severity">${severityText(a.severity)}</div>
      <div class="body">
        <div class="device">
          <a href="/device/${esc(a.device_id)}">${esc(a.device_id)}</a>
        </div>
        <div class="codes">${a.anomaly_codes.map(code => esc(anomalyCodeText(code))).join("  ·  ")}</div>
        <div class="meta">
  ${fmtAge(a.raised_at)}
</div>
      </div>
      <div class="actions">
        <button class="primary" onclick="ackAlert(${a.id})">Прийняти</button>
        <button class="success" onclick="resolveAlert(${a.id})">Закрити</button>
      </div>
    </div>
  `).join("");
}

function renderDevices(devices) {
  const root = document.getElementById("devices-grid");
  if (!devices.length) {
    root.innerHTML = `<div class="empty">
      <div class="icon">⏳</div>
      <div>Онтологічний API не повернув обладнання. Перевірте, чи запущено Fuseki.</div>
    </div>`;
    return;
  }
  root.innerHTML = devices.map(d => deviceCard(d)).join("");
}

function deviceCard(d) {
  const m = d.metrics || {};
  const b = d.bounds  || {};
  const stale = d.stale ? "stale" : (d.current_state || "unknown");

  const bar = (label, val, max, lowGood = false) => {
    if (val == null || max == null) return "";
    const pct = Math.min(100, Math.max(0, val / max * 100));
    const cls = pct >= 95 ? "bad" : pct >= 80 ? "warn" : "";
    const overflow = lowGood && val < max ? "bad" : "";  
    return `
      <div class="bound-row">
        <div>${esc(label)}</div>
        <div><span class="${cls === 'bad' || overflow ? 'v bad' : 'v'}">${val.toFixed(2)} / ${max.toFixed(2)}</span></div>
        <div class="bar"><div class="fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
  };

  const copBar = () => {
    if (m.cop == null || b.min_cop == null) return "";
    const ratio = m.cop / b.min_cop;
    const pct   = Math.min(100, ratio * 100);
    const cls   = m.cop < b.min_cop ? "bad" : (m.cop < b.min_cop * 1.1 ? "warn" : "");
    return `
      <div class="bound-row">
        <div>COP (мін)</div>
        <div><span class="${cls === 'bad' ? 'v bad' : 'v'}">${m.cop.toFixed(2)} / ≥${b.min_cop.toFixed(2)}</span></div>
        <div class="bar"><div class="fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
  };

  return `
    <div class="device-card" onclick="location.href='/device/${esc(d.id)}'">
      <div class="header">
        <div>
          <div class="title">${esc(d.label || d.id)}</div>
          <div class="model">${esc(d.id)}${d.model ? ' · ' + esc(d.model) : ''}</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;">
          ${d.active_alerts ? `<span class="alert-count">${d.active_alerts}</span>` : ''}<span class="state-badge ${stale}">
  ${d.stale ? '' : stateText(d.current_state || "unknown")}
</span>
        </div>
      </div>

      ${Object.keys(m).length ? `
        <div class="metrics-grid">
          <div class="k">Потужність</div>
          <div class="v">${(m.power_kw ?? 0).toFixed(2)} кВт</div>
          <div class="k">Подача</div>
          <div class="v">${(m.flow_temp_c ?? 0).toFixed(1)} °C</div>
          <div class="k">Зворотка</div>
          <div class="v">${(m.return_temp_c ?? 0).toFixed(1)} °C</div>
          <div class="k">COP</div>
          <div class="v">${m.cop != null ? m.cop.toFixed(2) : '—'}</div>
          <div class="k">Режим</div>
          <div class="v">${esc(m.mode || '—')}</div>
          <div class="k">Надворі</div>
          <div class="v">${m.outdoor_temp_c != null ? m.outdoor_temp_c.toFixed(1) + ' °C' : '—'}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px;">
          ${bar("Потужність", m.power_kw, b.max_power_kw)}
          ${bar("Темп. подачі", m.flow_temp_c, b.max_flow_c)}
          ${copBar()}
        </div>
      ` : `<div style="color:var(--text-dim);font-size:12px;">
              Жодних метрик ще не отримано. Запустіть analyzer + monitoring.
           </div>`}

      <div style="font-size:11px;color:var(--text-dim);margin-top:auto;">
        ${d.last_seen ? `Оновлено ${fmtAge(d.last_seen)}` : 'Ніколи'}
      </div>
    </div>`;
}

function renderHistory(alerts) {
  const root = document.getElementById("history-timeline");
  if (!alerts.length) {
    root.innerHTML = `<div style="padding:14px;color:var(--text-dim);">Журнал порожній.</div>`;
    return;
  }
  root.innerHTML = alerts.map(a => `
    <div class="timeline-item">
      <div class="when">${fmtTime(a.raised_at)}</div>
      <div class="what">
        <span style="color:${severityColor(a.severity)};font-weight:600;">${severityText(a.severity)}</span>
·
<a href="/device/${esc(a.device_id)}">${esc(a.device_id)}</a>
<span style="color:var(--normal);font-size:11px;"> · ${statusText(a.status)}</span>
<div class="codes">${a.anomaly_codes.map(code => esc(anomalyCodeText(code))).join('  ·  ')}</div>
      </div>
    </div>
  `).join("");
}

async function ackAlert(id) {
  try {
    await api(`/api/alerts/${id}/acknowledge`, { method: "POST", body: JSON.stringify({ user: "engineer" }) });
    toast(`Тривогу #${id} прийнято до уваги`, "success");
    refresh();
  } catch (e) { toast("Помилка: " + e.message, "danger"); }
}

async function resolveAlert(id) {
  try {
    await api(`/api/alerts/${id}/resolve`, { method: "POST" });
    toast(`Тривогу #${id} закрито`, "success");
    refresh();
  } catch (e) { toast("Помилка: " + e.message, "danger"); }
}

refresh();
setInterval(refresh, 5000);
