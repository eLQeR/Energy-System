from __future__ import annotations

import logging
import os #читання env
import sys #зміна шляху імпорту
import time

from pathlib import Path

import paho.mqtt.client as mqtt
import requests #http запити

from dotenv import load_dotenv

#додає корінь проєкту в Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#імпорт спільних схем
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
log = logging.getLogger("edge_analyzer")

MQTT_BROKER         = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT           = int(os.getenv("MQTT_PORT", "1883"))
ONTOLOGY_API        = os.getenv("ONTOLOGY_API",  "http://localhost:5000")
ALERTS_API          = os.getenv("ALERTS_API",    "http://localhost:5003")
#Інтервал повторного нагадування про проблему
REMIND_INTERVAL_SEC = int(os.getenv("REMIND_INTERVAL_SEC", "900"))
#Час кешування меж пристрою
BOUNDS_TTL_SEC      = int(os.getenv("BOUNDS_TTL_SEC",      "300"))

#Словник для кешу меж пристроїв.
BOUNDS_CACHE:   dict[str, dict]  = {}
#Зберігає час, коли межі були отримані
BOUNDS_FETCHED: dict[str, float] = {}

#Зберігає останній стан кожного пристрою.
LAST_STATE:  dict[str, str]   = {}
#Зберігає час останнього alert-нагадування
LAST_REMIND: dict[str, float] = {}

def get_bounds(device_id: str) -> dict:
    now = time.time()
    if device_id in BOUNDS_CACHE and now - BOUNDS_FETCHED.get(device_id, 0) < BOUNDS_TTL_SEC:
        return BOUNDS_CACHE[device_id]
    try:
        #Робить GET-запит:http://server:5002/device/ecodan_01/expected-bounds
        r = requests.get(f"{ONTOLOGY_API}/device/{device_id}/expected-bounds", timeout=3)
        r.raise_for_status() #якщо помилка викличе ексепшн
        BOUNDS_CACHE[device_id]   = r.json() #Зберігає JSON-відповідь у кеш.
        BOUNDS_FETCHED[device_id] = now #Запам’ятовує час отримання меж.
    except requests.RequestException as exc:
        log.warning("Ontology API unreachable for %s (%s)", device_id, exc)
        BOUNDS_CACHE[device_id]   = BOUNDS_CACHE.get(device_id, {})
        BOUNDS_FETCHED[device_id] = now
    return BOUNDS_CACHE[device_id]

"""Перевірка значень проти онтологічних меж."""
def rule_based_checks(metrics: dict, bounds: dict) -> list[str]:
    anomalies: list[str] = []
    cop = metrics.get("cop")
    power = metrics.get("power_kw", 0.0)
    flow = metrics.get("flow_temp_c", 0.0)

    if bounds.get("min_cop") is not None and cop is not None:
        #Якщо фактичний COP менший за дозволений мінімум - проблема
        if cop < bounds["min_cop"]:
            anomalies.append(f"cop_below_nominal({cop:.2f}<{bounds['min_cop']:.2f})")

    if bounds.get("max_power_kw") is not None and power > bounds["max_power_kw"]:
        anomalies.append(f"power_over_limit({power:.2f}>{bounds['max_power_kw']:.2f})")

    if bounds.get("max_flow_c") is not None and flow > bounds["max_flow_c"]:
        anomalies.append(f"flow_temp_over_limit({flow:.1f}>{bounds['max_flow_c']:.1f})")

    if bounds.get("min_flow_c") is not None and flow < bounds["min_flow_c"]:
        anomalies.append(f"flow_temp_under_limit({flow:.1f}<{bounds['min_flow_c']:.1f})")

    return anomalies

"""Будь-яке порушення онтологічних меж — anomaly. ML-сигнали тут відсутні
(edge_analyzer не має моделі), тож якщо є коди — це rule breach → червоне."""
def classify(anomalies: list[str]) -> str:
    if not anomalies:
        return "normal"
    return "anomaly"


def analyze(msg: MetricsMessage) -> StateMessage:
    #Перетворює Pydantic-об’єкт метрик у звичайний Python dict.
    metrics = msg.metrics.model_dump()
    #Отримує допустимі межі для цього пристрою
    bounds  = get_bounds(msg.device_id)
    anomalies = rule_based_checks(metrics, bounds)
    state = classify(anomalies)

    return StateMessage(
        device_id=msg.device_id,
        timestamp=utcnow_iso(),
        state=state,
        anomalies=anomalies,
        confidence=1.0,
        explanation=f"rule-checks: {len(anomalies)} matched",
    )

#Перевіряє, чи треба повторно нагадати про проблему.
def _should_remind(device_id: str) -> bool:
    now = time.time()
    if now - LAST_REMIND.get(device_id, 0) >= REMIND_INTERVAL_SEC:
        LAST_REMIND[device_id] = now
        return True
    return False

def send_heartbeat(state: StateMessage, metrics_dump: dict, bounds: dict) -> None:
    """Оновлює last_seen у alerts_server на КОЖЕН метрик. Інакше UI бачить
    «онлайн» лише під час алертів."""
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


#Функція відправляє alert на центральний сервер
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

#функція автоматично викликається, коли MQTT отримує нове повідомлення
def on_message(client: mqtt.Client, _userdata, mqtt_msg: mqtt.MQTTMessage) -> None:
    try:
        #Перевіряє JSON і створює MetricsMessage
        incoming = MetricsMessage.model_validate_json(mqtt_msg.payload)
    except Exception:
        log.exception("Bad metrics payload on %s", mqtt_msg.topic)
        return

    state = analyze(incoming)
    #Формує MQTT topic для публікації стану
    out_topic = TOPIC_STATE.format(device_id=state.device_id)
    #Публікує стан у MQTT
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

    log.info("%s → %s anomalies=%s%s",
             state.device_id, state.state, state.anomalies,
             "  → ALERT FORWARDED" if should_forward else "")


def on_connect(client, *_):
    log.info("MQTT connected - subscribing %s", TOPIC_METRICS_WILDCARD)
    client.subscribe(TOPIC_METRICS_WILDCARD, qos=0)


def main() -> None:
    log.info("LabEnergy starting  (broker=%s:%d, ontology=%s, alerts=%s)",
             MQTT_BROKER, MQTT_PORT, ONTOLOGY_API, ALERTS_API)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-edge-analyzer")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
