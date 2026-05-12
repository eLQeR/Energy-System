// Спільні утиліти для всіх сторінок dashboard.

const API = "";  // same origin

async function api(path, opts = {}) {
  const resp = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("uk-UA", { hour12: false });
}

function fmtAge(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60)        return `${sec}с тому`;
  if (sec < 3600)      return `${Math.floor(sec/60)} хв тому`;
  if (sec < 86400)     return `${Math.floor(sec/3600)} год тому`;
  return `${Math.floor(sec/86400)} д тому`;
}

function severityColor(sev) {
  return { warning: "var(--warning)", anomaly: "var(--anomaly)" }[sev] || "var(--text-dim)";
}

function severityText(sev) {
  return {
    warning: "ПОПЕРЕДЖЕННЯ",
    anomaly: "АНОМАЛІЯ"
  }[sev] || "НЕВІДОМО";
}

function statusText(status) {
  return {
    active: "АКТИВНА",
    acknowledged: "ПІДТВЕРДЖЕНО",
    resolved: "УСУНЕНО"
  }[status] || "ВИРІШЕНО";
}

function stateText(state, stale = false) {
  if (stale) return "НЕМАЄ ОНОВЛЕНЬ";
  return {
    normal: "НОРМА",
    warning: "ПОПЕРЕДЖЕННЯ",
    anomaly: "АНОМАЛІЯ",
    unknown: "НЕВІДОМО"
  }[state] || "НЕВІДОМО";
}

function anomalyCodeText(code) {
  const text = String(code ?? "");

  if (text.startsWith("ml_outlier")) {
    return "виявлено відхилення у роботі системи";
  }

  if (text.startsWith("cop_below_nominal")) {
    return "COP нижче норми";
  }

  if (text.startsWith("power_over_limit")) {
    return "перевищено потужність";
  }

  if (text.startsWith("flow_temp_over_limit")) {
    return "температура подачі вище норми";
  }

  if (text.startsWith("flow_temp_under_limit")) {
    return "температура подачі нижче норми";
  }

  return text;
}

function toast(text, kind = "info") {
  const area = document.getElementById("toast-area");
  if (!area) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  area.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function setLive(connected) {
  const dot   = document.getElementById("live-dot");
  const text  = document.getElementById("live-status");
  if (!dot) return;
  dot.classList.toggle("disconnected", !connected);
  text.textContent = connected ? "оновлення кожні 5 с" : "немає з'єднання";
}

setInterval(() => {
  const c = document.getElementById("now-clock");
  if (c) c.textContent = new Date().toLocaleTimeString("uk-UA", { hour12: false });
}, 1000);

(() => {
  const path = location.pathname;
  let key = "dashboard";
  if (path.startsWith("/ontology")) key = "ontology";
  else if (path.startsWith("/upload")) key = "upload";
  else if (path.startsWith("/device")) key = "dashboard";
  document.querySelectorAll(".topnav a[data-nav]").forEach((a) => {
    if (a.dataset.nav === key) a.classList.add("active");
  });
})();

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[m]);
}

// ──────────────────────────────────────────────────────────────────────
// Рендер діагнозів з онтології (вкладається в кожну картку тривоги).
// Використовується dashboard.js (renderActiveAlerts, renderHistory) і
// device_detail.js (renderTimeline). Тримаємо тут щоб не дублювати.
// ──────────────────────────────────────────────────────────────────────

const SEVERITY_COLOR = {
  info:     "#79c0ff",
  low:      "#3fb950",
  medium:   "#d29922",
  high:     "#f85149",
  critical: "#ff4f4f",
};

const SEVERITY_LABEL_UK = {
  info:     "інформ.",
  low:      "низька",
  medium:   "середня",
  high:     "висока",
  critical: "критична",
};

function severityBadge(sev) {
  if (!sev) return "";
  const color = SEVERITY_COLOR[sev] || "#8b949e";
  const text  = SEVERITY_LABEL_UK[sev] || sev;
  return `<span style="display:inline-block;padding:1px 8px;border-radius:10px;
          font-size:10px;background:${color}22;color:${color};
          border:1px solid ${color}66;text-transform:uppercase;
          letter-spacing:.5px;margin-left:8px;">${text}</span>`;
}

function renderDiagnoses(diagnoses, opts = {}) {
  if (!diagnoses || !diagnoses.length) return "";
  const compact = !!opts.compact;
  const limit   = opts.limit || (compact ? 3 : 6);
  const shown   = diagnoses.slice(0, limit);
  const hidden  = diagnoses.length - shown.length;

  const items = shown.map(d => {
    const sevColor = SEVERITY_COLOR[d.severity] || "#8b949e";
    const padding  = compact ? "4px 8px" : "6px 8px";
    const fontSize = compact ? "11px"    : "12px";
    if (d.kind === "error_code") {
      return `
        <div style="display:flex;gap:8px;align-items:flex-start;padding:${padding};
                    background:var(--bg-elev);border-radius:4px;
                    border-left:3px solid ${sevColor};margin-top:4px;">
          <code style="color:${sevColor};font-weight:600;min-width:48px;
                       font-size:${fontSize};">${esc(d.error_code || "?")}</code>
          <div style="font-size:${fontSize};line-height:1.4;flex:1;min-width:0;">
            <div style="color:var(--text);">${esc(d.error_description || "")}</div>
            ${compact ? "" : `<div style="color:var(--text-muted);margin-top:2px;">→ ${esc(d.error_action || "")}</div>`}
          </div>
        </div>`;
    }
    if (d.kind === "fault") {
      return `
        <div style="padding:${padding};background:var(--bg-elev);border-radius:4px;
                    border-left:3px solid ${sevColor};margin-top:4px;
                    font-size:${fontSize};line-height:1.4;">
          <div style="color:var(--text);">
            <strong>Можлива причина:</strong> ${esc(d.cause || "")}
          </div>
          ${compact ? "" : `<div style="color:var(--text-muted);margin-top:2px;">→ ${esc(d.solution || "")}</div>`}
          ${(!compact && d.affects_component) ? `<div style="color:var(--text-dim);margin-top:2px;font-size:11px;">Компонент: <code>${esc(d.affects_component)}</code></div>` : ""}
        </div>`;
    }
    return `
      <div style="padding:${padding};background:var(--bg-elev);border-radius:4px;
                  border-left:3px solid #8b949e;margin-top:4px;
                  font-size:${fontSize};color:var(--text-muted);font-style:italic;">
        💡 ${esc(d.hint || "")}
      </div>`;
  }).join("");

  const moreHint = hidden > 0
    ? `<div style="font-size:10px;color:var(--text-dim);margin-top:4px;">
         + ще ${hidden} ${hidden === 1 ? "діагноз" : "діагнозів"} →
         <a href="/device/${esc(opts.deviceId || "")}" style="color:var(--text-dim);">деталі</a>
       </div>`
    : "";

  return `<div style="margin-top:6px;">
    <div style="font-size:10px;color:var(--text-dim);text-transform:uppercase;
                letter-spacing:.5px;margin-bottom:2px;">Діагноз з онтології</div>
    ${items}
    ${moreHint}
  </div>`;
}
