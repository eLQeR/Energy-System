from __future__ import annotations

import logging
from pathlib import Path

import joblib
import pandas as pd
#IsolationForest шукає аномалії, ізолюючи “дивні” точки швидше, ніж нормальні
from sklearn.ensemble import IsolationForest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train")

HERE = Path(__file__).parent
DATA = HERE / "training_data.csv"
MODEL = HERE / "anomaly_model.pkl"

FEATURES = ["power_kw", "delta_t_c", "flow_temp_c", "outdoor_temp_c", "cop"]


def main() -> None:
    if not DATA.exists():
        raise SystemExit("training_data.csv відсутній — запусти generate_synthetic.py")
    df = pd.read_csv(DATA)
    log.info("Loaded %d rows, features=%s", len(df), FEATURES)

    model = IsolationForest(n_estimators=200, contamination=0.05, random_state=42)
    model.fit(df[FEATURES])

    joblib.dump({"model": model, "features": FEATURES}, MODEL)
    log.info("Saved model → %s", MODEL)

    preds = model.predict(df[FEATURES])
    anomaly_rate = (preds == -1).mean()
    log.info("In-sample anomaly rate: %.2f%% (очікуємо ~contamination)", anomaly_rate * 100)


if __name__ == "__main__":
    main()
