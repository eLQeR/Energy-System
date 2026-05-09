"""Stage 3 — canonical Pydantic schema = output contract for the LLM.

Anthropic's messages.parse() validates the model's output against this
schema. Any field added here must also be wired into turtle.py and
(if you want to query it) into equipment.ttl.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HeatPumpProfile(BaseModel):
    """Specifications of a heat pump extracted from a manufacturer manual.

    Cite ONLY values stated explicitly in the source text. Use null when
    the spec is not stated; do not infer from related values.
    """

    # protected_namespaces: silences "model_*" Pydantic warning; we need
    # those names to match the domain vocabulary.
    # extra=forbid: required for Anthropic strict tool-use JSON schema.
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    manufacturer: str | None = Field(
        None, description="Manufacturer name, e.g. 'Mitsubishi Electric'"
    )
    model_series: str | None = Field(
        None, description="Model series, e.g. 'EHST20', 'EHPT20'"
    )
    model_variants: list[str] = Field(
        default_factory=list,
        description="Specific model SKU codes mentioned, e.g. ['EHST20D-VM6D']",
    )

    nominal_heating_power_kw: float | None = Field(
        None, description="Nominal heating capacity at standard conditions, kW"
    )
    max_heating_power_kw: float | None = Field(
        None, description="Maximum heating capacity, kW"
    )

    min_cop: float | None = Field(
        None, description="Minimum stated coefficient of performance"
    )
    nominal_cop: float | None = Field(
        None, description="Nominal/rated COP at standard conditions"
    )

    max_flow_temp_c: float | None = Field(
        None, description="Maximum flow water temperature, °C"
    )
    min_flow_temp_c: float | None = Field(
        None, description="Minimum flow water temperature, °C"
    )

    refrigerant: str | None = Field(
        None, description="Refrigerant designation, e.g. 'R32', 'R410A'"
    )
    tank_volume_l: float | None = Field(
        None, description="Hot water tank volume, liters"
    )
    weight_kg: float | None = Field(None, description="Empty weight, kg")
    power_supply_v: int | None = Field(None, description="Mains voltage, V")

    operating_modes: list[Literal["heating", "cooling", "dhw", "standby"]] = Field(
        default_factory=list, description="Modes the unit supports"
    )
    components: list[str] = Field(
        default_factory=list,
        description="Major listed components, e.g. ['compressor', 'plate heat exchanger']",
    )
