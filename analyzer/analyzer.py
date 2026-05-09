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
    MetricsMessage,
    StateMessage,
    TOPIC_METRICS_WILDCARD,
    TOPIC_STATE,
    utcnow_iso,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("analyzer")

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
ONTOLOGY_API = os.getenv("ONTOLOGY_API", "http://localhost:5000")
ALERTS_API   = os.getenv("ALERTS_API",   "http://localhost:5003")

HERE = Path(__file__).parent
bundle = joblib.load(HERE / "anomaly_model.pkl")
MODEL = bundle["model"]
FEATURES = bundle["features"]

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
REMIND_INTERVAL_SEC = int(os.getenv("REMIND_INTERVAL_SEC", "300"))


def _should_remind(device_id: str) -> bool:
    now = time.time()
    if now - LAST_REMIND.get(device_id, 0) >= REMIND_INTERVAL_SEC:
        LAST_REMIND[device_id] = now
        return True
    return False


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


def analyze(msg: MetricsMessage) -> StateMessage:
    m = msg.metrics
    feat = np.array([[
        m.power_kw,
        m.flow_temp_c - m.return_temp_c,
        m.flow_temp_c,
        m.outdoor_temp_c if m.outdoor_temp_c is not None else 0.0,
        m.cop if m.cop is not None else 3.5,
    ]])
    prediction = int(MODEL.predict(feat)[0])     
    score = float(MODEL.decision_function(feat)[0])  

    bounds = get_bounds(msg.device_id)
    rule_anomalies = rule_based_checks(m.model_dump(), bounds)

    if prediction == -1 and rule_anomalies:
        state, anomalies = "anomaly", ["ml_outlier"] + rule_anomalies
    elif rule_anomalies:
        state, anomalies = "warning", rule_anomalies
    elif prediction == -1:
        state, anomalies = "warning", ["ml_outlier"]
    else:
        state, anomalies = "normal", []

    explanation = (
        f"ML score={score:.3f}; "
        f"rules matched: {len(rule_anomalies)}"
    )

    return StateMessage(
        device_id=msg.device_id,
        timestamp=utcnow_iso(),
        state=state,
        anomalies=anomalies,
        confidence=min(1.0, abs(score) / 0.5),
        explanation=explanation,
    )

def on_message(client: mqtt.Client, _userdata, mqtt_msg: mqtt.MQTTMessage) -> None:
    try:
        incoming = MetricsMessage.model_validate_json(mqtt_msg.payload)
    except Exception:
        log.exception("Bad metrics payload on %s", mqtt_msg.topic)
        return

    state = analyze(incoming)
    out_topic = TOPIC_STATE.format(device_id=state.device_id)
    client.publish(out_topic, state.model_dump_json(), qos=0)

    with STATE_LOCK:
        status = STATE_CACHE.setdefault(state.device_id, DeviceStatus(state.device_id))
        status.last_metrics = incoming.metrics.model_dump()
        status.last_state = state.model_dump()
        status.updated_at = state.timestamp

    with LAST_STATE_LOCK:
        prev = LAST_STATE.get(state.device_id, "normal")
        LAST_STATE[state.device_id] = state.state

    state_changed = prev != state.state
    is_problem    = state.state in ("warning", "anomaly")

    should_forward = is_problem and (state_changed or _should_remind(state.device_id))
    if should_forward:
        forward_alert(state, incoming.metrics.model_dump(), get_bounds(state.device_id))

    log.info(
        "%s → %s (conf=%.2f) anomalies=%s%s",
        state.device_id, state.state, state.confidence, state.anomalies,
        "  → ALERT FORWARDED" if should_forward else "",
    )


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
