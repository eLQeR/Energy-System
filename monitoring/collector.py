"""ДИПЛОМ 1 — Collector: тягне метрики з MELCloud (EcoDan) і публікує в MQTT.

Запуск:
    python collector.py

Якщо немає доступу до реального EcoDan — виставити MELCLOUD_USER=demo
у .env, і collector буде генерувати правдоподібні синтетичні метрики.
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import Metrics, MetricsMessage, TOPIC_METRICS, utcnow_iso

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collector")

MELCLOUD_USER = os.getenv("MELCLOUD_USER", "demo")
MELCLOUD_PASS = os.getenv("MELCLOUD_PASS", "")
DEVICE_ID = os.getenv("DEVICE_ID", "ecodan_01")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "30"))
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

MELCLOUD_BASE = "https://app.melcloud.com/Mitsubishi.Wifi.Client"


class MELCloudClient:
    """Мінімальний клієнт MELCloud. Логін + один GET на пристрій."""

    def __init__(self, user: str, password: str):
        self.user = user
        self.password = password
        self.token: str | None = None
        self.device_id: int | None = None
        self.building_id: int | None = None

    def login(self) -> None:
        resp = requests.post(
            f"{MELCLOUD_BASE}/Login/ClientLogin",
            json={
                "Email": self.user,
                "Password": self.password,
                "Language": 0,
                "AppVersion": "1.23.4.0",
                "Persist": True,
                "CaptchaResponse": None,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ErrorId"):
            raise RuntimeError(f"MELCloud login failed: {data}")
        self.token = data["LoginData"]["ContextKey"]

    def discover_first_device(self) -> None:
        resp = requests.get(
            f"{MELCLOUD_BASE}/User/ListDevices",
            headers={"X-MitsContextKey": self.token},
            timeout=15,
        )
        resp.raise_for_status()
        buildings = resp.json()
        for building in buildings:
            for device in building.get("Structure", {}).get("Devices", []):
                self.device_id = device["DeviceID"]
                self.building_id = building["ID"]
                return
        raise RuntimeError("No devices found in MELCloud account")

    def fetch_state(self) -> dict:
        resp = requests.get(
            f"{MELCLOUD_BASE}/Device/Get",
            headers={"X-MitsContextKey": self.token},
            params={"id": self.device_id, "buildingID": self.building_id},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def map_melcloud_to_metrics(raw: dict) -> Metrics:
    """MELCloud повертає багато полів — беремо релевантні для ATW heat pump."""
    mode_map = {0: "heating", 1: "dhw", 2: "cooling", 3: "standby"}
    return Metrics(
        power_kw=float(raw.get("CurrentEnergyConsumed", 0.0)),
        energy_kwh=float(raw.get("DailyHeatingEnergyConsumed", 0.0)),
        flow_temp_c=float(raw.get("FlowTemperatureZone1", raw.get("FlowTemperature", 0.0))),
        return_temp_c=float(raw.get("ReturnTemperatureZone1", raw.get("ReturnTemperature", 0.0))),
        outdoor_temp_c=float(raw.get("OutdoorTemperature", 0.0)),
        cop=float(raw.get("DailyHeatingCOP", 0.0)) or None,
        mode=mode_map.get(raw.get("OperationMode", -1), "unknown"),
    )


def synthetic_metrics() -> Metrics:
    """Демо-режим — коли MELCloud недоступний."""
    base_power = 2.5 + random.gauss(0, 0.3)
    flow = 42 + random.gauss(0, 1.5)
    ret = flow - 6 - random.gauss(0, 0.5)
    return Metrics(
        power_kw=max(0.0, round(base_power, 2)),
        energy_kwh=round(time.time() % 86400 / 3600 * base_power, 2),
        flow_temp_c=round(flow, 1),
        return_temp_c=round(ret, 1),
        outdoor_temp_c=round(5 + random.gauss(0, 2), 1),
        cop=round(3.5 + random.gauss(0, 0.3), 2),
        mode="heating",
    )


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"collector-{DEVICE_ID}")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()
    log.info("Connected to MQTT %s:%s", MQTT_BROKER, MQTT_PORT)

    mel: MELCloudClient | None = None
    if MELCLOUD_USER != "demo" and MELCLOUD_PASS:
        try:
            mel = MELCloudClient(MELCLOUD_USER, MELCLOUD_PASS)
            mel.login()
            mel.discover_first_device()
            log.info("MELCloud connected, device_id=%s", mel.device_id)
        except Exception:
            log.exception("MELCloud unavailable, falling back to synthetic data")
            mel = None
    else:
        log.info("Demo mode — publishing synthetic metrics")

    topic = TOPIC_METRICS.format(device_id=DEVICE_ID)
    while True:
        try:
            metrics = map_melcloud_to_metrics(mel.fetch_state()) if mel else synthetic_metrics()
            msg = MetricsMessage(device_id=DEVICE_ID, timestamp=utcnow_iso(), metrics=metrics)
            client.publish(topic, msg.model_dump_json(), qos=0)
            log.info("Published metrics: power=%.2f kW flow=%.1f°C", metrics.power_kw, metrics.flow_temp_c)
        except Exception:
            log.exception("Collector iteration failed")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
