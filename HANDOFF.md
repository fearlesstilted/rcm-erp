# RCM ERP — Handoff для следующей сессии
_Создан: 2026-04-13, рабочая станция TopKop Dev_

---

## Суть проекта

B2B Manufacturing ERP / CPQ для **RCM Sp. z o.o.** (сварка, металл, ЖБИ — Gołdap).
Заменяет бумажные "Zlecenie Wewnętrzne" (внутренние наряды).

**Папка:** `D:\vsc\f\rcm_erp\` (или куда склонировал на ноуте)
**Репо:** тот же git, ветка `main`

---

## Стек

| Слой | Технология |
|------|-----------|
| Backend | FastAPI + SQLAlchemy + SQLite (MVP) |
| Frontend | Vue 3 CDN, один файл `frontend/app.html` — без Node/Vite |
| PDF | Jinja2 → WeasyPrint (fallback: HTML если нет GTK на Windows) |
| Deploy | Docker локально, `http://192.168.1.15:8000` (офисный PC) |

---

## Структура папки

```
rcm_erp/
├── backend/
│   ├── main.py          # FastAPI — все эндпоинты
│   ├── models.py        # SQLAlchemy — 14 таблиц
│   ├── schemas.py       # Pydantic — валидация
│   ├── triage.py        # 3-ветковый движок маршрутизации
│   ├── seed.py          # Начальные данные + парсер xlsx из ceny/
│   ├── pdf_gen.py       # Генерация PDF (arkusz + oferta)
│   └── rcm_erp.db       # SQLite — в .gitignore, генерировать!
├── frontend/
│   └── app.html         # Весь UI — Vue 3 CDN, 3 роли
├── templates/
│   ├── arkusz.html      # PDF для цеха (без цен!)
│   └── oferta.html      # PDF для клиента (с ценой, без SOP)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Как запустить (первый раз или после pull)

```bash
cd rcm_erp/backend

# 1. Установить зависимости (если не установлено)
pip install fastapi uvicorn sqlalchemy jinja2 openpyxl python-multipart

# 2. Создать базу данных (ОБЯЗАТЕЛЬНО при первом запуске или после изменений моделей)
set PYTHONIOENCODING=utf-8   # Windows — иначе UnicodeEncodeError на польских символах
python seed.py               # Ожидаемый вывод: 10 шаблонов, 4 правила, 4 настройки, 35 цен

# 3. Запустить сервер
uvicorn main:app --reload --port 8000

# Открыть: http://localhost:8000
```

**PIN для входа:**
- Biuro (офис): `1111`
- Technolog: `2222`
- Dyrektor: `3333`

---

## 3-ветковый Triage Engine (ключевая логика)

```
Новое злецение
     ↓
[Odrzut] — жёсткие правила из ConstraintRule (алюминий, нержавейка → нет)
     ↓ если не отклонено
[Standard] — совпадение с ProductTemplate по sop_name (LIKE поиск)
           — или order_type == "catalog" с template_id
     ↓ если не совпало
