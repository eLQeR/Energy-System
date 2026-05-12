"""ДИПЛОМ 2 — HTTP API над SPARQL-endpoint.

Інші дипломи звертаються сюди, щоб отримати характеристики та очікувані
режими обладнання. Всі маршрути повертають JSON.

Запуск:
    python ontology_api.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from SPARQLWrapper import JSON, SPARQLWrapper

# Make sibling modules (pdf_to_ontology, load_ontology) importable regardless
# of whether this file is run as a script or via `python -m`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import load_ontology  # noqa: E402
from pdf_to_ontology.extract import extract_pages  # noqa: E402
from pdf_to_ontology.filter import filter_relevant  # noqa: E402
from pdf_to_ontology.llm import extract_profile  # noqa: E402
from pdf_to_ontology.turtle import to_turtle  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ontology_api")

FUSEKI_URL = os.getenv("FUSEKI_URL", "http://localhost:3030/lab")
TTL_PATH = Path(__file__).resolve().parent / "equipment.ttl"
DEVICE_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB

PREFIX = """
PREFIX lab:  <http://lab.example/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
"""

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES


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
    # rdfs:label необов'язковий — якщо LLM-конвеєр додав пристрій без
    # label, ми все одно його повертаємо (UI використає id як fallback).
    rows = sparql_query("""
        SELECT ?device ?label ?model WHERE {
            ?device a/rdfs:subClassOf* lab:Equipment .
            OPTIONAL { ?device rdfs:label ?label }
            OPTIONAL { ?device lab:model ?model }
        }
    """)
    return jsonify([
        {
            "id": r["device"].split("#")[-1],
            "label": r.get("label") or r["device"].split("#")[-1],
            "model": r.get("model"),
        }
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


@app.get("/device/<device_id>/faults")
def device_faults(device_id: str):
    """Випадки несправностей з онтології."""
    rows = sparql_query(f"""
        SELECT ?f ?label ?symptom ?cause ?solution ?severity ?affects ?affectsName
        WHERE {{
            lab:{device_id} lab:hasFaultCase ?f .
            OPTIONAL {{ ?f rdfs:label              ?label }}
            OPTIONAL {{ ?f lab:faultSymptom        ?symptom }}
            OPTIONAL {{ ?f lab:possibleCause       ?cause }}
            OPTIONAL {{ ?f lab:solution            ?solution }}
            OPTIONAL {{ ?f lab:severity            ?severity }}
            OPTIONAL {{ ?f lab:affectsComponent    ?affects }}
            OPTIONAL {{ ?f lab:affectsComponentName ?affectsName }}
        }}
        ORDER BY ?f
    """)
    return jsonify([
        {
            "id":        r["f"].split("#")[-1],
            "label":     r.get("label"),
            "symptom":   r.get("symptom"),
            "cause":     r.get("cause"),
            "solution":  r.get("solution"),
            "severity":  r.get("severity"),
            "affects":   r["affects"].split("#")[-1] if "affects" in r else r.get("affectsName"),
        }
        for r in rows
    ])


@app.get("/device/<device_id>/error-codes")
def device_error_codes(device_id: str):
    """Коди помилок контролера з онтології."""
    rows = sparql_query(f"""
        SELECT ?e ?label ?code ?desc ?action ?severity WHERE {{
            lab:{device_id} lab:hasErrorCode ?e .
            OPTIONAL {{ ?e rdfs:label           ?label }}
            OPTIONAL {{ ?e lab:codeId           ?code }}
            OPTIONAL {{ ?e lab:errorDescription ?desc }}
            OPTIONAL {{ ?e lab:errorAction      ?action }}
            OPTIONAL {{ ?e lab:severity         ?severity }}
        }}
        ORDER BY ?code
    """)
    return jsonify([
        {
            "id":          r["e"].split("#")[-1],
            "label":       r.get("label"),
            "code":        r.get("code"),
            "description": r.get("desc"),
            "action":      r.get("action"),
            "severity":    r.get("severity"),
        }
        for r in rows
    ])


@app.get("/device/<device_id>/maintenance")
def device_maintenance(device_id: str):
    """Сервісні деталі з регламентом ТО."""
    rows = sparql_query(f"""
        SELECT ?m ?label ?ry ?rh ?cy ?failure WHERE {{
            lab:{device_id} lab:hasMaintenancePart ?m .
            OPTIONAL {{ ?m rdfs:label             ?label }}
            OPTIONAL {{ ?m lab:replaceEveryYears  ?ry }}
            OPTIONAL {{ ?m lab:replaceEveryHours  ?rh }}
            OPTIONAL {{ ?m lab:checkEveryYears    ?cy }}
            OPTIONAL {{ ?m lab:typicalFailure     ?failure }}
        }}
        ORDER BY ?m
    """)
    return jsonify([
        {
            "id":                  r["m"].split("#")[-1],
            "label":               r.get("label"),
            "replace_every_years": int(r["ry"]) if "ry" in r else None,
            "replace_every_hours": int(r["rh"]) if "rh" in r else None,
            "check_every_years":   int(r["cy"]) if "cy" in r else None,
            "typical_failure":     r.get("failure"),
        }
        for r in rows
    ])


@app.get("/device/<device_id>/diagnostic-context")
def diagnostic_context(device_id: str):
    """Бандл для edge-аналізатора: межі + несправності + коди помилок.

    Pi-аналізатор кешує цю відповідь на BOUNDS_TTL_SEC і використовує її,
    щоб збагачувати alerts причиною/рішенням/кодом контролера без додаткових
    HTTP-запитів. Один endpoint замість трьох — менше round-trip часу.
    """
    bounds_rows = sparql_query(f"""
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
    bounds: dict = {}
    if bounds_rows:
        r = bounds_rows[0]
        bounds = {
            "min_cop":      float(r["minCOP"])   if "minCOP"   in r else None,
            "max_power_kw": float(r["maxPower"]) if "maxPower" in r else None,
            "max_flow_c":   float(r["maxFlow"])  if "maxFlow"  in r else None,
            "min_flow_c":   float(r["minFlow"])  if "minFlow"  in r else None,
        }

    return jsonify({
        "device_id":   device_id,
        "bounds":      bounds,
        "faults":      device_faults(device_id).json,
        "error_codes": device_error_codes(device_id).json,
    })


