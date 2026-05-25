# Cyber-Energy Lab

Edge-система моніторингу, семантичного опису та інтелектуальної діагностики
обладнання лабораторії кіберенергетичних систем.

## Дипломні роботи

Репозиторій обʼєднує три дипломні роботи, які разом утворюють один
працюючий програмно-апаратний комплекс:

1. **Система граничного моніторингу енергетичного споживання
   лабораторії кіберенергетичних систем.**
2. **Система збору даних та знань про технічні характеристики та
   режими експлуатації обладнання лабораторії кіберенергетичних систем
   засобами ШІ.**
3. **Система для аналізу стану обладнання кіберенергетичної
   лабораторії засобами граничних обчислень.**

Конвеєр даних побудований навколо теплового насоса Mitsubishi EcoDan:
збір метрик з обладнання, їх зберігання у часових рядах, візуалізація,
семантичний опис у вигляді OWL/Turtle онтології та локальний аналіз
стану обладнання на edge-вузлі (Raspberry Pi) з використанням
ML-моделі та правил, отриманих з онтології.

## Загальний опис системи

Система складається з трьох логічних підсистем, які працюють як єдиний
конвеєр і обмінюються повідомленнями через MQTT за узгодженими
JSON-схемами у [shared/schemas.py](shared/schemas.py).

1. **Моніторинг енергоспоживання.** Edge-вузол біля обладнання зчитує
   метрики (потужність, температури, COP, режим роботи) і публікує їх
   у MQTT. Міст `mqtt_to_influx.py` підписується на топіки та записує
   точки у InfluxDB. Grafana будує дашборд у реальному часі.
2. **Онтологія обладнання та LLM-парсер паспортів.** Triple store
   Apache Jena Fuseki містить OWL/Turtle онтологію `equipment.ttl` з
   описом пристроїв, їх характеристик, очікуваних меж параметрів та
   режимів роботи. Сервіс `ontology_api` надає HTTP-фасад поверх
   SPARQL. Окремий модуль витягує дані з PDF-паспортів обладнання
   через LLM і додає нові триплети у онтологію.
3. **Edge-аналіз стану обладнання.** Аналізатор на Raspberry Pi
   підписується на метрики у MQTT, запитує очікувані межі параметрів
   у `ontology_api`, проганяє дані через ML-модель (IsolationForest
   або numpy-only autoencoder для Pi Zero) і поєднує її висновок з
   правилами з онтології. Результат публікується назад у MQTT як стан
   пристрою та потрапляє на центральний сервер тривог.


## Як працює система

```
обладнання (EcoDan)
    │
    ▼
Raspberry Pi (edge-вузол)  ──publish──►  MQTT (Mosquitto)
                                              │
                            ┌─────────────────┼──────────────────┐
                            ▼                 ▼                  ▼
                     mqtt_to_influx     edge analyzer       alerts_server
                            │                 │                  │
                            ▼                 ▼                  ▼
                       InfluxDB        ontology_api          дашборд
                            │                 ▲
                            ▼                 │
                        Grafana            Fuseki
```

Цикл одного вимірювання:

1. Pi (або `synthetic_publisher` у режимі розробки) публікує JSON з
   метриками у топік `lab/equipment/{device_id}/metrics`.
2. `mqtt_bridge` пише точку в InfluxDB; паралельно `analyzer` отримує
   ті ж метрики, запитує очікувані межі через `ontology_api` і
   обчислює стан пристрою.
3. `analyzer` публікує результат у `lab/equipment/{device_id}/state`.
   Стан читає веб-панель і центральний сервер тривог.
4. Grafana опитує InfluxDB і показує енергоспоживання та похідні
   параметри.

## Апаратні вимоги

Система передбачає три типи апаратних вузлів. Для повноцінного
лабораторного стенду потрібні всі три; для розробки достатньо одного
сервера.

