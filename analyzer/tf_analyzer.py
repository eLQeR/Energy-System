"""Edge-аналізатор з ML-виявленням аномалій через TensorFlow Lite.

Альтернатива rule-only `edge_analyzer.py`: коли rule-based перевірки не
вистачає (наприклад, дрейф COP у межах онтології, але далеко від норми
конкретного пристрою), автоенкодер сигналізує про відхилення за
reconstruction MSE.

Вибір режиму:
    rule-based only:  python analyzer/edge_analyzer.py
    ML + rule-based:  python analyzer/tf_analyzer.py

Запуск на Raspberry Pi:
    pip install -r analyzer/requirements-tflite.txt
    # модель тренується на dev-машині (train_tf_model.py) і копіюється сюди
    python analyzer/tf_analyzer.py

Якщо anomaly_ae.tflite / .meta.json відсутні — аналізатор працює тільки
з rule-based перевірками (deg-graceful fallback).
"""
from __future__ import annotations

import json
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
log = logging.getLogger("tf_analyzer")

MQTT_BROKER         = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT           = int(os.getenv("MQTT_PORT", "1883"))
ONTOLOGY_API        = os.getenv("ONTOLOGY_API",  "http://localhost:5000")
ALERTS_API          = os.getenv("ALERTS_API",    "http://localhost:5003")
REMIND_INTERVAL_SEC = int(os.getenv("REMIND_INTERVAL_SEC", "900"))
BOUNDS_TTL_SEC      = int(os.getenv("BOUNDS_TTL_SEC",      "300"))

HERE = Path(__file__).parent

def _resolve_pair() -> tuple[Path, Path]:
    override_m = os.getenv("TF_MODEL")
    override_meta = os.getenv("TF_META")
    if override_m and override_meta:
        return Path(override_m), Path(override_meta)
    for name in ("anomaly_ae_strict", "anomaly_ae"):
        m = HERE / f"{name}.tflite"
        meta = HERE / f"{name}.meta.json"
        if m.exists() and meta.exists():
            return m, meta
    return HERE / "anomaly_ae.tflite", HERE / "anomaly_ae.meta.json"

MODEL_PATH, META_PATH = _resolve_pair()


# ─── TFLite loader (tflite-runtime on Pi, tensorflow.lite on dev) ─────────────

def _load_interpreter(model_path: Path):
    """Імпорт інтерпретера: tflite-runtime (Pi) має пріоритет, бо ~5MB
    замість 500MB повного TensorFlow."""
    try:
        from tflite_runtime.interpreter import Interpreter  # type: ignore
        log.info("Using tflite-runtime interpreter")
    except ImportError:
        try:
            from tensorflow.lite import Interpreter  # type: ignore
            log.info("tflite-runtime not found — using tensorflow.lite interpreter")
        except ImportError as e:
            raise RuntimeError(
                "Жоден з пакетів tflite-runtime / tensorflow не встановлено. "
                "Встановіть `pip install tflite-runtime` (Pi) або "
                "`pip install tensorflow` (dev)."
            ) from e
    interpreter = Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    return interpreter


class AutoencoderDetector:
    """Тонкий wrapper навколо TFLite-моделі: нормалізує вхід, рахує MSE
    і повертає (is_anomaly, score)."""

    def __init__(self, model_path: Path, meta_path: Path):
        meta = json.loads(meta_path.read_text())
        self.features: list[str] = meta["features"]
        self.mean = np.array(meta["mean"], dtype=np.float32)
        self.std  = np.array(meta["std"],  dtype=np.float32)
        self.threshold: float = float(meta["mse_threshold"])

        self.interpreter = _load_interpreter(model_path)
        self.input_details  = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        log.info("Loaded autoencoder: %d features, threshold MSE=%.6f",
                 len(self.features), self.threshold)

    def _vectorize(self, metrics: dict) -> np.ndarray:
        # Те саме перетворення, що й у generate_synthetic.py + train_tf_model.py.
        flow = metrics.get("flow_temp_c", 0.0) or 0.0
        ret  = metrics.get("return_temp_c", 0.0) or 0.0
        values = {
            "power_kw":       metrics.get("power_kw", 0.0) or 0.0,
            "delta_t_c":      flow - ret,
            "flow_temp_c":    flow,
            "outdoor_temp_c": metrics.get("outdoor_temp_c", 0.0) or 0.0,
            "cop":            metrics.get("cop", 3.5) if metrics.get("cop") is not None else 3.5,
        }
        vec = np.array([values[f] for f in self.features], dtype=np.float32)
        return (vec - self.mean) / self.std

    def score(self, metrics: dict) -> tuple[bool, float, float]:
        """→ (is_anomaly, mse, ratio_to_threshold)."""
        x = self._vectorize(metrics).reshape(1, -1).astype(np.float32)
        self.interpreter.set_tensor(self.input_details[0]["index"], x)
        self.interpreter.invoke()
        recon = self.interpreter.get_tensor(self.output_details[0]["index"])
        mse = float(np.mean((x - recon) ** 2))
        return mse > self.threshold, mse, mse / max(self.threshold, 1e-9)


