#!/usr/bin/env bash
# Один скрипт для Raspberry Pi Zero V1.3 — підняти edge-аналізатор
# з натренованим автоенкодером.
#
# Передумова: на dev-машині запущено повний стек (MQTT, ontology_api,
# alerts_server). Pi Zero дотягується по локальній мережі.
#
# Перший запуск на Pi:
#   git clone https://github.com/<user>/Energy-System-fresh.git
#   cd Energy-System-fresh
#   MQTT_BROKER=192.168.1.10 \
#   ONTOLOGY_API=http://192.168.1.10:5002 \
#   ALERTS_API=http://192.168.1.10:5003 \
#       ./scripts/pi-zero-bootstrap.sh
#
# Подальші запуски: просто `./scripts/pi-zero-bootstrap.sh`.
# Оновлення моделі/коду:
#   git pull && ./scripts/pi-zero-bootstrap.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/4] Перевірка ваг моделі…"
if [[ ! -f analyzer/anomaly_ae.npz ]]; then
    echo "  ✗ analyzer/anomaly_ae.npz відсутній."
    echo "    На dev-машині запусти:"
    echo "      pip install -r analyzer/requirements-tf-train.txt"
    echo "      python3 analyzer/generate_synthetic.py"
    echo "      python3 analyzer/train_tf_model.py"
    echo "    Потім закомміть anomaly_ae.{npz,meta.json} у git і git pull тут."
    exit 1
fi
echo "  ✓ $(du -h analyzer/anomaly_ae.npz | cut -f1) anomaly_ae.npz"

echo "[2/4] Залежності (один раз)…"
if ! python3 -c "import numpy, paho.mqtt.client, requests, pydantic, dotenv" 2>/dev/null; then
    pip install --user -r analyzer/requirements-pi-zero.txt
else
    echo "  ✓ всі модулі вже встановлені"
fi

echo "[3/4] Параметри підключення:"
echo "  MQTT_BROKER  = ${MQTT_BROKER:-localhost}"
echo "  ONTOLOGY_API = ${ONTOLOGY_API:-http://localhost:5000}"
echo "  ALERTS_API   = ${ALERTS_API:-http://localhost:5003}"

echo "[4/4] Старт pi_zero_analyzer.py…"
exec python3 analyzer/pi_zero_analyzer.py