**Сервер (центральний вузол).** Тримає MQTT-брокер, InfluxDB, Fuseki,
Grafana, ontology_api та сервер тривог. Це може бути окрема
Linux-машина у локальній мережі лабораторії, ноутбук на macOS або
Linux для розробки, або сервер на базі x86_64.

* CPU: 2+ ядра x86_64 або ARMv8.
* RAM: 4 ГБ мінімум, 8 ГБ рекомендовано (Grafana + InfluxDB +
  Fuseki + Mosquitto + Python-сервіси одночасно).
* Диск: 20 ГБ вільного місця під образи Docker та томи з даними
  часових рядів і онтологією.
* Мережа: статичний або відомий IP у локальній мережі, відкриті порти
  1883, 3000, 3030, 5002, 5003, 8087.
* OS: Linux (Ubuntu 22.04+, Debian 12+) або macOS 13+.
* ПЗ: Docker Engine 24+, плагін `docker compose`, Git.

**Edge-вузол на базі Raspberry Pi 4 або Pi 5 (рекомендований варіант
для проду).** Тримає edge-аналізатор з повноцінною ML-моделлю та
веб-панеллю.

* Raspberry Pi 4 (4 ГБ або 8 ГБ RAM) чи Raspberry Pi 5.
* microSD 16+ ГБ (рекомендовано 32 ГБ class 10 або A1/A2).
* Блок живлення 5 В / 3 А (USB-C для Pi 4/5).
* Raspberry Pi OS 64-bit (Bookworm).
* Docker Engine та плагін `docker compose`.
* Інтерфейс до обладнання EcoDan (Modbus RTU через USB-RS485 або
  Modbus TCP по мережі).

**Edge-вузол на базі Raspberry Pi Zero V1.3 (low-power варіант).**
Тримає урізаний numpy-only аналізатор без Docker.

* Raspberry Pi Zero V1.3 (ARMv6, BCM2835, 512 МБ RAM, без NEON,
  без вбудованого Wi-Fi).
* microSD 8+ ГБ (class 10).
* Блок живлення 5 В / 2 А (microUSB).
* USB-Wi-Fi адаптер або USB-Ethernet перехідник (на V1.3 немає
  вбудованого Wi-Fi на відміну від Pi Zero W).
* Raspberry Pi OS Lite для ARMv6 (32-bit).
* Інтерфейс до обладнання EcoDan (USB-RS485 через USB-OTG або
  Modbus TCP по мережі).
* Бібліотека `libopenblas` для numpy.

## Розподіл по дипломах

Файли по підсистемах:

**Диплом 1. Система граничного моніторингу енергетичного споживання
лабораторії кіберенергетичних систем.**

* [monitoring/](monitoring/): повна підсистема (міст MQTT→InfluxDB,
  синтетичний публікатор, Grafana-дашборди).
* [monitoring/mqtt_to_influx.py](monitoring/mqtt_to_influx.py),
  [monitoring/synthetic_publisher.py](monitoring/synthetic_publisher.py),
  [monitoring/grafana_dashboard.json](monitoring/grafana_dashboard.json),
  [monitoring/final_dashboard_v2.json](monitoring/final_dashboard_v2.json).

**Диплом 2. Система збору даних та знань про технічні характеристики
та режими експлуатації обладнання лабораторії кіберенергетичних систем
засобами ШІ.**

* [ontology/](ontology/): онтологія, SPARQL API, PDF→TTL конвеєр.
* [ontology/equipment.ttl](ontology/equipment.ttl),
  [ontology/load_ontology.py](ontology/load_ontology.py),
  [ontology/ontology_api.py](ontology/ontology_api.py),
  [ontology/pdf_to_ontology/](ontology/pdf_to_ontology/).

**Диплом 3. Система для аналізу стану обладнання кіберенергетичної
лабораторії засобами граничних обчислень.**

* [analyzer/](analyzer/): ML-модель, правила, веб-панель, варіант
  для Pi Zero.
