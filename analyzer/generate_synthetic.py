from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("synthetic")

N_SAMPLES = 5000 #буде створено 5000 штучних данних
RNG = np.random.default_rng(42)
OUT = Path(__file__).parent / "training_data.csv"


def main() -> None:
    outdoor = RNG.normal(5, 5, N_SAMPLES) #значення температури, середнє число 5, розкид +-5
    #температура подачі
    flow = np.clip(40 + 0.4 * (5 - outdoor) + RNG.normal(0, 1.5, N_SAMPLES), 25, 55)
    #pзворотна температура
    ret = flow - np.clip(6 + RNG.normal(0, 0.8, N_SAMPLES), 3, 10)
    #потужність
    power = np.clip(
        1.5 + 0.15 * (flow - 20) + 0.05 * (5 - outdoor) + RNG.normal(0, 0.2, N_SAMPLES),
        0.8, 3.8,
    )
    #вироблене тепло
    heat_output = (flow - ret) * 0.2 * 1.16
    cop = np.clip(heat_output / power, 2.5, 5.2)

    df = pd.DataFrame({
        "power_kw": power.round(3),
        "delta_t_c": (flow - ret).round(2),
        "flow_temp_c": flow.round(1),
        "outdoor_temp_c": outdoor.round(1),
        "cop": cop.round(3),
    })
    df.to_csv(OUT, index=False)
    log.info("Wrote %d samples to %s", len(df), OUT)
    log.info("Summary:\n%s", df.describe().round(2))


if __name__ == "__main__":
    main()
