"""Генератор синтетичних метрик теплового насоса для розробки та тестування.

Публікує MetricsMessage-сумісні дані трьома способами:
  - MQTT  — як справжній collector (для тестування analyzer у реальному часі)
  - JSONL — дамп N повідомлень у файл (для офлайн-тестування)
  - stdout — потік JSON (для piping у інші утиліти)

Фізична модель прив'язана до констант онтології (equipment.ttl):
  device_id = "ecodan_01"
  nominalPowerKw = 2.5, maxPowerKw = 4.0, minCOP = 2.8
  maxFlowTempC = 60, minFlowTempC = 20

Запуск:
  # MQTT (безперервно, 30 с інтервал):
  python monitoring/synthetic_publisher.py --output mqtt

  # JSONL файл, 500 нормальних + 50 аномальних:
  python monitoring/synthetic_publisher.py --output file --count 500 \\
      --anomaly low_cop:50

  # stdout, лише heating режим:
  python monitoring/synthetic_publisher.py --output stdout --count 20 \\
      --mode heating

  # Усі аномалії одразу (для тестування analyzer):
  python monitoring/synthetic_publisher.py --output mqtt --anomaly all
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import Metrics, MetricsMessage, TOPIC_METRICS, utcnow_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("synthetic")

# ─── Константи онтології (equipment.ttl → lab:ecodan_01) ──────────────────────
DEVICE_ID        = os.getenv("DEVICE_ID", "ecodan_01")
NOMINAL_POWER_KW = 2.5
MAX_POWER_KW     = 4.0
MIN_COP          = 2.8   # lab:minCOP — поріг, нижче якого analyzer б'є тривогу
MAX_FLOW_TEMP_C  = 60.0  # lab:maxFlowTempC
MIN_FLOW_TEMP_C  = 20.0  # lab:minFlowTempC

# ─── Параметри MQTT ────────────────────────────────────────────────────────────
MQTT_BROKER   = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "60"))

Mode = Literal["heating", "dhw", "standby", "cooling"]

AnomalyKind = Literal[
    "low_cop",       # COP < minCOP — можливий витік холодоагенту
    "high_power",    # power > maxPowerKw — перевантаження
    "stuck_flow",    # flow_temp не реагує на outdoor_temp
    "sensor_fault",  # temperature зовсім поза діапазоном
    "all",           # по черзі всі 4 типи
]


# ─── Фізична модель ────────────────────────────────────────────────────────────

@dataclass
class ScenarioState:
    """Змінний стан симуляції між тактами."""
    t: float = 0.0            # абстрактний час (секунди від старту)
    energy_kwh: float = 0.0   # накопичена енергія
    mode: Mode = "heating"
    anomaly_remaining: int = 0
    anomaly_kind: AnomalyKind | None = None
    _anomaly_queue: list[AnomalyKind] = field(default_factory=list)


def outdoor_temp(t: float) -> float:
    """Реалістична зовнішня температура з добовим циклом і шумом."""
    seasonal = -5 * math.cos(2 * math.pi * t / (365 * 86400))   # -5 … +5
    daily    = -3 * math.cos(2 * math.pi * t / 86400 - math.pi) # холодніше вночі
    noise    = random.gauss(0, 0.4)
    return round(seasonal + daily + noise, 1)


def cop_model(t_outdoor: float, t_flow: float) -> float:
    """Апроксимація COP для ATW теплового насоса типу EHST20D/PUHZ-W.

    Базова точка: COP=2.8 при A-3/W35 (паспортне значення ecodan_01).
    Формула: COP залежить лінійно від outdoor та flow temp.
    """
    base = 2.8
    cop = base + 0.055 * (t_outdoor - (-3)) - 0.025 * (t_flow - 35)
    return round(max(1.5, min(5.5, cop + random.gauss(0, 0.05))), 2)


def heating_metrics(state: ScenarioState, t_out: float) -> Metrics:
    flow   = min(MAX_FLOW_TEMP_C, max(MIN_FLOW_TEMP_C,
                 38 - 0.4 * t_out + random.gauss(0, 0.8)))
    delta  = max(3.0, 5 + random.gauss(0, 0.5))
    ret    = round(flow - delta, 1)
    power  = round(min(MAX_POWER_KW, NOMINAL_POWER_KW + 0.06 * (flow - 35)
                       + random.gauss(0, 0.1)), 2)
    cop    = cop_model(t_out, flow)
    return Metrics(
        power_kw=max(0.5, power),
        energy_kwh=round(state.energy_kwh, 3),
        flow_temp_c=round(flow, 1),
        return_temp_c=ret,
        outdoor_temp_c=t_out,
        cop=cop,
        mode="heating",
    )


def dhw_metrics(state: ScenarioState, t_out: float) -> Metrics:
    """DHW: вищий flow (до 55°C), коротший цикл, нижчий COP."""
    flow  = min(55.0, 48 + random.gauss(0, 1.0))
    delta = max(4.0, 6 + random.gauss(0, 0.5))
    ret   = round(flow - delta, 1)
    power = round(min(MAX_POWER_KW, 2.8 + random.gauss(0, 0.15)), 2)
    cop   = round(max(1.8, cop_model(t_out, flow) * 0.85), 2)  # DHW менш ефективний
    return Metrics(
        power_kw=max(0.5, power),
        energy_kwh=round(state.energy_kwh, 3),
        flow_temp_c=round(flow, 1),
        return_temp_c=ret,
        outdoor_temp_c=t_out,
        cop=cop,
        mode="dhw",
    )


def standby_metrics(state: ScenarioState, t_out: float) -> Metrics:
    return Metrics(
        power_kw=round(abs(random.gauss(0.05, 0.02)), 3),
        energy_kwh=round(state.energy_kwh, 3),
        flow_temp_c=round(MIN_FLOW_TEMP_C + random.gauss(0, 0.3), 1),
        return_temp_c=round(MIN_FLOW_TEMP_C - 1 + random.gauss(0, 0.2), 1),
        outdoor_temp_c=t_out,
        cop=None,
        mode="standby",
    )


def cooling_metrics(state: ScenarioState, t_out: float) -> Metrics:
    flow  = round(18 + random.gauss(0, 1.0), 1)
    ret   = round(flow + 4 + random.gauss(0, 0.5), 1)
    power = round(min(MAX_POWER_KW, 2.0 + random.gauss(0, 0.1)), 2)
    cop   = round(max(2.0, 3.5 - 0.03 * (t_out - 25) + random.gauss(0, 0.1)), 2)
    return Metrics(
        power_kw=max(0.5, power),
        energy_kwh=round(state.energy_kwh, 3),
        flow_temp_c=flow,
        return_temp_c=ret,
        outdoor_temp_c=t_out,
        cop=cop,
        mode="cooling",
    )


# ─── Ін'єкція аномалій ─────────────────────────────────────────────────────────

def inject_anomaly(base: Metrics, kind: AnomalyKind) -> Metrics:
    """Модифікує нормальні метрики для імітації несправності.

    Значення виходять за межі, описані в ontology (minCOP, maxPowerKw,
    maxFlowTempC, minFlowTempC), що дозволяє analyzer їх детектувати.
    """
    d = base.model_dump()

    if kind == "low_cop":
        # COP нижче lab:minCOP=2.8 — симулює витік холодоагенту
        d["cop"] = round(random.uniform(0.8, MIN_COP - 0.3), 2)
        d["power_kw"] = round(base.power_kw * 1.35 + random.gauss(0, 0.1), 2)

    elif kind == "high_power":
        # Споживання вище lab:maxPowerKw=4.0
        d["power_kw"] = round(random.uniform(MAX_POWER_KW + 0.5, MAX_POWER_KW + 2.0), 2)
        d["cop"] = round(max(0.5, (base.cop or 2.8) * 0.6), 2)

    elif kind == "stuck_flow":
        # flow_temp фіксована незалежно від outdoor_temp — несправність датчика / клапана
        d["flow_temp_c"] = round(random.uniform(MIN_FLOW_TEMP_C, MIN_FLOW_TEMP_C + 3), 1)
        d["return_temp_c"] = d["flow_temp_c"] - 0.5  # майже немає дельти

    elif kind == "sensor_fault":
        # Значення за межами фізично можливих (MAX_FLOW_TEMP_C=60)
        d["flow_temp_c"] = round(random.uniform(MAX_FLOW_TEMP_C + 5, MAX_FLOW_TEMP_C + 20), 1)
        d["return_temp_c"] = round(random.uniform(MAX_FLOW_TEMP_C + 1, MAX_FLOW_TEMP_C + 15), 1)
        d["outdoor_temp_c"] = round(random.uniform(-50, -30), 1)

    return Metrics(**d)


# ─── Вибір режиму за зовнішньою температурою ──────────────────────────────────

def choose_mode(t_out: float, current: Mode, t: float) -> Mode:
    """Простий автомат переключення режимів.

    - standby при t_out > 15°C і не DHW-час
    - dhw раз на 4 години (нагрів бака)
    - heating при t_out < 15°C
    - cooling при t_out > 22°C (потрібно явно задати --mode cooling)
    """
    hour = (t % 86400) / 3600
    is_dhw_window = hour in range(6, 7) or hour in range(18, 19)

    if is_dhw_window and current != "dhw":
        return "dhw"
    if current == "dhw":
        # DHW-цикл ~30 хв (1800 с)
        return "dhw" if (t % 3600) < 1800 else "heating"
    if t_out > 15:
        return "standby"
    return "heating"


# ─── Генератор одного MetricsMessage ──────────────────────────────────────────

def next_message(
    state: ScenarioState,
    force_mode: Mode | None = None,
    dt: float = 30.0,
) -> MetricsMessage:
    state.t += dt
    t_out = outdoor_temp(state.t)

    if force_mode:
        state.mode = force_mode
    else:
        state.mode = choose_mode(t_out, state.mode, state.t)

    if state.mode == "heating":
        m = heating_metrics(state, t_out)
    elif state.mode == "dhw":
        m = dhw_metrics(state, t_out)
    elif state.mode == "cooling":
        m = cooling_metrics(state, t_out)
    else:
        m = standby_metrics(state, t_out)

    state.energy_kwh += m.power_kw * dt / 3600

    # Ін'єкція аномалій
    if state.anomaly_remaining > 0:
        kind = state.anomaly_kind
        if kind == "all":
            if not state._anomaly_queue:
                state._anomaly_queue = ["low_cop", "high_power", "stuck_flow", "sensor_fault"]
            kind = state._anomaly_queue[0]
            if state.anomaly_remaining % max(1, state.anomaly_remaining // 4) == 0:
                if state._anomaly_queue:
                    state._anomaly_queue.pop(0)
        if kind and kind != "all":
            m = inject_anomaly(m, kind)  # type: ignore[arg-type]
        state.anomaly_remaining -= 1

    return MetricsMessage(
        device_id=DEVICE_ID,
        timestamp=utcnow_iso(),
        metrics=m,
    )


# ─── Output-бекенди ───────────────────────────────────────────────────────────

def publish_mqtt(msgs: list[MetricsMessage], interval: float = 0.0) -> None:
    try:
        import paho.mqtt.client as mqtt_client
    except ImportError:
        log.error("paho-mqtt не встановлено: pip install paho-mqtt")
        sys.exit(1)

    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2,
                                client_id=f"synthetic-{DEVICE_ID}")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()
    topic = TOPIC_METRICS.format(device_id=DEVICE_ID)
    log.info("Connected to MQTT %s:%d  topic=%s", MQTT_BROKER, MQTT_PORT, topic)

    for msg in msgs:
        client.publish(topic, msg.model_dump_json(), qos=0)
        log.info("[MQTT] %s  power=%.2f kW  cop=%s  mode=%s  anomaly=%s",
                 msg.timestamp, msg.metrics.power_kw,
                 f"{msg.metrics.cop:.2f}" if msg.metrics.cop else "—",
                 msg.metrics.mode,
                 "⚠" if msg.metrics.cop and msg.metrics.cop < MIN_COP else "")
        if interval > 0:
            time.sleep(interval)

    client.loop_stop()
    client.disconnect()


def publish_file(msgs: list[MetricsMessage], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for msg in msgs:
            f.write(msg.model_dump_json() + "\n")
    log.info("Wrote %d messages to %s", len(msgs), path)


def publish_stdout(msgs: list[MetricsMessage]) -> None:
    for msg in msgs:
        print(msg.model_dump_json())


# ─── Безперервний MQTT-режим (live) ───────────────────────────────────────────

def run_live_mqtt(args: argparse.Namespace) -> None:
    """Нескінченний цикл — аналог collector.py, але з гнучкою ін'єкцією аномалій."""
    try:
        import paho.mqtt.client as mqtt_mod
    except ImportError:
        log.error("paho-mqtt не встановлено: pip install paho-mqtt")
        sys.exit(1)

    client = mqtt_mod.Client(mqtt_mod.CallbackAPIVersion.VERSION2,
                             client_id=f"synthetic-{DEVICE_ID}")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()
    topic = TOPIC_METRICS.format(device_id=DEVICE_ID)
    log.info("Live MQTT publisher started → %s  interval=%ds", topic, POLL_INTERVAL)

    state = ScenarioState()
    _apply_anomaly_args(state, args)

    while True:
        msg = next_message(state, force_mode=args.mode or None, dt=float(POLL_INTERVAL))
        client.publish(topic, msg.model_dump_json(), qos=0)
        _log_msg(msg)
        time.sleep(POLL_INTERVAL)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _apply_anomaly_args(state: ScenarioState, args: argparse.Namespace) -> None:
    if not args.anomaly:
        return
    parts = args.anomaly.split(":")
    kind  = parts[0]
    count = int(parts[1]) if len(parts) > 1 else args.count
    state.anomaly_kind      = kind  # type: ignore[assignment]
    state.anomaly_remaining = count
    if kind == "all":
        state._anomaly_queue = ["low_cop", "high_power", "stuck_flow", "sensor_fault"]