* [analyzer/analyzer.py](analyzer/analyzer.py),
  [analyzer/edge_analyzer.py](analyzer/edge_analyzer.py),
  [analyzer/pi_zero_analyzer.py](analyzer/pi_zero_analyzer.py),
  [analyzer/train_model.py](analyzer/train_model.py),
  [analyzer/web_panel.py](analyzer/web_panel.py),
  [analyzer/anomaly_ae.npz](analyzer/anomaly_ae.npz).

Спільне ядро:
[shared/schemas.py](shared/schemas.py) (контракти JSON),
[alerts_server/](alerts_server/) (центральний сервер тривог),
[docker-compose.yml](docker-compose.yml),
[docker-compose.pi.yml](docker-compose.pi.yml).

## Сценарій 1. Запуск повної системи на сервері (Linux або macOS)

Сервер виконує роль центрального вузла: тримає MQTT-брокер, InfluxDB,
Fuseki, Grafana, ontology_api та сервер тривог. Edge-аналізатор на
окремій Raspberry Pi приєднується до цього сервера по мережі.

Вимоги:

* Docker Engine 24+ та плагін `docker compose`.
* Вільні TCP-порти 1883, 3000, 3030, 5002, 5003, 8087.
* Git.

Кроки:

```bash
git clone https://github.com/eLQeR/Energy-System.git
cd Energy-System
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Якщо всі контейнери у стані `Up`, система готова. Точки входу:

| URL | Призначення | Облікові дані |
|---|---|---|
| http://SERVER:3000 | Grafana | admin / admin |
| http://SERVER:3030 | Fuseki, SPARQL-консоль | admin / admin |
| http://SERVER:5002/devices | Список пристроїв з онтології | без авторизації |
| http://SERVER:5003 | Дашборд центральних тривог | без авторизації |
| http://SERVER:8087 | InfluxDB UI | admin / adminadmin |

Підключення Grafana до InfluxDB робиться один раз: Connections → Data
sources → InfluxDB. URL `http://influxdb:8086`, query language `Flux`,
org `lab`, token `lab-dev-token`, bucket `metrics`. UID datasource
обовʼязково `influx-lab`, інакше імпорт дашборда його не знайде. Сам
дашборд лежить у
[monitoring/grafana_dashboard.json](monitoring/grafana_dashboard.json)
і імпортується через Dashboards → Import.

Перевірка, що метрики реально йдуть у MQTT:

```bash
docker compose exec mosquitto mosquitto_sub -t 'lab/equipment/+/metrics' -v
```

Перевірка, що ontology_api відповідає:

```bash
curl http://localhost:5002/devices
curl http://localhost:5002/device/ecodan_01/expected-bounds
```

Зупинка:

```bash
scripts/stop_all.sh        # docker compose down, дані у томах зберігаються
docker compose down -v     # повне очищення з видаленням томів
```

## Сценарій 2. Запуск на Raspberry Pi Zero V1.3 (без Docker)

Pi Zero V1.3 побудований на ARMv6 без NEON. Docker Engine на ньому не
запускається штатно, TensorFlow wheels під ARMv6 не існують, локальна
компіляція триває години. Тому для Pi Zero V1.3 використовується
окремий bootstrap-скрипт, який запускає numpy-only autoencoder з
попередньо натренованими вагами
([analyzer/anomaly_ae.npz](analyzer/anomaly_ae.npz)).

Pi Zero V1.3 виступає edge-вузлом. Сервер з MQTT, InfluxDB,
ontology_api та сервером тривог має бути піднятий окремо за
сценарієм 1.

Вимоги:

* Raspberry Pi OS (Lite або Desktop) для ARMv6.
* Python 3.9+ у системі.
* Доступ по мережі до сервера.
* Бібліотека `libopenblas` (numpy потребує її для ефективних
  обчислень).

Підготовка системи:

```bash
sudo apt update
sudo apt install -y git python3-venv libopenblas0
```

Розгортання:

```bash
git clone https://github.com/eLQeR/Energy-System.git
cd Energy-System
MQTT_BROKER=192.168.1.10 \
ONTOLOGY_API=http://192.168.1.10:5002 \
ALERTS_API=http://192.168.1.10:5003 \
DEVICE_ID=ecodan_01 \
  ./scripts/pi-zero-bootstrap.sh
```

