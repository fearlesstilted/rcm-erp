# RCM ERP — Project Context for Claude

## Что это

B2B Manufacturing ERP для **RCM Sp. z o.o.** (сварка, металл, ЖБИ, Gołdap).
Заменяет бумажные "Zlecenie Wewnętrzne". Соло-разработчик, MVP.

## Стек

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy (sync), SQLite
- **Frontend:** Vue 3 (CDN, no build step) — `frontend/app.html`
- **Тесты:** pytest + httpx TestClient (66 тестов)
- **PDF:** WeasyPrint (`pdf_gen.py`)
- **Excel:** openpyxl (экспорт `/api/export/xlsx`)
- **Upload:** python-multipart + aiofiles → `backend/uploads/{order_id}/`
- **Deploy:** Docker + docker-compose

## Запуск

```bash
# Сервер (из корня проекта)
cd backend && uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# Тесты
python -m pytest tests/ -v

# Пересоздать БД (осторожно — вайп)
cd backend && python -X utf8 seed.py
```

**Порт 8000 занят Windows (PID 4) — всегда 8001.**

## PINы для входа

| Роль | PIN |
|------|-----|
| Biuro | 1111 |
| Technolog | 2222 |
| Dyrektor | 3333 |

## Статус-машина заказов

```
draft → [triage] → niestandard → quoted → in_production → w_trakcie → gotowe → wydane
                 ↘ standard (auto)
                 ↘ rejected
```

- `niestandard → quoted`: POST /quote, /quote/zapor, /quote/structured
- `quoted → in_production`: Biuro нажимает "✅ Zatwierdź" (POST /confirm)
- `in_production → w_trakcie`: Technolog нажимает "▶ Rozpocznij pracę" (POST /start)
- `w_trakcie → gotowe`: Technolog нажимает "✅ Gotowe" (POST /complete)
- `gotowe → wydane`: Biuro выдаёт клиенту (POST /deliver)

## Ключевые файлы

| Файл | Что делает |
|------|-----------|
| `backend/main.py` | Все FastAPI endpoints + startup hooks |
| `backend/models.py` | SQLAlchemy модели (Order, Quote, OrderAttachment, ...) |
| `backend/schemas.py` | Pydantic схемы (OrderCreate, OrderUpdate, QuoteStructuredCreate, ...) |
| `backend/triage.py` | Логика классификации standard/niestandard/odrzut |
| `backend/seed.py` | Заполняет БД тестовыми данными |
| `frontend/app.html` | Весь Vue 3 UI (все роли, модалы, логика) |
| `tests/test_api.py` | Интеграционные тесты (66 кейсов) |

## Архитектурные правила

1. **Нет CSV** — все данные через API, ничего локально
2. **`_ensure_quote_v2_columns(engine)`** на старте — идемпотентный ALTER TABLE, не ломает старую БД
3. **Только sync SQLAlchemy** — не переходить на async ORM без явной причины
4. **Тесты через TestClient** — in-memory SQLite, не поднимать реальный сервер
5. **PATCH с `exclude_unset=True`** — partial update, не перезаписывает поля которые не переданы

## БД — таблицы (15 шт)

`users`, `orders`, `order_operations`, `tech_cards`, `quality_cards`,
`material_requests`, `product_templates`, `constraint_rules`, `quotes`,
`price_history`, `stock_movements`, `component_containers`, `parameter_requests`,
`settings`, `order_attachments`

## Skills в проекте (`.agents/skills/`)

Загружаются автоматически (~800 токенов overhead):

| Skill | Зачем |
|-------|-------|
| `fastapi-python` | Паттерны FastAPI, dependency injection |
| `fastapi-templates` | Jinja2 шаблоны, PDF генерация |
| `sqlalchemy-alembic-*` | ORM паттерны, миграции |
| `python-testing-patterns` | pytest fixtures, TestClient |
| `python-executor` | Запуск Python кода |
| `frontend-design` | Vue 3 / CSS паттерны |
| `accessibility` | A11y для форм и таблиц |
| `seo` | (резерв) |

## Open Issues

| # | Приоритет | Задача | Статус |
|---|-----------|--------|--------|
| #2/#3 | низкий | SQLAlchemy deprecation warnings | ❌ не начато |
| ~~#1~~ | закрыт | Edit заказа: PATCH + форма в Szczegóły | ✅ done |
| ~~#4~~ | закрыт | Status badge в очереди Technologa | ✅ done |

## Что не трогать

- `rcm_erp.db` в корне — артефакт, рабочая БД в `backend/rcm_erp.db`
- `HANDOFF.md` — локальный, в .gitignore, не пушить
- `backend/uploads/` — загруженные файлы, не коммитить
