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
#
# На Pi OS Bookworm pip забороняє системний install (PEP 668), тому
# скрипт автоматично створює venv у ./.venv і всі команди використовують
# python/pip саме з нього. Працює і на старіших ОС без PEP 668.

set -euo pipefail
cd "$(dirname "$0")/.."

VENV_DIR=".venv"
VENV_PY="$VENV_DIR/bin/python"

echo "[1/4] Перевірка ваг моделі…"
if [[ -f analyzer/anomaly_ae_strict.npz ]]; then
    AE_FILE=analyzer/anomaly_ae_strict.npz
elif [[ -f analyzer/anomaly_ae.npz ]]; then
    AE_FILE=analyzer/anomaly_ae.npz
else
    echo "  ✗ analyzer/anomaly_ae*.npz відсутній."
    echo "    На dev-машині запусти:"
    echo "      pip install -r analyzer/requirements-tf-train.txt"
    echo "      python3 analyzer/generate_synthetic.py"
    echo "      python3 analyzer/train_tf_model.py"
    echo "    Потім закомміть anomaly_ae.{npz,meta.json} у git і git pull тут."
    exit 1
fi
echo "  ✓ $(du -h "$AE_FILE" | cut -f1) $AE_FILE"

echo "[2/4] Venv + залежності…"
if [[ ! -x "$VENV_PY" ]]; then
    echo "  створюю venv у $VENV_DIR/"
    python3 -m venv "$VENV_DIR" || {
        echo "  ✗ python3 -m venv впав — встановіть: sudo apt install -y python3-venv"
        exit 1
    }
fi

# Перевіряємо чи всі модулі доступні у venv. Якщо ні — інсталюємо.
if ! "$VENV_PY" -c "import numpy, paho.mqtt.client, requests, pydantic, dotenv" 2>/dev/null; then
    echo "  встановлюю залежності у venv…"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install -r analyzer/requirements-pi-zero.txt
else
    echo "  ✓ всі модулі вже встановлені у venv"
fi

echo "[3/4] Параметри підключення:"
echo "  MQTT_BROKER  = ${MQTT_BROKER:-localhost}"
echo "  ONTOLOGY_API = ${ONTOLOGY_API:-http://localhost:5000}"
echo "  ALERTS_API   = ${ALERTS_API:-http://localhost:5003}"

echo "[4/4] Старт pi_zero_analyzer.py через $VENV_PY…"
exec "$VENV_PY" analyzer/pi_zero_analyzer.py