IP `192.168.1.10` замінити на фактичну адресу сервера у локальній
мережі. Скрипт перевіряє наявність ваг моделі, створює віртуальне
оточення `.venv` (PEP 668 на сучасних Pi OS забороняє системний pip),
ставить мінімальні залежності й запускає
[analyzer/pi_zero_analyzer.py](analyzer/pi_zero_analyzer.py).

Перевірка з Pi Zero, що сервер доступний:

```bash
ping -c 3 192.168.1.10
curl http://192.168.1.10:5002/devices
```

Подальші запуски виконуються повторним викликом
`./scripts/pi-zero-bootstrap.sh`. Оновлення коду або моделі робиться
через `git pull && ./scripts/pi-zero-bootstrap.sh`.

Якщо `anomaly_ae.npz` відсутній, його треба натренувати на dev-машині
(`pip install -r analyzer/requirements-tf-train.txt`, далі
`analyzer/generate_synthetic.py` і `analyzer/train_tf_model.py`),
закомітити у репозиторій і виконати `git pull` на Pi Zero.

Для Raspberry Pi 4 або 5 (ARMv7/ARMv8) натомість використовується
повний Docker-варіант:

```bash
cp .env.pi.example .env.pi
nano .env.pi                  # вказати IP сервера
docker compose -f docker-compose.pi.yml --env-file .env.pi up -d
docker logs -f pi-analyzer
```

## Сценарій 3. Локальна розробка на Linux або macOS

Сценарій для розробки одного Python-сервісу без повного ребілда
контейнерів.

Підняти лише інфраструктуру у Docker:

```bash
docker compose up -d mosquitto influxdb fuseki
```

Поставити Python-залежності та запустити сервіс локально:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r ontology/requirements.txt      # або monitoring/ analyzer/ alerts_server/
python3 ontology/ontology_api.py
```

`.env.example` уже налаштований на проброшені порти Docker
(`localhost:1883`, `localhost:8087`, `localhost:3030`), тому
localhost-доступ працює без додаткових правок.

Окремо запустити публікатор метрик і міст:

```bash
python3 monitoring/synthetic_publisher.py --output mqtt &
python3 monitoring/mqtt_to_influx.py
```

## Профілі Docker Compose

За замовчуванням стартує базовий стек. Два сервіси сидять під
профілями і вмикаються явно.

**Профіль `pi-local`.** Запускає edge-аналізатор у тому самому стеку
(зручно для демо без живої Pi):

```bash
docker compose --profile pi-local up -d
```

Контейнер `lab-analyzer` віддає веб-панель на http://localhost:5001.

**Профіль `tools`.** Конвертація PDF-паспорта обладнання у фрагмент
онтології через LLM. Запускається разово:

```bash
docker compose --profile tools run --rm pdf_extractor \
    --pdf ontology/BH79D188H02.pdf \
    --device-id ehst20 \
    --out ontology/extracted/ehst20.ttl
```

Додати результат у головну онтологію та перезавантажити Fuseki:

```bash
cat ontology/extracted/ehst20.ttl >> ontology/equipment.ttl
docker compose run --rm ontology_loader
```

Якщо у `.env` заданий `ANTHROPIC_API_KEY`, парсер використовує його
напряму. Інакше використовується fallback через CLI у контейнері,
який треба разово залогінити:

```bash
docker compose exec ontology_api claude /login
```

Токен зберігається у named volume `claude_config:/root/.claude` і
переживає рестарти.

## Логи та діагностика

```bash
docker compose logs -f ontology_api
docker compose logs -f
docker compose ps
```

## Дані між запусками

Дані тримаються у named volumes: `mosquitto_data`, `influx_data`,
`fuseki_data`, `grafana_data`, `alerts_data`, `claude_config`. Команда
`docker compose down` залишає томи, `docker compose down -v` стирає
все.
