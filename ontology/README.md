# Диплом 2 — Система збору даних та знань засобами ШІ

## Що це

Онтологія (OWL/RDF) + SPARQL-endpoint (Fuseki) + HTTP API, через який
інші дипломи запитують характеристики обладнання. Плюс ШІ-конвеєр,
який парсить PDF-паспорти через Anthropic Claude і автоматично
наповнює онтологію.

## Компоненти

| Файл / каталог | Призначення |
|---|---|
| `equipment.ttl` | Онтологія в Turtle: класи, властивості, екземпляри |
| `load_ontology.py` | Завантажує .ttl у Fuseki (запускається сервісом `ontology_loader`) |
| `ontology_api.py` | Flask-обгортка над SPARQL, JSON API для інших дипломів |
| `pdf_to_ontology/` | **ШІ-конвеєр** PDF → Turtle (див. свій [README](pdf_to_ontology/README.md)) |

## Запуск (все в Docker)

API і завантаження онтології піднімаються разом з усією системою:

```bash
docker compose up -d
# → ontology_api    http://localhost:5002
# → fuseki          http://localhost:3030
```

## Редагування онтології

Зручно редагувати `equipment.ttl` у [Protégé](https://protege.stanford.edu/).
Відкрити → внести зміни → зберегти як Turtle → перезапустити завантажувач:

```bash
docker compose run --rm ontology_loader
```

## API

| Маршрут | Що повертає |
|---|---|
| `GET /devices` | Список усіх зареєстрованих пристроїв |
| `GET /device/<id>/specs` | Усі літеральні властивості пристрою |
| `GET /device/<id>/expected-bounds` | Межі для детектора аномалій (Диплом 3) |
| `GET /device/<id>/components` | Компоненти обладнання |

## ШІ-наповнення онтології з PDF

```bash
# 1. Поклади ANTHROPIC_API_KEY у .env
# 2. Запусти конвеєр (один раз на кожен PDF)
docker compose --profile tools run --rm pdf_extractor \
    --pdf ontology/BH79D188H02.pdf \
    --device-id ehst20 \
    --out ontology/extracted/ehst20.ttl

# 3. Додай витягнуті триплети в онтологію і перезавантаж
cat ontology/extracted/ehst20.ttl >> ontology/equipment.ttl
docker compose run --rm ontology_loader
```

Деталі — [pdf_to_ontology/README.md](pdf_to_ontology/README.md):
архітектура з 5 кроків, тактики економії токенів, параметри CLI.
