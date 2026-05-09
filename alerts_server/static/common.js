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

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[m]);
}
