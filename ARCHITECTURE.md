# Архітектура системи

## Загальна схема

```mermaid
flowchart TB
    subgraph EQ["🏭 Обладнання лабораторії"]
        ecodan["Mitsubishi EcoDan<br/>(тепловий насос)"]
        other["Інше обладнання<br/>(розширення)"]
    end

    subgraph BUS["📡 MQTT шина (Mosquitto)"]
        tM["lab/equipment/+/metrics"]
        tS["lab/equipment/+/state"]
    end

    subgraph D1["📊 Диплом 1 — Моніторинг енергоспоживання"]
        col["collector.py<br/>MELCloud → MQTT"]
        br["mqtt_to_influx.py<br/>міст"]
        influx[("InfluxDB<br/>часові ряди")]
        graf["Grafana<br/>дашборд :3000"]
    end

    subgraph D2["🧠 Диплом 2 — Онтологія + ШІ"]
        ttl["equipment.ttl<br/>OWL/Turtle"]
        fus[("Fuseki<br/>triple store")]
        api["ontology_api.py<br/>Flask + SPARQL :5002"]
        llm["llm_parser.py<br/>PDF паспорт → Turtle"]
    end

    subgraph D3["⚡ Диплом 3 — Edge-аналіз стану"]
        anl["analyzer.py<br/>IsolationForest + правила"]
        mdl[("anomaly_model.pkl")]
        panel["web_panel.py<br/>веб-панель :5001"]
    end

    ecodan -->|MELCloud API| col
    other -.->|Modbus/датчики| col
    col -->|publish| tM
    tM -->|subscribe| br
    tM -->|subscribe| anl
    br -->|write| influx
    influx -->|Flux query| graf

    ttl -->|load once| fus
    llm -.->|нові триплети| ttl
    fus -->|SPARQL| api
    api -->|"GET /expected-bounds<br/>(межі для детектора)"| anl

    mdl -->|"load на старті"| anl
    anl -->|publish| tS
    tS -->|subscribe| panel

    user(("👤 Оператор"))
    graf --> user
    panel --> user
    api -. JSON .-> user

    classDef d1 fill:#1f6feb33,stroke:#1f6feb
    classDef d2 fill:#8957e533,stroke:#8957e5
    classDef d3 fill:#3fb95033,stroke:#3fb950
    classDef bus fill:#e3b34133,stroke:#e3b341
    classDef eq  fill:#f8514933,stroke:#f85149
    class col,br,influx,graf d1
    class ttl,fus,api,llm d2
    class anl,mdl,panel d3
    class tM,tS bus
    class ecodan,other eq
```

## Потік одного циклу (sequence)

```mermaid
sequenceDiagram
    autonumber
    participant HP as EcoDan
    participant C as collector<br/>(Диплом 1)
    participant M as Mosquitto
    participant B as mqtt_bridge<br/>(Диплом 1)
    participant I as InfluxDB
    participant A as analyzer<br/>(Диплом 3)
    participant O as ontology_api<br/>(Диплом 2)
    participant P as web_panel<br/>(Диплом 3)
    participant G as Grafana

    loop кожні POLL_INTERVAL_SEC
        C->>HP: MELCloud /Device/Get
        HP-->>C: metrics (power, flow, COP, ...)
        C->>M: publish lab/.../metrics (JSON)
        par паралельно
            M->>B: metrics
            B->>I: write point
        and
            M->>A: metrics
            A->>O: GET /device/{id}/expected-bounds
            O-->>A: {min_cop, max_power, ...}
            A->>A: IsolationForest.predict<br/>+ правила з онтології
            A->>M: publish lab/.../state
            M->>P: state (для UI)
        end
    end

    G->>I: Flux query (кожні 10с)
    I-->>G: часові ряди
```

## Спільний контракт

Усі три підсистеми узгоджують формати у [shared/schemas.py](shared/schemas.py).
Без цього контракту жодна інтеграція між дипломами не працює.

```
Topic: lab/equipment/{device_id}/metrics        ← публікує collector
{
  "device_id": "ecodan_01",
  "timestamp": "2026-04-20T14:30:00Z",
  "metrics": {
    "power_kw": 2.4, "energy_kwh": 15.7,
    "flow_temp_c": 45.2, "return_temp_c": 38.1,
    "outdoor_temp_c": 5.0, "cop": 3.8, "mode": "heating"
  }
}

Topic: lab/equipment/{device_id}/state          ← публікує analyzer
{
  "device_id": "ecodan_01",
  "timestamp": "2026-04-20T14:30:01Z",
  "state": "normal" | "warning" | "anomaly",
  "anomalies": ["cop_below_nominal(2.5<2.8)", "ml_outlier"],
  "confidence": 0.87,
  "explanation": "ML score=-0.12; rules matched: 1"
}
```

## Розподіл відповідальності між дипломами

| Диплом | Роль | Фізичний рівень | Логічний рівень | Результат захисту |
|---|---|---|---|---|
| **1** Моніторинг | Збирач даних | Edge-вузол біля обладнання | Транспорт + візуалізація енергоспоживання | Дашборд Grafana в реальному часі |
| **2** Онтологія + ШІ | Довідник/мозок | Triple store | Семантика: що означають метрики, які межі, які режими | Онтологія + API + автопарсер паспортів |
| **3** Edge-аналіз | Діагност | Edge-вузол | Гібридне рішення: ML-модель + правила з онтології | Веб-панель стану + порівняння edge vs cloud |

Кожна робота захищається самостійно, але разом демонструє повний стек:
**сенсор → передача → зберігання → семантика → аналіз → рішення оператору**.

## Edge-принцип (для захисту Диплому 3)

```mermaid
flowchart LR
    subgraph EDGE["Edge-вузол (Raspberry Pi біля обладнання)"]
        direction TB
        e1[Збір даних]
        e2[ML + правила]
        e3[Локальні рішення]
        e1 --> e2 --> e3
    end
    subgraph CLOUD["Хмара (опціонально)"]
        c1[Довгострокове сховище]
        c2[Глобальні звіти]
    end
    EDGE -->|тільки агреговане<br/>і аномалії| CLOUD

    note["✓ затримка <100мс<br/>✓ працює без інтернету<br/>✓ не вантажить центр"]
    EDGE -.- note
```
