# Диплом 1 — Система граничного моніторингу енергоспоживання

## Що це

Edge-вузол (Raspberry Pi / міні-ПК) біля обладнання збирає метрики з
теплового насоса Mitsubishi EcoDan через MELCloud API, публікує їх у
MQTT, зберігає в InfluxDB і виводить у Grafana.

## Компоненти

| Файл | Призначення |
|---|---|
| `collector.py` | Опитує MELCloud, публікує в `lab/equipment/{id}/metrics` |
| `mqtt_to_influx.py` | Підписується на MQTT, пише у InfluxDB |
| `grafana_dashboard.json` | Імпортувати в Grafana після підключення InfluxDB datasource |

## Запуск

```bash
pip install -r requirements.txt
python collector.py &        # тягне з EcoDan або синтетичні дані
python mqtt_to_influx.py &   # пише в InfluxDB
```

Потім у Grafana (http://localhost:3000, admin/admin):
1. Add data source → InfluxDB, URL `http://lab-influxdb:8086`, org `lab`,
   token `lab-dev-token`, bucket `metrics`, UID `influx-lab`.
2. Dashboards → Import → завантаж `grafana_dashboard.json`.

## Демо-режим без EcoDan

Залиш `MELCLOUD_USER=demo` у `.env` — collector публікуватиме правдоподібні
синтетичні метрики, щоб можна було розробляти дашборд і тестувати конвеєр.
