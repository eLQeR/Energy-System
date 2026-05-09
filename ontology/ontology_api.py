"""ДИПЛОМ 2 — HTTP API над SPARQL-endpoint.

Інші дипломи звертаються сюди, щоб отримати характеристики та очікувані
режими обладнання. Всі маршрути повертають JSON.

Запуск:
    python ontology_api.py
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify
from SPARQLWrapper import JSON, SPARQLWrapper

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ontology_api")

FUSEKI_URL = os.getenv("FUSEKI_URL", "http://localhost:3030/lab")
PREFIX = """
PREFIX lab:  <http://lab.example/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
"""

app = Flask(__name__)


def sparql_query(query: str) -> list[dict]:
    s = SPARQLWrapper(f"{FUSEKI_URL}/query")
    s.setQuery(PREFIX + query)
    s.setReturnFormat(JSON)
    bindings = s.query().convert()["results"]["bindings"]
    return [{k: v["value"] for k, v in row.items()} for row in bindings]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/devices")
def list_devices():
    rows = sparql_query("""
        SELECT ?device ?label ?model WHERE {
            ?device a/rdfs:subClassOf* lab:Equipment ;
                    rdfs:label ?label .
            OPTIONAL { ?device lab:model ?model }
        }
    """)
    return jsonify([
        {"id": r["device"].split("#")[-1], "label": r.get("label"), "model": r.get("model")}
        for r in rows
    ])


@app.get("/device/<device_id>/specs")
def device_specs(device_id: str):
    rows = sparql_query(f"""
        SELECT ?prop ?value WHERE {{
            lab:{device_id} ?prop ?value .
            FILTER(isLiteral(?value))
        }}
    """)
    if not rows:
        abort(404, f"Unknown device {device_id}")
    specs = {r["prop"].split("#")[-1]: r["value"] for r in rows}
    return jsonify({"device_id": device_id, "specs": specs})


@app.get("/device/<device_id>/expected-bounds")
def expected_bounds(device_id: str):
    """Межі для детектора аномалій — використовує Диплом 3."""
    # Fallbacks: if the explicit anomaly threshold isn't set, use the
    # manufacturer's nominal value as a baseline (conservative).
    rows = sparql_query(f"""
        SELECT ?minCOP ?maxPower ?maxFlow ?minFlow WHERE {{
            OPTIONAL {{ lab:{device_id} lab:minCOP         ?minCOPExplicit }}
            OPTIONAL {{ lab:{device_id} lab:nominalCOP     ?nomCOP }}
            BIND(COALESCE(?minCOPExplicit, ?nomCOP) AS ?minCOP)

            OPTIONAL {{ lab:{device_id} lab:maxPowerKw     ?maxPowerExplicit }}
            OPTIONAL {{ lab:{device_id} lab:nominalPowerKw ?nomPower }}
            BIND(COALESCE(?maxPowerExplicit, ?nomPower) AS ?maxPower)

            OPTIONAL {{ lab:{device_id} lab:maxFlowTempC   ?maxFlow }}
            OPTIONAL {{ lab:{device_id} lab:minFlowTempC   ?minFlow }}
        }}
    """)
    if not rows:
        abort(404, f"Unknown device {device_id}")
    r = rows[0]
    return jsonify({
        "device_id": device_id,
        "min_cop":       float(r["minCOP"])   if "minCOP"   in r else None,
        "max_power_kw":  float(r["maxPower"]) if "maxPower" in r else None,
        "max_flow_c":    float(r["maxFlow"])  if "maxFlow"  in r else None,
        "min_flow_c":    float(r["minFlow"])  if "minFlow"  in r else None,
    })


@app.get("/device/<device_id>/components")
def components(device_id: str):
    rows = sparql_query(f"""
        SELECT ?c ?label ?type WHERE {{
            lab:{device_id} lab:hasComponent ?c .
            ?c a ?type ; rdfs:label ?label .
        }}
    """)
    return jsonify([
        {"id": r["c"].split("#")[-1], "label": r["label"], "type": r["type"].split("#")[-1]}
        for r in rows
    ])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
