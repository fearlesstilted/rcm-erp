# RCM ERP

![CI](https://github.com/fearlesstilted/rcm-erp/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Vue.js](https://img.shields.io/badge/Vue.js-3-4FC08D?logo=vuedotjs&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-→%20PostgreSQL-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

Internal work order system for a steel fabrication shop. Handles the full cycle from client inquiry to shop-floor production: automatic routing, technologist estimation, PDF generation, and status tracking.

---

## How It Works

Every order runs through a 3-branch triage engine before a human sees it:

```
New order (Biuro)
      │
      ├─► ODRZUT       — reject rules from DB (material, deadline, scope)
      │                  → instant rejection, reason logged
      │
      ├─► STANDARD     — catalog product or drawing + matching SOP template
      │                  → price auto-calculated, no technologist needed
      │
      └─► NIESTANDARD  → technologist queue
                          └─► QUOTED       — structured estimate submitted
                                └─► IN_PRODUCTION → W_TRAKCIE → GOTOWE → WYDANE
```

Reject rules live in the database — the director adds new ones from the UI without touching code.

---

## Technologist Estimate Formula

```
base  = Σ(process costs)
      + material cost
      + weight_kg × rate_pln_kg
      + welding_hours × labor_rate
      + extra_labor_hours × labor_rate

total = base × (1 + overhead_pct) × (1 + margin_pct)
```

After saving, one click promotes the estimate to a reusable SOP catalog entry — next identical order routes to STANDARD automatically.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI 0.115 + SQLAlchemy 2.0 |
| Database | SQLite (MVP) — swap to PostgreSQL with one connection string change |
| Frontend | Vue 3 CDN, single `app.html`, no build step |
| PDF | Jinja2 → WeasyPrint (HTML fallback on Windows without GTK) |
| Export | openpyxl — XLSX download for director |
| Deploy | Docker + docker-compose |

---

## Quick Start

```bash
git clone https://github.com/fearlesstilted/rcm-erp.git
cd rcm-erp/backend

pip install -r requirements.txt
set PYTHONIOENCODING=utf-8       # Windows only

python seed.py                   # creates DB with templates, rules, settings, prices
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

> Port 8000 is often held by Windows (PID 4). Use `--port 8001`.

**Login PINs:**

| Role | PIN | Access |
|------|-----|--------|
| Biuro | `1111` | Submit orders, confirm quotes, hand off to client |
| Technolog | `2222` | Estimation queue, structured form, file attachments |
| Dyrektor | `3333` | Analytics, revenue, settings, XLSX export |

---

## Project Structure

```
rcm-erp/
├── backend/
│   ├── main.py       — FastAPI, all endpoints
│   ├── models.py     — SQLAlchemy ORM, 15 tables
│   ├── schemas.py    — Pydantic request/response contracts
│   ├── triage.py     — 3-branch routing engine (pure functions)
│   ├── seed.py       — initial data loader + xlsx price parser
│   └── pdf_gen.py    — PDF generation, WeasyPrint with HTML fallback
├── frontend/
│   └── app.html      — Vue 3 SPA, 3 roles, no build step
├── templates/
│   ├── arkusz.html   — shop-floor PDF (no prices)
│   └── oferta.html   — client PDF (final price only)
└── tests/
    ├── test_triage.py — unit tests, triage engine (MagicMock, no DB)
    └── test_api.py    — integration tests, FastAPI + in-memory SQLite
```

---

## API

| Method | Endpoint | Role | Description |
|--------|----------|------|-------------|
| `POST` | `/api/orders` | Biuro | Create order |
| `POST` | `/api/orders/{id}/triage` | auto | Route through decision tree |
| `POST` | `/api/orders/{id}/quote/structured` | Technolog | V2 estimate (formula) |
| `POST` | `/api/orders/{id}/quote` | Technolog | V1 estimate (simple price) |
| `POST` | `/api/orders/{id}/quote/zapor` | Technolog | Deterrent price ×3–4.5 |
| `POST` | `/api/orders/{id}/confirm` | Biuro | quoted → in_production |
| `POST` | `/api/orders/{id}/start` | Technolog | in_production → w_trakcie |
| `POST` | `/api/orders/{id}/complete` | Technolog | w_trakcie → gotowe |
| `POST` | `/api/orders/{id}/deliver` | Biuro | gotowe → wydane |
| `POST` | `/api/orders/{id}/save-as-template` | Technolog | promote estimate to SOP |
| `POST` | `/api/orders/{id}/attachments` | Technolog | upload drawing/PDF (max 50 MB) |
| `GET` | `/api/orders/{id}/pdf` | any | Arkusz (shop floor, no PLN) |
| `GET` | `/api/orders/{id}/oferta` | Biuro | Oferta (client, final price only) |
| `GET` | `/api/analytics` | Dyrektor | KPIs, revenue by month, top clients, overdue |
| `GET` | `/api/export/xlsx` | Dyrektor | Excel export of all orders |
| `PATCH` | `/api/settings/{key}` | Dyrektor | update labor rate, overhead, margin |

---

## PDF Documents

Two documents from one order, deliberately separated:

| Document | For | Shows | Hides |
|----------|-----|-------|-------|
| **Arkusz** (`/pdf`) | Shop floor | Operations, SOP steps, material | All PLN figures |
| **Oferta** (`/oferta`) | Client | Final price net + VAT | Rates, margin, SOP |

---

## Business Rules

- Soft delete on templates — `DELETE /api/templates/{id}` sets `is_active=False`, never hard-deletes. Old orders keep FK references intact.
- Quote upsert — one quote per order, re-submitting overwrites.
- `is_defence` flag — MON/defence orders get a red stripe, documents marked *poufne*.
- Labor rate in DB — director updates via `PATCH /api/settings/labor_rate_pln`, takes effect immediately.
- Arkusz never shows PLN. Oferta never shows rates or margin.

---

## Tests

```bash
cd rcm-erp
pip install pytest httpx
pytest tests/ -v
```

63 tests across unit (triage engine) and integration (FastAPI + in-memory SQLite).

---

## Docker

```bash
docker-compose up --build
```
