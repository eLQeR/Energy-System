from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import (
    AlertPayload,
    Diagnosis,
    MetricsMessage,
    StateMessage,
    TOPIC_METRICS_WILDCARD,
    TOPIC_STATE,
    utcnow_iso,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose import DiagnosticEngine  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("analyzer")

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
ONTOLOGY_API = os.getenv("ONTOLOGY_API", "http://localhost:5000")
ALERTS_API   = os.getenv("ALERTS_API",   "http://localhost:5003")

HERE = Path(__file__).parent

# --- ML-моделі: автоенкодер (новий) має пріоритет, fallback на IsolationForest.
# Порядок пошуку:
#   1. явний шлях у env AE_NPZ (для override)
#   2. anomaly_ae_strict.npz  ← стрімкіший поріг, менше false-positives
#   3. anomaly_ae.npz         ← дефолтний (99-й перцентиль)
AE_FEATURES = ["power_kw", "delta_t_c", "flow_temp_c", "outdoor_temp_c", "cop"]


def _resolve_ae_path() -> Path | None:
    override = os.getenv("AE_NPZ")
    if override:
        p = Path(override)
        return p if p.exists() else None
    for name in ("anomaly_ae_strict.npz", "anomaly_ae.npz"):
        p = HERE / name
        if p.exists():
            return p
    return None


AE_NPZ = _resolve_ae_path()

class _NumpyAE:
    """Forward pass автоенкодера на голому numpy (той самий код, що й
    pi_zero_analyzer — щоб демонструвати: модель ОДНА, рантайми різні)."""

    def __init__(self, npz_path: Path):
        data = np.load(npz_path)
        self.mean      = data["mean"].astype(np.float32)
        self.std       = data["std"].astype(np.float32)
        self.threshold = float(data["threshold"])
        layer_keys = sorted(k for k in data.files
                             if k.endswith("_W") and not k.startswith("__"))
        self.layers = [(data[k].astype(np.float32),
                        data[k[:-2] + "_b"].astype(np.float32))
                       for k in layer_keys]

    def _vec(self, m: dict) -> np.ndarray:
        flow = m.get("flow_temp_c") or 0.0
        ret  = m.get("return_temp_c") or 0.0
        cop  = m.get("cop") if m.get("cop") is not None else 3.0
        values = {
            "power_kw":       m.get("power_kw") or 0.0,
            "delta_t_c":      flow - ret,
            "flow_temp_c":    flow,
            "outdoor_temp_c": m.get("outdoor_temp_c") or 0.0,
            "cop":            cop,
        }
        return np.array([values[f] for f in AE_FEATURES], dtype=np.float32)

    def predict(self, m: dict) -> dict:
        """→ {is_anomaly, mse, threshold, predicted{}, score_ratio}.
        predicted — у фізичних одиницях (denormalized)."""
        x_raw = self._vec(m)
        x = ((x_raw - self.mean) / self.std).reshape(1, -1)
        h = x
        for i, (W, b) in enumerate(self.layers):
            h = h @ W + b
            if i < len(self.layers) - 1:
                np.maximum(h, 0.0, out=h)
        recon_norm = h.reshape(-1)
        mse = float(np.mean((x.reshape(-1) - recon_norm) ** 2))
        recon = recon_norm * self.std + self.mean
        predicted = {f: float(recon[i]) for i, f in enumerate(AE_FEATURES)}
        # Збудуємо predicted_return для зручності UI
        predicted["return_temp_c"] = predicted["flow_temp_c"] - predicted["delta_t_c"]
        return {
            "is_anomaly":  mse > self.threshold,
            "mse":         mse,
            "threshold":   self.threshold,
            "predicted":   predicted,
            "score_ratio": mse / max(self.threshold, 1e-9),
        }


AE: _NumpyAE | None = None
MODEL = None
FEATURES = AE_FEATURES

if AE_NPZ is not None:
    AE = _NumpyAE(AE_NPZ)
    log.info("Loaded autoencoder %s (%d layers, threshold=%.4f)",
             AE_NPZ.name, len(AE.layers), AE.threshold)
else:
    bundle = joblib.load(HERE / "anomaly_model.pkl")
    MODEL = bundle["model"]
    FEATURES = bundle["features"]
    log.info("Autoencoder not found — falling back to IsolationForest")

@dataclass
class DeviceStatus:
    device_id: str
    last_metrics: dict = field(default_factory=dict)
    last_state: dict = field(default_factory=dict)
    updated_at: str = ""


STATE_CACHE: dict[str, DeviceStatus] = {}
STATE_LOCK = threading.Lock()
BOUNDS_CACHE: dict[str, dict] = {}
BOUNDS_TTL_SEC = 300
BOUNDS_FETCHED: dict[str, float] = {}
LAST_STATE: dict[str, str] = {}
LAST_STATE_LOCK = threading.Lock()

# Двигун збагачення анома­лій причинами та рішеннями з онтології.
# Кеш контексту синхронізований з BOUNDS_TTL_SEC.
DIAGNOSER = DiagnosticEngine(
    ontology_api_url=ONTOLOGY_API,
    cache_ttl_sec=BOUNDS_TTL_SEC,
)


def get_bounds(device_id: str) -> dict:
    now = time.time()
    if device_id in BOUNDS_CACHE and now - BOUNDS_FETCHED.get(device_id, 0) < BOUNDS_TTL_SEC:
        return BOUNDS_CACHE[device_id]
    try:
        r = requests.get(f"{ONTOLOGY_API}/device/{device_id}/expected-bounds", timeout=3)
        r.raise_for_status()
        BOUNDS_CACHE[device_id] = r.json()
        BOUNDS_FETCHED[device_id] = now
    except Exception as exc:
        log.warning("Ontology API unavailable for %s (%s) — fallback bounds", device_id, exc)
        BOUNDS_CACHE[device_id] = {}
        BOUNDS_FETCHED[device_id] = now
    return BOUNDS_CACHE[device_id]

LAST_REMIND: dict[str, float] = {}
REMIND_INTERVAL_SEC = int(os.getenv("REMIND_INTERVAL_SEC", "900"))


def _should_remind(device_id: str) -> bool:
    now = time.time()
    if now - LAST_REMIND.get(device_id, 0) >= REMIND_INTERVAL_SEC:
        LAST_REMIND[device_id] = now
        return True
    return False


def send_heartbeat(state: StateMessage, metrics_dump: dict, bounds: dict,
                    prediction: dict | None = None) -> None:
    """Оновлює last_seen + актуальні метрики + ML-прогноз у alerts_server."""
    body = {
        "device_id": state.device_id,
        "timestamp": state.timestamp,
        "state":     state.state,
        "metrics":   metrics_dump,
        "bounds":    bounds,
    }
    if prediction is not None:
        body["prediction"] = prediction
    try:
        requests.post(f"{ALERTS_API}/api/heartbeat", json=body, timeout=2)
    except requests.RequestException as exc:
        log.debug("heartbeat dropped for %s (%s)", state.device_id, exc)


def forward_alert(state: StateMessage, metrics_dump: dict, bounds: dict) -> None:
    if state.state not in ("warning", "anomaly"):
        return
    payload = AlertPayload(
        device_id=state.device_id,
        timestamp=state.timestamp,
        severity=state.state,
        anomaly_codes=state.anomalies,
        explanation=state.explanation,
        confidence=state.confidence,
        metrics_snapshot=metrics_dump,
        bounds_snapshot=bounds,
        diagnoses=state.diagnoses,
    )
    try:
        requests.post(
            f"{ALERTS_API}/api/alerts",
            data=payload.model_dump_json(),
            headers={"Content-Type": "application/json"},
            timeout=3,
        ).raise_for_status()
    except requests.RequestException as exc:
        log.warning("alerts_server unavailable (%s) — alert dropped (device=%s)",
                    exc, state.device_id)


def rule_based_checks(metrics: dict, bounds: dict) -> list[str]:
    anomalies = []
    if bounds.get("min_cop") is not None and metrics.get("cop") is not None:
        if metrics["cop"] < bounds["min_cop"]:
            anomalies.append(f"cop_below_nominal({metrics['cop']:.2f}<{bounds['min_cop']:.2f})")
    if bounds.get("max_power_kw") is not None and metrics["power_kw"] > bounds["max_power_kw"]:
        anomalies.append(
            f"power_over_limit({metrics['power_kw']:.2f}>{bounds['max_power_kw']:.2f})"
        )
    if bounds.get("max_flow_c") is not None and metrics["flow_temp_c"] > bounds["max_flow_c"]:
        anomalies.append(
            f"flow_temp_over_limit({metrics['flow_temp_c']:.1f}>{bounds['max_flow_c']:.1f})"
        )
    return anomalies


def analyze(msg: MetricsMessage) -> tuple[StateMessage, dict | None]:
    """→ (StateMessage, prediction_dict | None). Prediction містить
    `mse`, `threshold`, `predicted` (reconstructed values in physical units)
    і `score_ratio`. None коли AE недоступний (fallback на IsolationForest)."""
    m = msg.metrics
    metrics_dump = m.model_dump()

    bounds = get_bounds(msg.device_id)
    rule_anomalies = rule_based_checks(metrics_dump, bounds)

    ml_outlier = False
    score = 0.0
    prediction: dict | None = None
    explanation_ml = "ML disabled"

    if AE is not None:
        prediction = AE.predict(metrics_dump)
        ml_outlier = prediction["is_anomaly"]
        score = prediction["score_ratio"]
        explanation_ml = (
            f"AE mse={prediction['mse']:.4f} "
            f"ratio={prediction['score_ratio']:.2f}×"
        )
    elif MODEL is not None:
        feat = np.array([[
            m.power_kw,
            m.flow_temp_c - m.return_temp_c,
            m.flow_temp_c,
            m.outdoor_temp_c if m.outdoor_temp_c is not None else 0.0,
            m.cop if m.cop is not None else 3.5,
        ]])
        ml_outlier = int(MODEL.predict(feat)[0]) == -1
        score = abs(float(MODEL.decision_function(feat)[0])) / 0.5
        explanation_ml = f"IF score={score:.3f}"

    # Семантика тривог:
    #   rule_anomalies (вже сталось порушення меж онтології) → anomaly (червоне)
    #   ml_outlier      (модель передбачає дрейф)            → warning (жовте)
    #   обидва                                              → anomaly з міткою ML
    if rule_anomalies:
        anomalies = rule_anomalies + (["ml_outlier"] if ml_outlier else [])
        state = "anomaly"
    elif ml_outlier:
        anomalies = ["ml_outlier"]
        state = "warning"
    else:
        anomalies = []
        state = "normal"

    # Збагачуємо причиною/рішенням з онтології тільки якщо щось пішло не так.
    diagnoses: list[Diagnosis] = (
        DIAGNOSER.enrich(anomalies, msg.device_id) if state != "normal" else []
    )

    state_msg = StateMessage(
        device_id=msg.device_id,
        timestamp=utcnow_iso(),
        state=state,
        anomalies=anomalies,
        confidence=min(1.0, abs(score)),
        explanation=f"{explanation_ml}; rules matched: {len(rule_anomalies)}",
        diagnoses=diagnoses,
    )
    return state_msg, prediction

def on_message(client: mqtt.Client, _userdata, mqtt_msg: mqtt.MQTTMessage) -> None:
    try:
        incoming = MetricsMessage.model_validate_json(mqtt_msg.payload)
    except Exception:
        log.exception("Bad metrics payload on %s", mqtt_msg.topic)
        return

    state, prediction = analyze(incoming)
    out_topic = TOPIC_STATE.format(device_id=state.device_id)
    client.publish(out_topic, state.model_dump_json(), qos=0)

    bounds = get_bounds(state.device_id)
    metrics_dump = incoming.metrics.model_dump()
    send_heartbeat(state, metrics_dump, bounds, prediction)

    with STATE_LOCK:
        status = STATE_CACHE.setdefault(state.device_id, DeviceStatus(state.device_id))
        status.last_metrics = metrics_dump
        status.last_state = state.model_dump()
        status.updated_at = state.timestamp

    with LAST_STATE_LOCK:
        prev = LAST_STATE.get(state.device_id, "normal")
        LAST_STATE[state.device_id] = state.state

    state_changed = prev != state.state
    is_problem    = state.state in ("warning", "anomaly")

    should_forward = is_problem and (state_changed or _should_remind(state.device_id))
    if should_forward:
        forward_alert(state, metrics_dump, bounds)

    log.info(
        "%s → %s (conf=%.2f) anomalies=%s%s",
        state.device_id, state.state, state.confidence, state.anomalies,
        "  → ALERT FORWARDED" if should_forward else "",
    )
    for diag in state.diagnoses:
        log.info("  · %s", DIAGNOSER.format_for_log(diag))


def on_connect(client, *_):
    log.info("MQTT connected — subscribing %s", TOPIC_METRICS_WILDCARD)
    client.subscribe(TOPIC_METRICS_WILDCARD, qos=0)


def start(block: bool = True) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edge-analyzer")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    if block:
        client.loop_forever()
    else:
        client.loop_start()
    return client


if __name__ == "__main__":
    start()
