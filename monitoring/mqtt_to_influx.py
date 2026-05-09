"""ДИПЛОМ 1 — міст MQTT → InfluxDB.

Підписується на всі метрики з усіх пристроїв і пише їх у бакет metrics.
Дашборд Grafana читає звідти.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import MetricsMessage, TOPIC_METRICS_WILDCARD

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mqtt_to_influx")

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "lab-dev-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "lab")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "metrics")

influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)


def on_message(client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
    try:
        payload = MetricsMessage.model_validate_json(msg.payload)
    except Exception:
        log.exception("Invalid metrics payload on %s", msg.topic)
        return

    m = payload.metrics
    point = (
        Point("equipment_metrics")
        .tag("device_id", payload.device_id)
        .tag("mode", m.mode)
        .field("power_kw", m.power_kw)
        .field("energy_kwh", m.energy_kwh)
        .field("flow_temp_c", m.flow_temp_c)
        .field("return_temp_c", m.return_temp_c)
        .field("delta_t_c", m.flow_temp_c - m.return_temp_c)
    )
    if m.outdoor_temp_c is not None:
        point.field("outdoor_temp_c", m.outdoor_temp_c)
    if m.cop is not None:
        point.field("cop", m.cop)

    write_api.write(bucket=INFLUX_BUCKET, record=point, write_precision=WritePrecision.S)
    log.debug("Wrote point for %s", payload.device_id)


def on_connect(client, *_):
    log.info("MQTT connected, subscribing to %s", TOPIC_METRICS_WILDCARD)
    client.subscribe(TOPIC_METRICS_WILDCARD, qos=0)


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mqtt2influx")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
