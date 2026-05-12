"""Stage 5 — HeatPumpProfile → Turtle fragment ready for equipment.ttl.

Produces a self-contained Turtle block:
    @prefix declarations
    lab:<device_id>            — main equipment instance with specs + links
    lab:<device_id>_c_<slug>   — Component instances
    lab:<device_id>_f<NN>      — FaultCase instances
    lab:<device_id>_err_<code> — ErrorCode instances
    lab:<device_id>_mp_<slug>  — MaintenancePart instances
"""
from __future__ import annotations

import re

from .schema import (
    Component,
    ErrorCode,
    FaultCase,
    HeatPumpProfile,
    MaintenancePart,
)

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

_COMPONENT_CLASS = {
    "compressor":    "lab:Compressor",
    "evaporator":    "lab:Evaporator",
    "condenser":     "lab:Condenser",
    # everything else maps to the generic lab:Component
}


def _esc(s: str) -> str:
    """Escape a string for inclusion in a Turtle double-quoted literal."""
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", " ")
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("_", s.lower()).strip("_")
    return (s or "x")[:max_len]


def _component_iri(device_id: str, idx: int, c: Component) -> str:
    return f"lab:{device_id}_c{idx:02d}_{_slug(c.name)}"


def _code_slug(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", code).strip("_") or "x"


def _emit_specs(device_id: str, p: HeatPumpProfile,
                comp_iris: list[str]) -> str:
    """Main `lab:{device_id}` block with specs + hasComponent/hasFault links."""
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
        if mode in _MODE_IRI:
            add("lab:hasOperatingMode", _MODE_IRI[mode])

    for iri in comp_iris:
        add("lab:hasComponent", iri)

    for i, _ in enumerate(p.fault_cases, start=1):
        add("lab:hasFaultCase", f"lab:{device_id}_f{i:02d}")
    for ec in p.error_codes:
        add("lab:hasErrorCode", f"lab:{device_id}_err_{_code_slug(ec.code)}")
    for i, mp in enumerate(p.maintenance_parts, start=1):
        add("lab:hasMaintenancePart", f"lab:{device_id}_mp{i:02d}_{_slug(mp.name)}")

    return " ;\n".join(triples) + " .\n"


def _emit_components(device_id: str, comps: list[Component],
                     iris: list[str]) -> str:
    out: list[str] = []
    for iri, c in zip(iris, comps):
        cls = _COMPONENT_CLASS.get(c.component_type or "", "lab:Component")
        lines = [f"{iri} a {cls}",
                 f'    rdfs:label "{_esc(c.name)}"']
        if c.description:
            lines.append(f'    lab:description "{_esc(c.description)}"')
        out.append(" ;\n".join(lines) + " .\n")
    return "\n".join(out)


def _comp_name_to_iri(comps: list[Component], iris: list[str]) -> dict[str, str]:
    """Case-insensitive map: component name → IRI (for fault.affects_component)."""
    m: dict[str, str] = {}
    for c, iri in zip(comps, iris):
        m[c.name.lower().strip()] = iri
    return m


def _resolve_component_iri(
    affects: str | None, name_map: dict[str, str],
) -> str | None:
    if not affects:
        return None
    key = affects.lower().strip()
    if key in name_map:
        return name_map[key]
    # try a substring match (handles "Booster heater" vs "Booster heater (3 kW)")
    for n, iri in name_map.items():
        if key in n or n in key:
            return iri
    return None


def _emit_fault_cases(device_id: str, faults: list[FaultCase],
                      name_map: dict[str, str]) -> str:
    out: list[str] = []
    for i, f in enumerate(faults, start=1):
        iri = f"lab:{device_id}_f{i:02d}"
        lines = [
            f"{iri} a lab:FaultCase",
            f'    rdfs:label        "{_esc(f.symptom)}"',
            f'    lab:faultSymptom  "{_esc(f.symptom)}"',
            f'    lab:possibleCause "{_esc(f.cause)}"',
            f'    lab:solution      "{_esc(f.solution)}"',
        ]
        if f.severity:
            lines.append(f'    lab:severity      "{f.severity}"')
        if f.affects_component:
            comp_iri = _resolve_component_iri(f.affects_component, name_map)
            if comp_iri:
                lines.append(f"    lab:affectsComponent {comp_iri}")
            else:
                # keep the raw name as a literal so the info isn't lost
                lines.append(
                    f'    lab:affectsComponentName "{_esc(f.affects_component)}"'
                )
        out.append(" ;\n".join(lines) + " .\n")
    return "\n".join(out)


def _emit_error_codes(device_id: str, codes: list[ErrorCode]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for c in codes:
        slug = _code_slug(c.code)
        # avoid duplicate IRIs if LLM repeated a code
        unique = slug
        suffix = 2
        while unique in seen:
            unique = f"{slug}_{suffix}"
            suffix += 1
        seen.add(unique)
        iri = f"lab:{device_id}_err_{unique}"
        lines = [
            f"{iri} a lab:ErrorCode",
            f'    rdfs:label           "{_esc(c.code)} — {_esc(c.description)}"',
            f'    lab:codeId           "{_esc(c.code)}"',
            f'    lab:errorDescription "{_esc(c.description)}"',
            f'    lab:errorAction      "{_esc(c.action)}"',
        ]
        if c.severity:
            lines.append(f'    lab:severity         "{c.severity}"')
        out.append(" ;\n".join(lines) + " .\n")
    return "\n".join(out)


def _emit_maintenance(device_id: str, parts: list[MaintenancePart]) -> str:
    out: list[str] = []
    for i, mp in enumerate(parts, start=1):
        iri = f"lab:{device_id}_mp{i:02d}_{_slug(mp.name)}"
        lines = [
            f"{iri} a lab:MaintenancePart",
            f'    rdfs:label "{_esc(mp.name)}"',
        ]
        if mp.replace_every_years is not None:
            lines.append(
                f'    lab:replaceEveryYears "{mp.replace_every_years}"^^xsd:integer'
            )
        if mp.replace_every_hours is not None:
            lines.append(
                f'    lab:replaceEveryHours "{mp.replace_every_hours}"^^xsd:integer'
            )
        if mp.check_every_years is not None:
            lines.append(
                f'    lab:checkEveryYears   "{mp.check_every_years}"^^xsd:integer'
            )
        if mp.typical_failure:
            lines.append(f'    lab:typicalFailure    "{_esc(mp.typical_failure)}"')
        out.append(" ;\n".join(lines) + " .\n")
    return "\n".join(out)


def to_turtle(device_id: str, p: HeatPumpProfile) -> str:
    comp_iris = [_component_iri(device_id, i, c)
                 for i, c in enumerate(p.components, start=1)]
    name_map = _comp_name_to_iri(p.components, comp_iris)

    sections = [
        PREFIX,
        _emit_specs(device_id, p, comp_iris),
    ]
    if p.components:
        sections += ["\n# --- Components ---\n",
                     _emit_components(device_id, p.components, comp_iris)]
    if p.fault_cases:
        sections += ["\n# --- Fault cases ---\n",
                     _emit_fault_cases(device_id, p.fault_cases, name_map)]
    if p.error_codes:
        sections += ["\n# --- Error codes ---\n",
                     _emit_error_codes(device_id, p.error_codes)]
    if p.maintenance_parts:
        sections += ["\n# --- Maintenance parts ---\n",
                     _emit_maintenance(device_id, p.maintenance_parts)]

    return "".join(sections)
