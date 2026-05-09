"""Спільний контракт повідомлень для всіх трьох підсистем.

Імпортується з monitoring/, ontology/, analyzer/ — щоб усі писали
і читали однаковий JSON. Змінювати тільки всіма трьома командами разом.
"""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Metrics(BaseModel):
    power_kw: float = Field(description="Поточна електрична потужність, кВт")
    energy_kwh: float = Field(description="Накопичена енергія, кВт·год")
    flow_temp_c: float = Field(description="Температура подачі, °C")
    return_temp_c: float = Field(description="Температура зворотки, °C")
    outdoor_temp_c: float | None = None
    cop: float | None = Field(default=None, description="Коефіцієнт трансформації")
    mode: Literal["heating", "cooling", "dhw", "standby", "unknown"] = "unknown"


class MetricsMessage(BaseModel):
    """Публікується Диплом-1 collector'ом у lab/equipment/{device_id}/metrics."""
    device_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    metrics: Metrics


class StateMessage(BaseModel):
    """Публікується Диплом-3 analyzer'ом у lab/equipment/{device_id}/state."""
    device_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    state: Literal["normal", "warning", "anomaly", "unknown"]
    anomalies: list[str] = []
    confidence: float = 1.0
    explanation: str = ""


TOPIC_METRICS = "lab/equipment/{device_id}/metrics"
TOPIC_STATE = "lab/equipment/{device_id}/state"
TOPIC_METRICS_WILDCARD = "lab/equipment/+/metrics"
TOPIC_STATE_WILDCARD = "lab/equipment/+/state"


class AlertPayload(BaseModel):
    """POST /api/alerts — Диплом 3 (edge-analyzer) → alerts_server.

    Створюється тільки коли стан переходить у warning/anomaly. Не публікуємо
    тривогу при кожному метричному повідомленні — тільки на зміну стану.
    """
    device_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    severity: Literal["warning", "anomaly"]
    anomaly_codes: list[str] = Field(
        description="Перелік порушень: cop_below_nominal, power_over_limit, ml_outlier, …",
    )
    explanation: str = ""
    confidence: float = 1.0
    metrics_snapshot: dict = Field(
        default_factory=dict,
        description="Метрики на момент детекції (Metrics.model_dump())",
    )
    bounds_snapshot: dict = Field(
        default_factory=dict,
        description="Межі з онтології, проти яких перевіряли (для аудиту)",
    )
