"""Diagnostic engine: anomaly codes → ontology-grounded explanations.

Чому окремий модуль:
* Pi-аналізатор (pi_zero_analyzer.py) має робити одну річ — детектити аномалії
  з метрик. Перевід "anomaly code → причина та рішення" — окрема відповідальність.
* На Pi немає Fuseki / SPARQL. Натомість Pi один раз на ~5 хв тягне
  /device/<id>/diagnostic-context (bounds + faults + error_codes) з ontology_api
  і тримає в RAM. Розмір — кілька кілобайт, безпечно для Pi Zero V1.3.
* diagnostic_mapping.json — окремий файл, який редагується інженерами,
  ОНТОЛОГ переписує без зміни Python-коду.

Алгоритм enrich():
  1. Для кожного anomaly_code знаходимо правила в mapping за code_prefix.
  2. Для кожного правила:
       a) серед закешованих FaultCase шукаємо ті, у яких affects-поле
          містить будь-який з affects_components (case-insensitive).
       b) серед ErrorCode беремо ті, чий code є в error_codes правила.
  3. Якщо нічого не знайдено — додаємо Diagnosis(kind="hint") з текстом правила.

Невпевненість:
* confidence = 1.0 — точний збіг по error code.
* confidence = 0.7 — fault матчиться по affects_components (евристика).
* confidence = 0.3 — тільки hint, без онтологічного джерела.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.schemas import Diagnosis  # noqa: E402

log = logging.getLogger("diagnose")

_DEFAULT_MAPPING_PATH = Path(__file__).parent / "diagnostic_mapping.json"


class DiagnosticEngine:
    """Кешує онтологічний контекст на пристрій і будує Diagnosis-список.

    Не thread-safe. У pi_zero_analyzer все одно serial-обробка MQTT повідомлень.
    """

    def __init__(
        self,
        ontology_api_url: str,
        mapping_path: Path = _DEFAULT_MAPPING_PATH,
        cache_ttl_sec: int = 300,
        max_diagnoses_per_alert: int = 6,
    ):
        self.api_url = ontology_api_url.rstrip("/")
        self.mapping_path = mapping_path
        self.cache_ttl = cache_ttl_sec
        self.max_diagnoses = max_diagnoses_per_alert

        self._cache: dict[str, dict] = {}        # device_id → context dict
        self._fetched_at: dict[str, float] = {}

        self.rules = self._load_mapping()
        log.info("DiagnosticEngine: loaded %d mapping rules from %s",
                 len(self.rules), self.mapping_path)

    # ── mapping ──────────────────────────────────────────────────────────

    def _load_mapping(self) -> list[dict]:
        if not self.mapping_path.exists():
            log.warning("Diagnostic mapping not found at %s — engine will only "
                        "emit empty diagnoses.", self.mapping_path)
            return []
        try:
            data = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.error("Bad JSON in %s: %s", self.mapping_path, e)
            return []
        return data.get("rules", [])

    def reload_mapping(self) -> int:
        """Перечитати mapping з диску без рестарту процесу. Повертає кількість правил."""
        self.rules = self._load_mapping()
        return len(self.rules)

    # ── ontology context cache ───────────────────────────────────────────

    def _is_fresh(self, device_id: str) -> bool:
        if device_id not in self._cache:
            return False
        return (time.time() - self._fetched_at.get(device_id, 0)) < self.cache_ttl

    def _fetch_context(self, device_id: str) -> dict | None:
        try:
            r = requests.get(
                f"{self.api_url}/device/{device_id}/diagnostic-context",
                timeout=3,
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("Failed to fetch diagnostic context for %s: %s",
                        device_id, exc)
            return None

    def get_context(self, device_id: str) -> dict:
        """Cached: {bounds, faults: [...], error_codes: [...]}."""
        if not self._is_fresh(device_id):
            fresh = self._fetch_context(device_id)
            if fresh is not None:
                self._cache[device_id] = fresh
                self._fetched_at[device_id] = time.time()
        return self._cache.get(device_id, {"bounds": {}, "faults": [], "error_codes": []})

    # ── core: enrich ─────────────────────────────────────────────────────

    @staticmethod
    def _code_prefix(anomaly_code: str) -> str:
        """`cop_below_nominal(2.72<2.80)` → `cop_below_nominal`."""
        return anomaly_code.split("(", 1)[0].strip()

    def _match_rule(self, anomaly_code: str) -> dict | None:
        prefix = self._code_prefix(anomaly_code)
        for rule in self.rules:
            if rule.get("code_prefix") == prefix:
                return rule
        return None

    @staticmethod
    def _affects_match(affects: str | None, keywords: list[str]) -> bool:
        if not affects:
            return False
        a = affects.lower()
        return any(k.lower() in a for k in keywords)

    def _diagnose_one_code(
        self, anomaly_code: str, ctx: dict,
    ) -> list[Diagnosis]:
        rule = self._match_rule(anomaly_code)
        if rule is None:
            return []
        out: list[Diagnosis] = []

        # — match faults by affects_component substring —
        affects_keywords = rule.get("affects_components", [])
        if affects_keywords:
            for f in ctx.get("faults", []):
                if self._affects_match(f.get("affects"), affects_keywords):
                    out.append(Diagnosis(
                        matched_code=anomaly_code,
                        kind="fault",
                        cause=f.get("cause"),
                        solution=f.get("solution"),
                        fault_iri=f.get("id"),
                        fault_symptom=f.get("symptom"),
                        affects_component=f.get("affects"),
                        severity=f.get("severity"),
                        confidence=0.7,
                    ))

        # — match error codes by codeId —
        wanted_codes = set(rule.get("error_codes", []))
        if wanted_codes:
            for ec in ctx.get("error_codes", []):
                if ec.get("code") in wanted_codes:
                    out.append(Diagnosis(
                        matched_code=anomaly_code,
                        kind="error_code",
                        error_code=ec.get("code"),
                        error_description=ec.get("description"),
                        error_action=ec.get("action"),
                        severity=ec.get("severity"),
                        confidence=1.0,
                    ))

        # — fallback hint if nothing matched —
        hint = rule.get("hint")
        if not out and hint:
            out.append(Diagnosis(
                matched_code=anomaly_code,
                kind="hint",
                hint=hint,
                confidence=0.3,
            ))
        return out

    def enrich(self, anomaly_codes: list[str], device_id: str) -> list[Diagnosis]:
        """Головний публічний метод. Повертає до max_diagnoses_per_alert діагнозів,
        відсортованих за впевненістю (спершу error_codes, потім faults, потім hints)."""
        if not anomaly_codes:
            return []
        ctx = self.get_context(device_id)
        if not ctx.get("faults") and not ctx.get("error_codes"):
            log.debug("No ontology context for %s — diagnoses will be hints only",
                      device_id)

        all_diag: list[Diagnosis] = []
        for code in anomaly_codes:
            all_diag.extend(self._diagnose_one_code(code, ctx))

        # Dedupe by (kind, fault_iri or error_code) — multiple anomaly codes
        # may match the same ontology item; keep highest-confidence first hit.
        seen: set[tuple[str, str]] = set()
        deduped: list[Diagnosis] = []
        for d in sorted(all_diag, key=lambda x: -x.confidence):
            key = (d.kind, d.fault_iri or d.error_code or d.hint or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(d)

        return deduped[: self.max_diagnoses]

    # ── pretty logging helper ────────────────────────────────────────────

    @staticmethod
    def format_for_log(diag: Diagnosis) -> str:
        """Однорядковий лог-рядок: '[L1?] severity=high — Опис — Дія'."""
        if diag.kind == "error_code":
            sev = f" sev={diag.severity}" if diag.severity else ""
            return f"[{diag.error_code}?{sev}] {diag.error_description or ''} → {diag.error_action or ''}"
        if diag.kind == "fault":
            sev = f" sev={diag.severity}" if diag.severity else ""
            return f"[fault:{diag.affects_component or '?'}{sev}] {diag.cause or ''} → {diag.solution or ''}"
        return f"[hint] {diag.hint or ''}"
