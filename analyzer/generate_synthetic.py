"""Генерація тренувального датасету «нормальної» роботи теплового насоса.

Тренує автоенкодер на типовій роботі ATW heat pump (Mitsubishi EcoDan-клас).
Жодних аномалій тут немає — модель вчиться відтворювати норму, а аномалії
detect-аться через високу reconstruction MSE на inference.

Що змінилось vs. попередня версія:
* 4 режими роботи (heating / dhw / cooling / standby) з реалістичними пропорціями
* добовий + сезонний цикл outdoor temp (а не один глобальний шум)
* COP як функція T_outdoor та T_flow згідно з паспортами PUHZ-W серії
* delta_T дотримується теплобалансу: Q_out = ρ·c·V·ΔT
* стохастика на верхньому рівні (від погоди), плюс легка вимірювальна похибка
* 20 000 зразків замість 5 000 — більше для надійного training/val split

Запуск:
    python analyzer/generate_synthetic.py            # пише training_data.csv
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("synthetic")

N_SAMPLES = 20_000
RNG = np.random.default_rng(42)
OUT = Path(__file__).parent / "training_data.csv"

# ─── Mode mix (відсотки кожного режиму у тренувальному датасеті) ───────────────
MODE_WEIGHTS = {
    "heating":  0.55,   # переважно опалювальний сезон
    "dhw":      0.20,   # ГВП кілька разів на день
    "standby":  0.20,   # вночі / у міжсезоння
    "cooling":  0.05,   # рідко, тільки реверс-моделі
}


def sample_time(n: int) -> np.ndarray:
    """Випадкові моменти у році, рівномірно. Повертає sec_of_year."""
    return RNG.uniform(0, 365 * 86400, n)


def outdoor_temp(t_sec_of_year: np.ndarray) -> np.ndarray:
    """Реалістичний outdoor: сезонний (±12°C), добовий (±4°C), шум (1°C)."""
    seasonal = -12 * np.cos(2 * np.pi * t_sec_of_year / (365 * 86400))
    daily    = -4  * np.cos(2 * np.pi * (t_sec_of_year % 86400) / 86400 - math.pi)
    noise    = RNG.normal(0, 1.0, len(t_sec_of_year))
    return 8.0 + seasonal + daily + noise  # річне середнє ~+8°C


def heating_rows(n: int) -> pd.DataFrame:
    """Опалення: flow ~ функція outdoor (weather compensation curve)."""
    t = sample_time(n)
    t_out = outdoor_temp(t)
    # WCC: чим холодніше надворі, тим вища температура подачі (35–55°C)
    flow = np.clip(45 - 0.6 * t_out + RNG.normal(0, 1.2, n), 30, 55)
    delta_t = np.clip(5 + RNG.normal(0, 0.6, n), 3.0, 8.0)
    ret = flow - delta_t
    # Потужність: чим вища ΔT * витрата, тим більше Q_out, тим більше P_in
    q_out = delta_t * 0.20 * 4.186  # kW thermal (12 L/min типу)
    # COP паспортний: ~3.0 при A2/W35, гірше при холодніше і вищому flow
    cop = np.clip(
        3.1 + 0.045 * (t_out - 2) - 0.030 * (flow - 35) + RNG.normal(0, 0.15, n),
        2.0, 5.0,
    )
    power = q_out / cop
    return pd.DataFrame({
        "mode": "heating",
        "power_kw": power.round(3),
        "delta_t_c": delta_t.round(2),
        "flow_temp_c": flow.round(1),
        "return_temp_c": ret.round(1),
        "outdoor_temp_c": t_out.round(1),
        "cop": cop.round(3),
    })


def dhw_rows(n: int) -> pd.DataFrame:
    """ГВП: вищий flow (48–60°C), нижчий COP, окремий цикл нагріву."""
    t = sample_time(n)
    t_out = outdoor_temp(t)
    flow = np.clip(50 + RNG.normal(0, 2.0, n), 45, 60)
    delta_t = np.clip(6 + RNG.normal(0, 0.7, n), 4.0, 9.0)
    ret = flow - delta_t
    q_out = delta_t * 0.18 * 4.186
    # COP ГВП помітно нижчий через високий flow_temp
    cop = np.clip(
        2.4 + 0.040 * (t_out - 2) - 0.020 * (flow - 50) + RNG.normal(0, 0.15, n),
        1.8, 3.8,
    )
    power = q_out / cop
    return pd.DataFrame({
        "mode": "dhw",
        "power_kw": power.round(3),
        "delta_t_c": delta_t.round(2),
        "flow_temp_c": flow.round(1),
        "return_temp_c": ret.round(1),
        "outdoor_temp_c": t_out.round(1),
        "cop": cop.round(3),
    })


def cooling_rows(n: int) -> pd.DataFrame:
    """Охолодження (реверс): low flow (7–18°C), середній COP."""
    t = sample_time(n)
    # охолодження тільки коли тепло надворі
    t_out = np.clip(outdoor_temp(t) + 8.0, 15.0, 35.0)
    flow = np.clip(12 + RNG.normal(0, 2.0, n), 7, 18)
    delta_t = np.clip(4 + RNG.normal(0, 0.5, n), 2.5, 7.0)
    ret = flow + delta_t  # реверс: ret вище ніж flow
    q_out = delta_t * 0.18 * 4.186
    cop = np.clip(
        3.8 - 0.040 * (t_out - 25) + RNG.normal(0, 0.15, n),
        2.5, 5.5,
    )
    power = q_out / cop
    return pd.DataFrame({
        "mode": "cooling",
        "power_kw": power.round(3),
        "delta_t_c": delta_t.round(2),
        "flow_temp_c": flow.round(1),
        "return_temp_c": ret.round(1),
        "outdoor_temp_c": t_out.round(1),
        "cop": cop.round(3),
    })


def standby_rows(n: int) -> pd.DataFrame:
    """Standby: насос майже не споживає, температури близькі."""
    t = sample_time(n)
    t_out = outdoor_temp(t)
    flow = np.clip(22 + RNG.normal(0, 1.0, n), 18, 28)
    delta_t = np.clip(RNG.normal(0.5, 0.3, n), 0.0, 1.5)
    ret = flow - delta_t
    power = np.clip(RNG.normal(0.08, 0.04, n), 0.02, 0.25)
    # У standby реальний COP не визначений, ставимо очікуваний baseline,
    # щоб модель не вибухала на нульових ділах. Поки RAGGED — fillна 3.0.
    cop = np.clip(RNG.normal(3.0, 0.2, n), 2.0, 4.0)
    return pd.DataFrame({
        "mode": "standby",
        "power_kw": power.round(3),
        "delta_t_c": delta_t.round(2),
        "flow_temp_c": flow.round(1),
        "return_temp_c": ret.round(1),
        "outdoor_temp_c": t_out.round(1),
        "cop": cop.round(3),
    })


def main() -> None:
    counts = {m: int(round(N_SAMPLES * w)) for m, w in MODE_WEIGHTS.items()}
    log.info("Generating mix: %s (total=%d)", counts, sum(counts.values()))

    frames = [
        heating_rows(counts["heating"]),
        dhw_rows(counts["dhw"]),
        cooling_rows(counts["cooling"]),
        standby_rows(counts["standby"]),
    ]
    df = pd.concat(frames, ignore_index=True)
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)  # перемішати

    df.to_csv(OUT, index=False)
    log.info("Wrote %d samples to %s", len(df), OUT)
    log.info("Per-mode counts:\n%s", df["mode"].value_counts().to_string())
    log.info("Numeric summary:\n%s",
             df.drop(columns=["mode"]).describe().round(2).to_string())


if __name__ == "__main__":
    main()
