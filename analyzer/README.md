# Диплом 3 — Система аналізу стану обладнання засобами граничних обчислень

## Що це

Edge-вузол (Raspberry Pi поруч з обладнанням), який:
1. Читає метрики з MQTT (публікує Диплом 1).
2. Запитує очікувані межі з онтологічного API (Диплом 2).
3. Поєднує ML-модель `IsolationForest` з правилами на межах.
4. Публікує стан (`normal` / `warning` / `anomaly`) у MQTT і показує у веб-панелі.

Усе це — локально, без інтернету, затримка <100 мс. Це і є «засоби граничних обчислень».

## Компоненти

| Файл | Призначення |
|---|---|
| `generate_synthetic.py` | Генерує тренувальний CSV з «нормальних» режимів (20k зразків, 4 mode) |
| `train_model.py` | Legacy: навчає `IsolationForest`, зберігає `anomaly_model.pkl` |
| `train_tf_model.py` | Навчає TF-автоенкодер → `.keras` + `.tflite` + `.npz` ваги |
| `analyzer.py` | MQTT-листенер: AE-прогноз (якщо є `.npz`) + правила |
| `edge_analyzer.py` | Pi-варіант: тільки правила, мінімум залежностей |
| `tf_analyzer.py` | Pi-варіант: TFLite автоенкодер (ARMv7+: Pi 2/3/4/5, Zero 2W) |
| `pi_zero_analyzer.py` | **Pi Zero V1.3 (ARMv6)**: numpy-only autoencoder, без TFLite |
| `web_panel.py` | Flask веб-панель з поточним станом (:5001) |

## Запуск (dev: ML + панель)

```bash
pip install -r requirements.txt
python generate_synthetic.py
python train_model.py
python web_panel.py            # піднімає analyzer у тому ж процесі
# → http://localhost:5001
```

Якщо треба розділити analyzer і панель на два процеси:

```bash
python analyzer.py &
PANEL_START_ANALYZER=0 python web_panel.py
```

## Запуск на Raspberry Pi — три опції

Вибір залежить від того, чи потрібен ML і скільки залежностей готові
поставити.

**1. Rule-based only** (найлегше, ~30 MB залежностей):

```bash
pip install -r analyzer/requirements-edge.txt
python analyzer/edge_analyzer.py
```

**2. TensorFlow Lite + правила** (~50 MB, ML-детекція дрейфу):

```bash
# на dev-машині:
pip install -r analyzer/requirements-tf-train.txt
python analyzer/generate_synthetic.py
python analyzer/train_tf_model.py
# → копіюємо anomaly_ae.tflite + anomaly_ae.meta.json на Pi у analyzer/

# на Pi:
pip install -r analyzer/requirements-tflite.txt
python analyzer/tf_analyzer.py
```

Якщо файли моделі відсутні — `tf_analyzer.py` коректно деградує до
rule-based-only і пише попередження у лог.

**3. Raspberry Pi Zero V1.3 (ARMv6, без NEON)** — numpy-only:

Натреновані ваги (`anomaly_ae.npz` ~3 KB, `anomaly_ae.meta.json` ~0.5 KB)
вже у репо, тому Pi просто клонує і запускає:

```bash
# Перший запуск на Pi Zero:
git clone https://github.com/<user>/Energy-System-fresh.git
cd Energy-System-fresh
MQTT_BROKER=192.168.1.10 \
ONTOLOGY_API=http://192.168.1.10:5002 \
ALERTS_API=http://192.168.1.10:5003 \
  ./scripts/pi-zero-bootstrap.sh

# Подальші запуски / оновлення моделі:
git pull && ./scripts/pi-zero-bootstrap.sh
```

Bootstrap-скрипт перевіряє ваги, ставить залежності (`numpy<2`, `paho-mqtt`,
`requests`, `pydantic`, `python-dotenv` — всі мають ARMv6 wheels на
piwheels.org), і запускає `pi_zero_analyzer.py`.

Якщо хочеш переучити модель з нуля: запусти `train_tf_model.py` на
dev-машині, закоміть оновлені `anomaly_ae.*` і `git pull` на Pi.

**4. Повний sklearn-стек** (важко для Pi, тільки для довідки):

```bash
pip install -r analyzer/requirements.txt
python analyzer/analyzer.py
```

## Що демонструвати на захисті

1. **Edge vs cloud** — порівняй затримку від метрики до реакції, якщо
   analyzer запустити локально vs на віддаленому сервері. Аргумент диплому.
2. **Комбінація ML + правила** — ML ловить «незвичайне» навіть без
   попередніх знань, правила з онтології дають інтерпретовані аномалії
   (`cop_below_nominal(...)`). Показуй разом.
3. **Автономність** — відключи інтернет, відключи онтологічний API — 
   analyzer і далі працює (падає тільки пояснювальна частина від правил).