def _log_msg(msg: MetricsMessage) -> None:
    m = msg.metrics
    flag = ""
    if m.cop is not None and m.cop < MIN_COP:
        flag = "  ⚠ LOW COP"
    if m.power_kw > MAX_POWER_KW:
        flag = "  ⚠ HIGH POWER"
    if m.flow_temp_c > MAX_FLOW_TEMP_C:
        flag = "  ⚠ SENSOR FAULT"
    log.info("%s  mode=%-7s  power=%.2f kW  flow=%.1f°C  cop=%s%s",
             msg.timestamp, m.mode, m.power_kw, m.flow_temp_c,
             f"{m.cop:.2f}" if m.cop else "—", flag)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Synthetic heat-pump metrics publisher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--output", choices=["mqtt", "file", "stdout"], default="stdout",
                   help="Куди публікувати: mqtt | file | stdout (default: stdout)")
    p.add_argument("--count", type=int, default=200,
                   help="Кількість повідомлень для file/stdout (default: 200)")
    p.add_argument("--mode", choices=["heating", "dhw", "standby", "cooling"], default=None,
                   help="Примусово задати режим. Без цього — автомат.")
    p.add_argument("--anomaly",
                   metavar="KIND[:COUNT]",
                   help=(
                       "Ін'єкція аномалій. KIND: low_cop | high_power | "
                       "stuck_flow | sensor_fault | all. "
                       "COUNT: кількість аномальних повідомлень (default = --count). "
                       "Приклад: --anomaly low_cop:50"
                   ))
    p.add_argument("--out-file", type=Path,
                   default=Path(__file__).parent / "synthetic_output.jsonl",
                   help="Файл виводу при --output file")
    p.add_argument("--dt", type=float, default=30.0,
                   help="Крок часу між повідомленнями, сек (default: 30)")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.output == "mqtt" and args.count == 200 and not args.anomaly:
        # live mode — нескінченно, як collector
        run_live_mqtt(args)
        return

    state = ScenarioState()
    _apply_anomaly_args(state, args)

    msgs = [
        next_message(state, force_mode=args.mode or None, dt=args.dt)
        for _ in range(args.count)
    ]

    if args.output == "mqtt":
        publish_mqtt(msgs)
    elif args.output == "file":
        publish_file(msgs, args.out_file)
    else:
        publish_stdout(msgs)


if __name__ == "__main__":
    main()
