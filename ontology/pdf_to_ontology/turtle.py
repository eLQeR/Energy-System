"""Stage 5 — HeatPumpProfile → Turtle fragment ready for equipment.ttl."""
from __future__ import annotations

from .schema import HeatPumpProfile

PREFIX = (
    "@prefix lab:  <http://lab.example/ontology#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n\n"
)

_MODE_IRI = {
    "heating":  "lab:heating_mode",
    "cooling":  "lab:cooling_mode",
    "dhw":      "lab:dhw_mode",
    "standby":  "lab:standby_mode",
}


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def to_turtle(device_id: str, p: HeatPumpProfile) -> str:
    triples: list[str] = [f"lab:{device_id} a lab:AirToWaterHP"]
    add = lambda pred, val: triples.append(f"    {pred} {val}")

    if p.manufacturer:   add("lab:manufacturer", f'"{_esc(p.manufacturer)}"')
    if p.model_series:   add("lab:modelSeries",  f'"{_esc(p.model_series)}"')
    for v in p.model_variants:
        add("lab:modelVariant", f'"{_esc(v)}"')

    if p.nominal_heating_power_kw is not None:
        add("lab:nominalPowerKw", f'"{p.nominal_heating_power_kw}"^^xsd:float')
    if p.max_heating_power_kw is not None:
        add("lab:maxPowerKw", f'"{p.max_heating_power_kw}"^^xsd:float')
    if p.min_cop is not None:
        add("lab:minCOP", f'"{p.min_cop}"^^xsd:float')
    if p.nominal_cop is not None:
        add("lab:nominalCOP", f'"{p.nominal_cop}"^^xsd:float')
    if p.max_flow_temp_c is not None:
        add("lab:maxFlowTempC", f'"{p.max_flow_temp_c}"^^xsd:float')
    if p.min_flow_temp_c is not None:
        add("lab:minFlowTempC", f'"{p.min_flow_temp_c}"^^xsd:float')
    if p.refrigerant:
        add("lab:refrigerant", f'"{_esc(p.refrigerant)}"')
    if p.tank_volume_l is not None:
        add("lab:tankVolumeL", f'"{p.tank_volume_l}"^^xsd:float')
    if p.weight_kg is not None:
        add("lab:weightKg", f'"{p.weight_kg}"^^xsd:float')
    if p.power_supply_v is not None:
        add("lab:powerSupplyV", f'"{p.power_supply_v}"^^xsd:integer')

    for mode in p.operating_modes:
        add("lab:hasOperatingMode", _MODE_IRI[mode])

    body = " ;\n".join(triples) + " .\n"
    return PREFIX + body
