"""Завантажує equipment.ttl у Fuseki (dataset `lab`).

Стійкий до повільного старту Fuseki: чекає до 120с готовності, потім
створює датасет (якщо потрібно) і завантажує триплети.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("load_ontology")

FUSEKI_URL = os.getenv("FUSEKI_URL", "http://localhost:3030/lab")
TTL_PATH = Path(__file__).parent / "equipment.ttl"
AUTH = ("admin", "admin")


def base_url() -> tuple[str, str]:
    parsed = urlparse(FUSEKI_URL)
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.strip("/") or "lab"


def wait_for_fuseki(base: str, timeout_sec: int = 120) -> None:
    log.info("Waiting for Fuseki at %s ...", base)
    for _ in range(timeout_sec):
        try:
            r = requests.get(f"{base}/$/ping", timeout=3)
            if r.ok:
                log.info("Fuseki is ready")
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise TimeoutError(f"Fuseki at {base} not ready after {timeout_sec}s")


def ensure_dataset(base: str, dataset: str) -> None:
    r = requests.get(f"{base}/$/datasets/{dataset}", auth=AUTH, timeout=10)
    if r.status_code == 200:
        log.info("Dataset '%s' already exists", dataset)
        return
    log.info("Dataset '%s' missing — creating", dataset)
    create = requests.post(
        f"{base}/$/datasets",
        params={"dbName": dataset, "dbType": "tdb2"},
        auth=AUTH,
        timeout=30,
    )
    if create.status_code not in (200, 201, 204):
        log.error("Create dataset failed: %s %s", create.status_code, create.text)
        sys.exit(1)
    log.info("Dataset '%s' created", dataset)


def upload() -> None:
    data = TTL_PATH.read_bytes()
    resp = requests.put(
        f"{FUSEKI_URL}/data?default",
        data=data,
        headers={"Content-Type": "text/turtle"},
        auth=AUTH,
        timeout=30,
    )
    if resp.status_code not in (200, 201, 204):
        log.error("Upload failed: %s %s", resp.status_code, resp.text)
        sys.exit(1)
    log.info("Ontology uploaded to %s (%d bytes)", FUSEKI_URL, len(data))


def main() -> None:
    base, dataset = base_url()
    wait_for_fuseki(base)
    ensure_dataset(base, dataset)
    upload()


if __name__ == "__main__":
    main()