def _local_name(uri: str) -> str:
    return uri.split("#")[-1].split("/")[-1]


@app.get("/graph")
def ontology_graph():
    """Return the ontology as a node/edge graph for visualization.

    Optional ?device=<id> restricts the graph to one device and the resources
    it transitively references. Literal values (specs) become leaf nodes so
    engineers can see the full spec sheet inline.
    """
    device = (request.args.get("device") or "").strip()

    if device and not DEVICE_ID_RE.match(device):
        abort(400, "Invalid device id")

    if device:
        rows = sparql_query(f"""
            SELECT ?s ?p ?o ?sl ?ol ?st ?ot WHERE {{
                lab:{device} (lab:hasComponent|^lab:hasComponent|!lab:hasComponent)* ?s .
                ?s ?p ?o .
                OPTIONAL {{ ?s rdfs:label ?sl }}
                OPTIONAL {{ ?o rdfs:label ?ol }}
                OPTIONAL {{ ?s a ?st }}
                OPTIONAL {{ ?o a ?ot }}
            }}
        """)
    else:
        rows = sparql_query("""
            SELECT ?s ?p ?o ?sl ?ol ?st ?ot WHERE {
                ?s ?p ?o .
                OPTIONAL { ?s rdfs:label ?sl }
                OPTIONAL { ?o rdfs:label ?ol }
                OPTIONAL { ?s a ?st }
                OPTIONAL { ?o a ?ot }
                FILTER(STRSTARTS(STR(?s), "http://lab.example/ontology#"))
            }
        """)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    literal_counter = 0

    SKIP_PREDICATES = {"type"}  # rdf:type goes into node category, not edges

    for r in rows:
        s_uri = r.get("s", "")
        p_uri = r.get("p", "")
        o_uri = r.get("o", "")
        if not s_uri or not p_uri:
            continue

        s_name = _local_name(s_uri)
        p_name = _local_name(p_uri)
        s_type = _local_name(r.get("st", "")) if r.get("st") else ""
        s_label = r.get("sl") or s_name

        if s_name not in nodes:
            nodes[s_name] = {
                "id": s_name, "label": s_label,
                "group": s_type or "Resource",
                "kind": "resource",
            }

        # Literal object — render as a small leaf node
        is_literal = not o_uri.startswith("http")
        if is_literal:
            if p_name in SKIP_PREDICATES:
                continue
            literal_counter += 1
            leaf_id = f"_lit_{s_name}_{p_name}_{literal_counter}"
            nodes[leaf_id] = {
                "id": leaf_id, "label": str(o_uri),
                "group": "Literal",
                "kind": "literal",
            }
            edges.append({"from": s_name, "to": leaf_id, "label": p_name})
            continue

        o_name = _local_name(o_uri)
        o_type = _local_name(r.get("ot", "")) if r.get("ot") else ""
        o_label = r.get("ol") or o_name

        if p_name == "type":
            # set category and skip edge
            if o_name and o_name != "NamedIndividual":
                nodes[s_name]["group"] = o_name
            continue

        if o_name not in nodes:
            nodes[o_name] = {
                "id": o_name, "label": o_label,
                "group": o_type or "Resource",
                "kind": "resource",
            }
        edges.append({"from": s_name, "to": o_name, "label": p_name})

    return jsonify({"nodes": list(nodes.values()), "edges": edges})


@app.post("/upload")
def upload_pdf():
    """Multipart upload endpoint. The engineer-panel proxies here over HTTP;
    no HTML form lives in this service anymore."""
    file = request.files.get("pdf")
    device_id = (request.form.get("device_id") or "").strip()
    model = (request.form.get("model") or "claude-opus-4-7").strip()

    if not file or file.filename == "":
        return jsonify({"error": "Не передано PDF-файл"}), 400
    if not device_id or not DEVICE_ID_RE.match(device_id):
        return jsonify({
            "error": "Некоректний device_id (літери/цифри/_/-, перша — літера)"
        }), 400
    if not (file.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "Файл має бути .pdf"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        pdf_path = Path(tmp.name)

    try:
        log.info("Processing upload: %s → device_id=%s model=%s",
                 file.filename, device_id, model)
        pages = extract_pages(pdf_path)
        relevant = filter_relevant(pages, max_pages=30)
        text = "\n\n".join(f"=== Page {i + 1} ===\n{t}" for i, t in relevant)
        log.info("Filtered %d/%d pages, %d chars → LLM",
                 len(relevant), len(pages), len(text))

        profile = extract_profile(text, model=model)
        ttl_fragment = to_turtle(device_id, profile)

        header = f"\n\n# === auto-extracted from upload: {device_id} ===\n"
        with TTL_PATH.open("a", encoding="utf-8") as f:
            f.write(header)
            f.write(ttl_fragment)

        load_ontology.upload()
        log.info("Reloaded Fuseki after appending %s", device_id)

        return jsonify({
            "device_id": device_id,
            "profile": profile.model_dump(),
            "turtle": ttl_fragment,
        })
    except Exception as exc:
        log.exception("Upload pipeline failed")
        return jsonify({"error": f"Помилка обробки: {exc}"}), 500
    finally:
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