def _try_load_detector() -> AutoencoderDetector | None:
    if not MODEL_PATH.exists() or not META_PATH.exists():
        log.warning(
            "TFLite-модель не знайдена (%s / %s) — fallback на rule-based-only. "
            "Натренуйте: python analyzer/train_tf_model.py",
            MODEL_PATH.name, META_PATH.name,
        )
        return None
    try:
        return AutoencoderDetector(MODEL_PATH, META_PATH)
    except Exception:
        log.exception("Не вдалось завантажити TFLite-модель — fallback на rule-based")
        return None


DETECTOR: AutoencoderDetector | None = _try_load_detector()


# ─── Bounds cache + rule-based checks (ті самі, що в edge_analyzer.py) ────────

BOUNDS_CACHE: dict[str, dict]  = {}
BOUNDS_FETCHED: dict[str, float] = {}
LAST_STATE:  dict[str, str]   = {}
LAST_REMIND: dict[str, float] = {}


def get_bounds(device_id: str) -> dict:
    now = time.time()
    if device_id in BOUNDS_CACHE and now - BOUNDS_FETCHED.get(device_id, 0) < BOUNDS_TTL_SEC:
        return BOUNDS_CACHE[device_id]
    try:
        r = requests.get(f"{ONTOLOGY_API}/device/{device_id}/expected-bounds", timeout=3)
        r.raise_for_status()
        BOUNDS_CACHE[device_id]   = r.json()
    except requests.RequestException as exc:
        log.warning("Ontology API unreachable for %s (%s)", device_id, exc)
        BOUNDS_CACHE[device_id]   = BOUNDS_CACHE.get(device_id, {})
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
    """Семантика:
        rule_anomalies (вже за межами онтології) → anomaly (червоне)
        ml_outlier     (модель прогнозує дрейф)  → warning (жовте)
        обидва                                  → anomaly з міткою ml_outlier
    """
    if rule_anomalies:
        codes = rule_anomalies + (["ml_outlier"] if ml_anomaly else [])
        return "anomaly", codes
    if ml_anomaly:
        return "warning", ["ml_outlier"]
    return "normal", []


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
        # Впевненість: 0.5 у точці порога, 1.0 при ratio≥3, лінійно між.
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


# ─── MQTT + alerts forwarding (ідентично edge_analyzer.py) ────────────────────

def _should_remind(device_id: str) -> bool:
    now = time.time()
    if now - LAST_REMIND.get(device_id, 0) >= REMIND_INTERVAL_SEC:
        LAST_REMIND[device_id] = now
        return True
    return False


def send_heartbeat(state: StateMessage, metrics_dump: dict, bounds: dict) -> None:
    """Оновлює last_seen у alerts_server на кожен метрик (UI-індикатор «онлайн»)."""
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
    mode = "ML+rule-based" if DETECTOR is not None else "rule-based only (fallback)"
    log.info("TF analyzer starting [%s]  (broker=%s:%d, ontology=%s, alerts=%s)",
             mode, MQTT_BROKER, MQTT_PORT, ONTOLOGY_API, ALERTS_API)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-tf-analyzer")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
