"""Edge-аналізатор для Raspberry Pi Zero V1.3 (ARMv6, без NEON, 512MB RAM).

Чому окремий файл, а не tf_analyzer.py:
* На ARMv6 офіційного `tflite-runtime` нема — лише ARMv7+ (Pi Zero 2 / Pi 3+).
* Pi Zero V1.3 — це single-core ARM11 @ 1GHz, дуже бідно.
* Tensorflow тут не запустимо, але автоенкодер у нас 5→4→3→4→5 — це 5 dense
  layers, ~50 параметрів. Numpy виконає це за мікросекунди.

Тому:
  1. На dev-машині train_tf_model.py зберігає ваги у anomaly_ae.npz.
  2. Сюди копіюємо лише .npz (~5 KB) + цей файл.
  3. Залежності — голий numpy + paho-mqtt + requests + pydantic. Усі мають
     pre-built ARMv6 wheels на piwheels.org (Raspbian має це за замовчуванням).

Запуск:
    pip install -r analyzer/requirements-pi-zero.txt
    python analyzer/pi_zero_analyzer.py

Якщо anomaly_ae.npz відсутній — деградує до rule-based-only.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import (
    AlertPayload,
    MetricsMessage,
    StateMessage,
    TOPIC_METRICS_WILDCARD,
    TOPIC_STATE,
    utcnow_iso,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pi_zero_analyzer")

MQTT_BROKER         = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT           = int(os.getenv("MQTT_PORT", "1883"))
ONTOLOGY_API        = os.getenv("ONTOLOGY_API",  "http://localhost:5000")
ALERTS_API          = os.getenv("ALERTS_API",    "http://localhost:5003")
REMIND_INTERVAL_SEC = int(os.getenv("REMIND_INTERVAL_SEC", "900"))
BOUNDS_TTL_SEC      = int(os.getenv("BOUNDS_TTL_SEC",      "300"))

HERE = Path(__file__).parent
NPZ_PATH = Path(os.getenv("AE_WEIGHTS", str(HERE / "anomaly_ae.npz")))

# Список фіч у тому ж порядку, в якому тренувалась мережа.
# Якщо в train_tf_model.py FEATURES змінюється — оновити і тут.
FEATURES = ["power_kw", "delta_t_c", "flow_temp_c", "outdoor_temp_c", "cop"]


# ─── Numpy autoencoder ────────────────────────────────────────────────────────

class NumpyAutoencoder:
    """Forward pass автоенкодера на голому numpy.

    Архітектура (з train_tf_model.py):
        Dense(4, relu) → Dense(3, relu) → Dense(4, relu) → Dense(5, linear)

    Ваги завантажуються з .npz, який експортує train_tf_model.py:
        dense_*_W, dense_*_b, mean, std, threshold
    """

    def __init__(self, npz_path: Path):
        data = np.load(npz_path)
        self.mean      = data["mean"].astype(np.float32)
        self.std       = data["std"].astype(np.float32)
        self.threshold = float(data["threshold"])

        # Шари збережено з ключами layer00_W, layer01_W, … — sorted дає
        # правильну послідовність encoder → bottleneck → decoder.
        weight_keys = sorted(k for k in data.files
                              if k.endswith("_W") and not k.startswith("__"))
        self.layers: list[tuple[np.ndarray, np.ndarray]] = []
        for w_key in weight_keys:
            b_key = w_key[:-2] + "_b"
            W = data[w_key].astype(np.float32)
            b = data[b_key].astype(np.float32)
            self.layers.append((W, b))
        log.info("Loaded autoencoder: %d layers, threshold MSE=%.6f",
                 len(self.layers), self.threshold)

    def _forward(self, x: np.ndarray) -> np.ndarray:
        # Усі шари окрім останнього — ReLU. Останній — linear (reconstruction).
        for i, (W, b) in enumerate(self.layers):
            x = x @ W + b
            if i < len(self.layers) - 1:
                np.maximum(x, 0.0, out=x)  # in-place relu
        return x

    def _vectorize(self, metrics: dict) -> np.ndarray:
        flow = metrics.get("flow_temp_c", 0.0) or 0.0
        ret  = metrics.get("return_temp_c", 0.0) or 0.0
        values = {
            "power_kw":       metrics.get("power_kw", 0.0) or 0.0,
            "delta_t_c":      flow - ret,
            "flow_temp_c":    flow,
            "outdoor_temp_c": metrics.get("outdoor_temp_c", 0.0) or 0.0,
            "cop":            metrics.get("cop", 3.0) if metrics.get("cop") is not None else 3.0,
        }
        vec = np.array([values[f] for f in FEATURES], dtype=np.float32)
        return (vec - self.mean) / self.std

    def score(self, metrics: dict) -> tuple[bool, float, float]:
        """→ (is_anomaly, mse, mse_ratio_to_threshold)."""
        x = self._vectorize(metrics).reshape(1, -1)
        recon = self._forward(x.copy())
        mse = float(np.mean((x - recon) ** 2))
        return mse > self.threshold, mse, mse / max(self.threshold, 1e-9)


def _try_load_detector() -> NumpyAutoencoder | None:
    if not NPZ_PATH.exists():
        log.warning(
            "Ваги моделі не знайдено (%s) — fallback до rule-based-only. "
            "Натренуйте на dev-машині: python analyzer/train_tf_model.py",
            NPZ_PATH,
        )
        return None
    try:
        return NumpyAutoencoder(NPZ_PATH)
    except Exception:
        log.exception("Не вдалось завантажити ваги — fallback до rule-based")
        return None


DETECTOR: NumpyAutoencoder | None = _try_load_detector()


# ─── Bounds cache + rule-based checks ─────────────────────────────────────────

BOUNDS_CACHE:   dict[str, dict]  = {}
BOUNDS_FETCHED: dict[str, float] = {}
LAST_STATE:     dict[str, str]   = {}
LAST_REMIND:    dict[str, float] = {}


def get_bounds(device_id: str) -> dict:
    now = time.time()
    if device_id in BOUNDS_CACHE and now - BOUNDS_FETCHED.get(device_id, 0) < BOUNDS_TTL_SEC:
        return BOUNDS_CACHE[device_id]
    try:
        r = requests.get(f"{ONTOLOGY_API}/device/{device_id}/expected-bounds", timeout=3)
        r.raise_for_status()
        BOUNDS_CACHE[device_id] = r.json()
    except requests.RequestException as exc:
        log.warning("Ontology API unreachable for %s (%s)", device_id, exc)
        BOUNDS_CACHE[device_id] = BOUNDS_CACHE.get(device_id, {})
    BOUNDS_FETCHED[device_id] = now
    return BOUNDS_CACHE[device_id]


def rule_based_checks(metrics: dict, bounds: dict) -> list[str]:
    anomalies: list[str] = []
    cop = metrics.get("cop")
    power = metrics.get("power_kw", 0.0)
    flow = metrics.get("flow_temp_c", 0.0)

    if bounds.get("min_cop") is not None and cop is not None and cop < bounds["min_cop"]:
        anomalies.append(f"cop_below_nominal({cop:.2f}<{bounds['min_cop']:.2f})")
    if bounds.get("max_power_kw") is not None and power > bounds["max_power_kw"]:
        anomalies.append(f"power_over_limit({power:.2f}>{bounds['max_power_kw']:.2f})")
    if bounds.get("max_flow_c") is not None and flow > bounds["max_flow_c"]:
        anomalies.append(f"flow_temp_over_limit({flow:.1f}>{bounds['max_flow_c']:.1f})")
    if bounds.get("min_flow_c") is not None and flow < bounds["min_flow_c"]:
        anomalies.append(f"flow_temp_under_limit({flow:.1f}<{bounds['min_flow_c']:.1f})")
    return anomalies


def classify(rule_anomalies: list[str], ml_anomaly: bool) -> tuple[str, list[str]]:
    codes = list(rule_anomalies)
    if ml_anomaly:
        codes.insert(0, "ml_outlier")
    if not codes:
        return "normal", []
    for a in rule_anomalies:
        if a.startswith(("power_over_limit", "flow_temp_over_limit")):
            return "anomaly", codes
    if ml_anomaly and rule_anomalies:
        return "anomaly", codes
    return "warning", codes


def analyze(msg: MetricsMessage) -> StateMessage:
    metrics = msg.metrics.model_dump()
    bounds  = get_bounds(msg.device_id)
    rule_anomalies = rule_based_checks(metrics, bounds)

    ml_anomaly = False
    ml_explanation = "ML detector disabled"
    confidence = 1.0
    if DETECTOR is not None:
        ml_anomaly, mse, ratio = DETECTOR.score(metrics)
        ml_explanation = f"ML mse={mse:.4f} ratio={ratio:.2f}× threshold"
        confidence = float(np.clip(0.5 + (ratio - 1.0) * 0.25, 0.0, 1.0))

    state, anomalies = classify(rule_anomalies, ml_anomaly)
    return StateMessage(
        device_id=msg.device_id,
        timestamp=utcnow_iso(),
        state=state,
        anomalies=anomalies,
        confidence=confidence if state != "normal" else 1.0,
        explanation=f"{ml_explanation}; rules matched: {len(rule_anomalies)}",
    )


# ─── MQTT + alerts forwarding ─────────────────────────────────────────────────

def _should_remind(device_id: str) -> bool:
    now = time.time()
    if now - LAST_REMIND.get(device_id, 0) >= REMIND_INTERVAL_SEC:
        LAST_REMIND[device_id] = now
        return True
    return False


def send_heartbeat(state: StateMessage, metrics_dump: dict, bounds: dict) -> None:
    try:
        requests.post(
            f"{ALERTS_API}/api/heartbeat",
            json={
                "device_id": state.device_id,
                "timestamp": state.timestamp,
                "state":     state.state,
                "metrics":   metrics_dump,
                "bounds":    bounds,
            },
            timeout=2,
        )
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
    )
    try:
        requests.post(
            f"{ALERTS_API}/api/alerts",
            data=payload.model_dump_json(),
            headers={"Content-Type": "application/json"},
            timeout=3,
        ).raise_for_status()
    except requests.RequestException as exc:
        log.warning("alerts_server unreachable (%s) — alert dropped (device=%s)",
                    exc, state.device_id)


def on_message(client: mqtt.Client, _userdata, mqtt_msg: mqtt.MQTTMessage) -> None:
    try:
        incoming = MetricsMessage.model_validate_json(mqtt_msg.payload)
    except Exception:
        log.exception("Bad metrics payload on %s", mqtt_msg.topic)
        return

    state = analyze(incoming)
    out_topic = TOPIC_STATE.format(device_id=state.device_id)
    client.publish(out_topic, state.model_dump_json(), qos=0)

    bounds = get_bounds(state.device_id)
    metrics_dump = incoming.metrics.model_dump()
    send_heartbeat(state, metrics_dump, bounds)

    prev = LAST_STATE.get(state.device_id, "normal")
    LAST_STATE[state.device_id] = state.state
    state_changed = prev != state.state
    is_problem = state.state in ("warning", "anomaly")

    should_forward = is_problem and (state_changed or _should_remind(state.device_id))
    if should_forward:
        forward_alert(state, metrics_dump, bounds)

    log.info("%s → %s (conf=%.2f) anomalies=%s%s",
             state.device_id, state.state, state.confidence, state.anomalies,
             "  → ALERT FORWARDED" if should_forward else "")


def on_connect(client, *_):
    log.info("MQTT connected - subscribing %s", TOPIC_METRICS_WILDCARD)
    client.subscribe(TOPIC_METRICS_WILDCARD, qos=0)


def main() -> None:
    mode = "ML+rule-based (numpy)" if DETECTOR is not None else "rule-based only (fallback)"
    log.info("Pi Zero analyzer starting [%s]  (broker=%s:%d, ontology=%s, alerts=%s)",
             mode, MQTT_BROKER, MQTT_PORT, ONTOLOGY_API, ALERTS_API)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-zero-analyzer")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