[Niestandard] — очередь Technologa, ручная wycena
```

---

## Что было сделано в этой сессии (2026-04-13)

### Исправленные баги
1. **sop_name = None (Bug #1)** — Remont с известным SOP всегда шёл в Niestandard. Исправлено: `sop_name = order.sop_name` в triage_order.
2. **Excel по индексам (Bug #2)** — Заменён на динамический поиск заголовков (`find_col` с keyword matching). Парсер переживёт любой порядок колонок.
3. **Hardcoded 90 PLN/h (Bug #3)** — Заменён таблицей `Setting` в БД. Директор меняет ставку через UI без рестарта сервера.
4. **PDF endpoint отсутствовал (Bug #4)** — Реализован `pdf_gen.py` + `arkusz.html` + `GET /api/orders/{id}/pdf`.
5. **template_id отсутствовал в TriageInput (Bug #5, Gemini не заметил)** — Каталог показывал "katalog" вместо имени продукта. Исправлено: добавлено поле в dataclass + передача из main.py.

### Новые фичи
- **`oferta.html`** + **`GET /api/orders/{id}/oferta`** — коммерческое предложение для клиента с ценой нетто+НДС, без SOP и ставок.
- **Разделение документов**: Arkusz (для цеха) = без PLN. Oferta (для клиента) = только итоговая цена.
- **`is_defence: bool`** — чекбокс "🛡 Projekt zbrojeniowy / MON" в визарде, красная полоска в списке заказов.
- **Soft delete шаблонов**: `DELETE /api/templates/{id}` → `is_active=False` (запись остаётся, FK не рвётся). `PATCH /api/templates/{id}/restore` — возврат.
- **6 реальных ProductTemplate** — цены из реальных файлов `ceny/*.xlsx` (Wspawanie zębów, Wymiana zęba, Remont łyżki, Zbrojenie dekiel, Strzemiona CNC, Zbrojenie piwniczki).
- **Динамический парсер xlsx** — сканирует строки 1–5 в поисках заголовка (там где "klient"/"firma"), обрабатывает цены типа "1 200,00 zł" и "544 netto".
- **`GET /api/settings`** + **`PATCH /api/settings/{key}`** — для Директора.
- **`OrderOut` расширен** — возвращает sop_name, template_id, description, requires_visit, quantity, estimated_value, is_defence.
- **Triage сохраняет template_id** — после матча записывает в `order.template_id`.
- **22 блока SOP** в SOP_LIBRARY фронтенда (из анализа XLSM: PIŁA, LASER, SM операции).

---

## Что НЕ сделано (V2 — не делать до демо)

| Фича | Почему отложено |
|------|----------------|
| Варианты продуктов (ковши 0.3/0.8m³) | Требует parent-child SKU, переделка таблиц |
| VIN/серийные номера для прицепов | Трассируемость партий металла = кошмар |
| Defence — отдельная ветка triage | Чекбокс `is_defence` достаточно для MVP |
| Настройки для Директора в UI | Endpoint есть (`/api/settings`), UI нет |
| Полноценная страница "Szczegóły" | Сейчас `alert()`, нужен модал |
| Harmonogram (Gantt/календарь) | Таблица есть, визуализации нет |
| Очередь машин (PIŁA, LASER) | Таблица `machine_queue` не создана |
| SMS/email уведомления клиенту | Не начато |

---

## Важные бизнес-правила (не ломать!)

- **Arkusz для цеха — НИКОГДА не показывает PLN** (коммерческая тайна от сварщиков)
- **Oferta для клиента — НИКОГДА не показывает ставки/маржу** (не давай клиенту торговаться)
- **Soft delete шаблонов** — никогда не удалять из БД (ломает FK старых заказов)
- **is_defence** — красная полоска в UI, документы "поufне"
- **PYTHONIOENCODING=utf-8** — обязателен на Windows, иначе UnicodeEncodeError на польских буквах

---

## Каталог сервисов RCM (из rcmc.pl, для расширения каталога)

**Производство:**
- Clever Turtle CT1/CT2/CT1MAX/CTPLUS (гибридные прицепы)
- Przyczepa koparkowa PK1/PK2

**Osprzęt do koparek:**
- Łyżki (5+ размеров), Zrywaki, Lemiesze, Chwytaki sortujące, Pługoukładacze

**Usługi:**
- Spawanie TIG/MAG (сталь конструкционная, нерж., алюминий)
- Cięcie plazmą CNC (bramowa)
- Obróbka skrawaniem (фрезеровка, точение, сверление, резьба, точность 0.01mm)
- Konstrukcje stalowe (индивидуальные)

**Defence (RCM Defence):**
- Gwiazdoblok Dual-Use (заграждения + фундамент под антидрон)
- Tactical Body (кузов dual-use на грузовик)
- Modułowy system schronowy RCM Bastion
- RCM Rapid Fort (фортификационные модули)

---

## Ключевые вопросы к сотрудникам (задать перед демо)

**Technolog:**
- Сколько времени сейчас занимает заполнение Аркуша вручную? (ROI аргумент: если 15 мин × 10 заказов/день = 2.5ч Технолога = ~50€/день)
- Łyżki — сколько размеров в каталоге? (0.3/0.5/0.8/1.2m³?)
- Какой этап "болит" больше всего — запись, материалы или контроль качества?

**Biuro:**
- Как сейчас отправляете офferты клиентам — Word, Excel?
- Бывает что два человека берут одно злецение одновременно? (нужна блокировка)

**Dyrektor (Кшиштоф):**
- Злецения Оборонные — через то же Biuro или отдельный контакт?
- Сколько % оборота — серийное (прицепы) vs единичное (osprzęt/usługi)?

---

## Git — что закоммитить перед демо

```bash
cd D:/vsc/f   # корень репо

git add rcm_erp/ .gitignore
git commit -m "feat: RCM ERP MVP — triage engine, PDF arkusz/oferta, soft delete, is_defence"
git push origin main
```

**`.gitignore` уже обновлён** — `rcm_erp.db` и `Keys/` заигнорированы.
**Без co-author** в этом репо (пользователь явно попросил).
