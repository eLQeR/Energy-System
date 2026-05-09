from __future__ import annotations

import logging
import os

from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

import analyzer  

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("web_panel")

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

if os.getenv("PANEL_START_ANALYZER", "1") == "1":
    analyzer.start(block=False)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    with analyzer.STATE_LOCK:
        devices = [
            {
                "device_id": s.device_id,
                "updated_at": s.updated_at,
                "metrics": s.last_metrics,
                "state": s.last_state,
            }
            for s in analyzer.STATE_CACHE.values()
        ]
    return jsonify({"devices": devices})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
