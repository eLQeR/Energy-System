# Диплом 1 — Система граничного моніторингу енергоспоживання

## Що це

Edge-вузол (Raspberry Pi) біля обладнання збирає метрики з теплового
насоса напряму, публікує їх у MQTT. Сервер приймає їх у MQTT, зберігає
в InfluxDB і виводить у Grafana.

## Компоненти

| Файл | Призначення |
|---|---|
| `mqtt_to_influx.py` | Підписується на MQTT, пише у InfluxDB |
| `synthetic_publisher.py` | Генерує синтетичні метрики у MQTT — для демо/розробки без живого Pi |
| `grafana_dashboard.json` | Імпортувати в Grafana після підключення InfluxDB datasource |

## Запуск

```bash
pip install -r requirements.txt
python synthetic_publisher.py --output mqtt &  # синтетика (опціонально)
python mqtt_to_influx.py &                     # пише в InfluxDB
```

Потім у Grafana (http://localhost:3000, admin/admin):
1. Add data source → InfluxDB, URL `http://lab-influxdb:8086`, org `lab`,
   token `lab-dev-token`, bucket `metrics`, UID `influx-lab`.
2. Dashboards → Import → завантаж `grafana_dashboard.json`.

## Демо-режим без Raspberry Pi

`synthetic_publisher.py` генерує правдоподібні метрики і публікує їх у те
саме MQTT-топік, що й Pi. Це дозволяє розробляти дашборди й тестувати
конвеєр без живого обладнання. У `docker-compose.yml` він стартує за
замовчуванням.
