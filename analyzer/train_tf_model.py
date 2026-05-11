"""Тренування автоенкодера для виявлення аномалій (заміна IsolationForest).

Чому автоенкодер: нейромережа вчиться відтворювати "нормальну" роботу
теплового насоса. Аномалія = вхід, який модель не може добре відтворити,
тобто reconstruction MSE значно вища за поріг.

Вивід:
    anomaly_ae.keras       — повна модель (для розробки / переучування)
    anomaly_ae.tflite      — оптимізована модель для запуску на Raspberry Pi 3+
    anomaly_ae.npz         — голі numpy-ваги для Pi Zero (ARMv6, без TFLite)
    anomaly_ae.meta.json   — features, mean/std для нормалізації, поріг MSE

Запуск:
    pip install -r analyzer/requirements-tf-train.txt
    python analyzer/generate_synthetic.py    # якщо ще не згенеровано
    python analyzer/train_tf_model.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train_tf")

HERE = Path(__file__).parent
DATA = HERE / "training_data.csv"
MODEL_KERAS = HERE / "anomaly_ae.keras"
MODEL_TFLITE = HERE / "anomaly_ae.tflite"
MODEL_NPZ = HERE / "anomaly_ae.npz"
META_FILE = HERE / "anomaly_ae.meta.json"

FEATURES = ["power_kw", "delta_t_c", "flow_temp_c", "outdoor_temp_c", "cop"]
EPOCHS = 60
BATCH_SIZE = 64
THRESHOLD_PERCENTILE = 99.0  # MSE вище за цей перцентиль -> аномалія


def build_autoencoder(n_features: int) -> keras.Model:
    """Маленький автоенкодер 5→4→3→4→5. Достатньо для табличних метрик ТН."""
    inp = keras.Input(shape=(n_features,), name="metrics")
    x = keras.layers.Dense(4, activation="relu")(inp)
    bottleneck = keras.layers.Dense(3, activation="relu", name="bottleneck")(x)
    x = keras.layers.Dense(4, activation="relu")(bottleneck)
    out = keras.layers.Dense(n_features, activation="linear", name="reconstruction")(x)
    model = keras.Model(inp, out, name="hvac_autoencoder")
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mse")
    return model


def main() -> None:
    if not DATA.exists():
        raise SystemExit(
            "training_data.csv відсутній — запусти спочатку generate_synthetic.py"
        )
    df = pd.read_csv(DATA)
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        raise SystemExit(f"У training_data.csv відсутні колонки: {missing}")

    X = df[FEATURES].to_numpy(dtype=np.float32)
    log.info("Loaded %d rows × %d features", X.shape[0], X.shape[1])

    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Xn = (X - mean) / std

    tf.random.set_seed(42)
    model = build_autoencoder(len(FEATURES))
    model.summary(print_fn=log.info)

    model.fit(
        Xn, Xn,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_split=0.1,
        verbose=2,
        callbacks=[keras.callbacks.EarlyStopping(patience=8, restore_best_weights=True)],
    )

    reconstructed = model.predict(Xn, verbose=0)
    mse = np.mean((Xn - reconstructed) ** 2, axis=1)
    threshold = float(np.percentile(mse, THRESHOLD_PERCENTILE))
    anomaly_rate = float((mse > threshold).mean())
    log.info("MSE percentile %.1f → threshold=%.6f (in-sample anomaly rate %.2f%%)",
             THRESHOLD_PERCENTILE, threshold, anomaly_rate * 100)

    model.save(MODEL_KERAS)
    log.info("Saved Keras model → %s", MODEL_KERAS)

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_bytes = converter.convert()
    MODEL_TFLITE.write_bytes(tflite_bytes)
    log.info("Saved TFLite model → %s (%.1f KB)",
             MODEL_TFLITE, MODEL_TFLITE.stat().st_size / 1024)

    # Голі ваги для Pi Zero V1.3 (ARMv6, без NEON, tflite-runtime недоступний).
    # На Pi Zero завантажуються через numpy.load() і виконуються чистими
    # matmul-ами — ~5 KB загалом, мікросекунди на forward pass.
    weights = {}
    layer_idx = 0
    for layer in model.layers:
        ws = layer.get_weights()
        if not ws:
            continue
        # ws = [kernel, bias] для Dense. Ключі з нумерацією — щоб
        # послідовність шарів збереглась при np.load() (sorted-by-name).
        weights[f"layer{layer_idx:02d}_W"] = ws[0].astype(np.float32)
        weights[f"layer{layer_idx:02d}_b"] = ws[1].astype(np.float32)
        layer_idx += 1
    np.savez(
        MODEL_NPZ,
        mean=mean.astype(np.float32),
        std=std.astype(np.float32),
        threshold=np.float32(threshold),
        **weights,
    )
    log.info("Saved numpy weights → %s (%.1f KB, %d arrays)",
             MODEL_NPZ, MODEL_NPZ.stat().st_size / 1024, 3 + len(weights))

    META_FILE.write_text(json.dumps({
        "features": FEATURES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "mse_threshold": threshold,
        "mse_threshold_percentile": THRESHOLD_PERCENTILE,
        "epochs_trained": EPOCHS,
    }, indent=2))
    log.info("Saved metadata → %s", META_FILE)


if __name__ == "__main__":
    main()
