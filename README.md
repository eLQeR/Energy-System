# Cyber-Energy Lab — спільна edge-система (3 дипломи)

Монорепозиторій трьох дипломних робіт, які разом утворюють одну працюючу
систему. **Все крутиться в Docker** — на хості потрібен тільки `docker`.

📐 **Архітектура з діаграмами:** [ARCHITECTURE.md](ARCHITECTURE.md)

## Структура

```
.
├── Dockerfile                  Один образ для всіх Python-сервісів
├── docker-compose.yml          Інфраструктура + 5 Python-сервісів
├── .env.example                Копіюй у .env для реальних кредів MELCloud
├── shared/                     Спільний контракт (JSON-схеми, топіки)
├── monitoring/                 ДИПЛОМ 1 — збір метрик EcoDan + дашборд
├── ontology/                   ДИПЛОМ 2 — онтологія + SPARQL API + LLM-парсер
├── analyzer/                   ДИПЛОМ 3 — edge-аналіз стану обладнання
└── scripts/                    start_all.sh / stop_all.sh
```

## Швидкий старт

```bash
cp .env.example .env          # можна не редагувати — demo-режим

bash scripts/start_all.sh     # або: docker compose up -d --build
```

Перша збірка тягне Python-залежності і навчає ML-модель (~2-3 хв).
Наступні запуски — миттєві.

Відкрий у браузері:

| URL | Що це |
|---|---|
| http://localhost:3000 | **Grafana** (admin/admin) — дашборди Диплому 1 |
| http://localhost:5001 | **Edge-панель** Диплому 3 — поточний стан обладнання |
| http://localhost:5002/devices | **Ontology API** Диплому 2 — перелік пристроїв |
| http://localhost:3030 | Fuseki (admin/admin) — SPARQL-endpoint |
| http://localhost:8087 | InfluxDB UI (admin/adminadmin) |

## Сервіси в docker-compose

| Сервіс | Диплом | Що робить |
|---|---|---|
| `mosquitto` | — | MQTT-брокер (шина повідомлень) |
| `influxdb` | 1 | БД часових рядів |
| `fuseki` | 2 | Triple store + SPARQL endpoint |
| `grafana` | 1 | Дашборд |
| `collector` | 1 | MELCloud/synthetic → MQTT |
| `mqtt_bridge` | 1 | MQTT → InfluxDB |
| `ontology_loader` | 2 | Одноразовий — вантажить `equipment.ttl` у Fuseki |
| `ontology_api` | 2 | Flask над SPARQL (:5000) |
| `analyzer_panel` | 3 | Edge-analyzer (ML + правила) + веб-панель (:5001) |

## Логи та діагностика

```bash
docker compose logs -f collector           # збір з EcoDan
docker compose logs -f analyzer_panel      # вердикти ML
docker compose logs -f ontology_api        # SPARQL-запити
docker compose ps                          # хто живий
```

## Grafana — перший запуск

1. http://localhost:3000 → admin/admin → пропустити зміну пароля.
2. Add data source → InfluxDB:
   - URL: `http://influxdb:8086`
   - Query language: `Flux`
   - Organization: `lab`
   - Token: `lab-dev-token`
   - Default bucket: `metrics`
   - **UID: `influx-lab`** (важливо — використовується в дашборді)
3. Dashboards → Import → завантаж [monitoring/grafana_dashboard.json](monitoring/grafana_dashboard.json).

## Спільний контракт повідомлень

Топіки MQTT:
- `lab/equipment/{device_id}/metrics` — сирі метрики (публікує collector)
- `lab/equipment/{device_id}/state` — оцінка стану (публікує analyzer)

Формати — див. [shared/schemas.py](shared/schemas.py).

## Розподіл відповідальності

| Диплом | Тема | Що робить | Читає від інших | Віддає іншим |
|---|---|---|---|---|
| 1 | Моніторинг енергоспоживання | Збирає метрики з EcoDan, пише в InfluxDB, показує в Grafana | стан з `lab/.../state` (для підсвічування аномалій) | сирі метрики в `lab/.../metrics` |
| 2 | Онтологія + ШІ | Зберігає знання про обладнання, парсить паспорти через LLM | — | SPARQL + HTTP API зі специфікаціями |
| 3 | Edge-аналіз стану | Читає метрики, запитує очікувані значення в онтології, запускає ML, публікує стан | метрики Диплому 1, специфікації Диплому 2 | стан у `lab/.../state` |

## Зупинка і очищення

```bash
bash scripts/stop_all.sh          # контейнери down, дані лишаються
docker compose down -v            # ВСЕ разом з томами (InfluxDB/Fuseki/Grafana — чисто)
```
