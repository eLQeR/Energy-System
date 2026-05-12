"""Stage 3 — canonical Pydantic schema = output contract for the LLM.

Anthropic's forced tool use validates the model's output against this
schema. Any field added here must also be wired into turtle.py and
(if you want to query it) into equipment.ttl.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "low", "medium", "high", "critical"]


class Component(BaseModel):
    """A physical/logical part of the unit that may be referenced by faults."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Short component name as it appears in the manual, "
                    "e.g. 'Booster heater', '3-way valve', 'Plate heat exchanger'.",
    )
    component_type: Literal[
        "compressor", "evaporator", "condenser", "heat_exchanger",
        "heater", "valve", "pump", "vessel", "sensor", "thermistor",
        "controller", "strainer", "receiver", "fan", "expansion_device",
        "other",
    ] | None = Field(
        None, description="Canonical category for the component.",
    )
    description: str | None = Field(
        None, description="Optional short description (1 sentence).",
    )


class FaultCase(BaseModel):
    """One row from the troubleshooting / basic fault-finding table."""

    model_config = ConfigDict(extra="forbid")

    symptom: str = Field(
        description="Observable fault symptom exactly as stated, e.g. "
                    "'Cold water at tap', 'Water discharges from expansion relief valve'.",
    )
    cause: str = Field(
        description="Single possible cause for this symptom (one row of the "
                    "table). If the manual marks the cause as Direct/Indirect "
                    "or Continual/Intermittent, keep that prefix verbatim.",
    )
    solution: str = Field(
        description="Recommended action as written in the manual. Preserve "
                    "specific part numbers, button names, page references.",
    )
    severity: Severity | None = Field(
        None, description="Infer from the action: 'info'=no action needed, "
                          "'low'=user adjustment, 'medium'=service-call, "
                          "'high'=isolate/replace part, 'critical'=safety risk.",
    )
    affects_component: str | None = Field(
        None, description="Name of the component this fault is about, taken "
                          "from the components list above when possible "
                          "(e.g. 'Booster heater', '3-way valve'). Null if "
                          "fault is system-wide or affects no specific part.",
    )


class ErrorCode(BaseModel):
    """One row from the controller error-code table (L1, E9, U*/F*, etc.)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description="Exact code or code-range as printed, e.g. 'L1', 'J1-J8', "
                    "'E6-EF', 'U*/F*'.",
    )
    description: str = Field(
        description="What the controller is signalling, e.g. "
                    "'Booster heater overheat detection'.",
    )
    action: str = Field(
        description="Diagnostic / repair action recommended, verbatim where possible.",
    )
    severity: Severity | None = Field(
        None, description="Infer from the action — see FaultCase.severity.",
    )


class MaintenancePart(BaseModel):
    """A part listed with a service interval or inspection cadence."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Part name as printed, e.g. 'Pressure relief valve (PRV)'.",
    )
    replace_every_years: int | None = Field(
        None, description="Scheduled replacement interval in years (if stated).",
    )
    replace_every_hours: int | None = Field(
        None, description="Scheduled replacement interval in operating hours "
                          "(if stated, e.g. pump = 20 000 h).",
    )
    check_every_years: int | None = Field(
        None, description="Inspection cadence in years (if stated).",
    )
    typical_failure: str | None = Field(
        None, description="The expected failure mode listed for this part, "
                          "e.g. 'Water leakage due to copper corrosion'.",
    )


class HeatPumpProfile(BaseModel):
    """Specifications, components and service knowledge extracted from a
    manufacturer manual.

    Cite ONLY values stated explicitly in the source text. Use null / empty
    list when the spec is not stated; do not infer from related values.
    """

    # protected_namespaces: silences "model_*" Pydantic warning; we need
    # those names to match the domain vocabulary.
    # extra=forbid: required for Anthropic strict tool-use JSON schema.
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    # ── identification ──────────────────────────────────────────────────
    manufacturer: str | None = Field(
        None, description="Manufacturer name, e.g. 'Mitsubishi Electric'.",
    )
    model_series: str | None = Field(
        None, description="Model series, e.g. 'EHST20', 'EHPT20', 'PUHZ-W'.",
    )
    model_variants: list[str] = Field(
        default_factory=list,
        description="Specific model SKU codes mentioned, e.g. "
                    "['EHST20D-VM6D', 'EHST20C-VM6HB'].",
    )

    # ── performance ─────────────────────────────────────────────────────
    nominal_heating_power_kw: float | None = Field(
        None, description="Nominal heating capacity at standard conditions, kW.",
    )
    max_heating_power_kw: float | None = Field(
        None, description="Maximum heating capacity, kW.",
    )
    min_cop: float | None = Field(
        None, description="Minimum stated coefficient of performance.",
    )
    nominal_cop: float | None = Field(
        None, description="Nominal/rated COP at standard conditions.",
    )
    max_flow_temp_c: float | None = Field(
        None, description="Maximum flow water temperature, °C.",
    )
    min_flow_temp_c: float | None = Field(
        None, description="Minimum flow water temperature, °C.",
    )

    # ── physical / electrical ──────────────────────────────────────────
    refrigerant: str | None = Field(
        None, description="Refrigerant designation, e.g. 'R32', 'R410A'.",
    )
    tank_volume_l: float | None = Field(
        None, description="Hot water tank volume, litres.",
    )
    weight_kg: float | None = Field(None, description="Empty weight, kg.")
    power_supply_v: int | None = Field(None, description="Mains voltage, V.")

    operating_modes: list[Literal["heating", "cooling", "dhw", "standby"]] = Field(
        default_factory=list, description="Modes the unit supports.",
    )

    # ── structured knowledge ───────────────────────────────────────────
    components: list[Component] = Field(
        default_factory=list,
        description="Major listed components. Extract every part referenced "
                    "in the troubleshooting / maintenance / parts sections.",
    )
    fault_cases: list[FaultCase] = Field(
        default_factory=list,
        description="One entry per row of the troubleshooting / "
                    "fault-finding table. Do NOT deduplicate by symptom — "
                    "each (symptom, cause, solution) row is its own entry.",
    )
    error_codes: list[ErrorCode] = Field(
        default_factory=list,
        description="One entry per row of the controller error-code table. "
                    "Include code-ranges (e.g. 'J1-J8') as a single entry.",
    )
    maintenance_parts: list[MaintenancePart] = Field(
        default_factory=list,
        description="Parts with replacement / inspection schedules (from the "
                    "'Parts which require regular replacement/inspection' "
                    "tables).",
    )
