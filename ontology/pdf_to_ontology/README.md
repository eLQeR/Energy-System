# PDF → Ontology pipeline

Витягує технічні характеристики з паспорта обладнання (PDF) і генерує
фрагмент Turtle, готовий для додавання в [equipment.ttl](../equipment.ttl).

## Архітектура (5 кроків)

```
PDF
 │  pypdf — без LLM, без коштів
 ▼
list[(page_no, text)]                     ← extract.py
 │  фільтр за ключовими словами + дедуп
 │  багатомовні титулки відкидаємо
 ▼
~15-25 spec-dense сторінок                ← filter.py
 │  один HTTP-запит, structured output
 │  Anthropic messages.parse() з Pydantic
 ▼
HeatPumpProfile (валідований)             ← schema.py + llm.py
 │  тривіально
 ▼
Turtle фрагмент                           ← turtle.py
```

## Чому ефективно за токенами

| Тактика | Економія |
|---|---|
| `pypdf` витягує текст локально (без vision) | ~5× проти Files API з PDF |
| Фільтр викидає ~50% сторінок (warnings, install steps, мультимовні титулки) | ~2× менше вхідних токенів |
| Один запит з `messages.parse()` замість chunking | менше overhead, без merge-кроку |
| Локальний кеш по hash вмісту | повторний запуск — 0 токенів |

Для типового паспорта (~50 сторінок, ~5MB):
- Сирий текст: ~25K токенів
- Після фільтра: ~8-12K токенів
- Один виклик з Opus 4.7: ~$0.05-0.10
- Те саме з Haiku 4.5 (`--model claude-haiku-4-5`): ~$0.01-0.02

## Запуск

```bash
# у Docker (рекомендовано — не треба anthropic на хості)
docker compose --profile tools run --rm pdf_extractor \
    --pdf ontology/BH79D188H02.pdf \
    --device-id ehst20 \
    --out ontology/extracted/ehst20.ttl

# локально
ANTHROPIC_API_KEY=sk-... python3 -m ontology.pdf_to_ontology.cli \
    --pdf ontology/BH79D188H02.pdf \
    --device-id ehst20 \
    --out ontology/extracted/ehst20.ttl
```

Потім скопіювати/конкатенувати фрагмент у `equipment.ttl` і перезавантажити:

```bash
cat ontology/extracted/ehst20.ttl >> ontology/equipment.ttl
docker compose run --rm ontology_loader
```

## Параметри

| Прапорець | За замовчуванням | Опис |
|---|---|---|
| `--pdf` | — | Шлях до PDF паспорта |
| `--device-id` | — | Локальне ім'я в онтології (`lab:<device_id>`) |
| `--out` | stdout | Куди писати .ttl |
| `--model` | `claude-opus-4-7` | Модель Anthropic. `claude-haiku-4-5` ≈5× дешевше |
| `--max-pages` | 30 | Скільки топ-релевантних сторінок надсилати |
| `--no-cache` | false | Не використовувати локальний кеш |
| `--cache-dir` | `ontology/.pdf_cache` | Де зберігати JSON-кеш |

## Якщо багато PDF

- Перемкнути на Haiku 4.5: `--model claude-haiku-4-5`
- Розглянути Anthropic Batches API (50% знижка, async) — варто реалізувати,
  якщо PDF більше 20-30. Поточний код — синхронний one-shot.

## Структура

| Файл | Призначення |
|---|---|
| `extract.py` | PDF → текст (pypdf) |
| `filter.py` | Релевантність сторінок |
| `schema.py` | Pydantic-модель = контракт виходу LLM |
| `llm.py` | Anthropic виклик з `messages.parse()` |
| `turtle.py` | Profile → Turtle |
| `cli.py` | Точка входу |
