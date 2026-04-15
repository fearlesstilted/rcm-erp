# RCM ERP — Internal Work Order System

![CI](https://github.com/fearlesstilted/rcm-erp/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Vue.js](https://img.shields.io/badge/Vue.js-3-4FC08D?logo=vuedotjs&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-MVP-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
  
> Replaces a fully paper-based order process that cost the technologist **~2 hours/day** in manual estimation.

---

## The Problem

The office submits a work order on paper. The technologist gets it, calculates the price in their head, and the client waits. Every step is manual.

- **Technologist interruptions**: office calls 3–5 times/day with questions that are already in the system
- **Pricing errors**: manual estimates drift 5–15% from actual cost — straight margin loss
- **No history**: every completed job is knowledge that disappears when the Excel is closed

**This system eliminates all three.**

---

## How Orders Flow — the Triage Engine

Every new order runs through a 3-branch decision tree before it touches a human:

```
New Order (Biuro submits form)
        │
        ▼
  ┌─── ODRZUT ──────── Reject rules from DB (aluminium, stainless, deadline < 2 days)
  │                    → Immediate rejection with reason. Zero technologist time wasted.
  │
  ▼ (passes rules)
  ┌─── STANDARD ───── Catalog product, or has drawing + matching SOP template
  │                   → Price auto-generated. Technologist never involved.
  │
  ▼ (no template match)
  ┌─── NIESTANDARD ── Technologist queue. Structured estimate form (formula below).
  │
  ▼ (technologist submits wycena)
  ┌─── QUOTED ──────── Biuro sees "✅ Zatwierdź" button. One click → in_production.
  │
  ▼
  IN_PRODUCTION → DONE
```

Reject rules live in the database — the Director can add new ones from the UI without touching code.

---

## Technologist Estimate Formula (v2)

When an order hits NIESTANDARD, the technologist fills a structured form. Final price:

```
base = Σ(process costs)
     + material purchase cost
     + weight_kg × weight_rate_pln_kg   ← 7–30 PLN/kg slider by complexity
     + welding_hours × labor_rate
     + extra_labor_hours × labor_rate

total_net = base × (1 + overhead_pct) × (1 + margin_pct)
```

`weight_netto_kg` / `weight_brutto_kg` stored for reference — not used in the formula.

After saving, **"💾 Zapisz jako szablon SOP"** promotes the job to the Standard catalog.
The next identical order prices itself automatically.

---

## Features

| Feature | Details |
|---------|---------|
| **3-branch triage engine** | Orders auto-route on submission — no manual dispatch |
| **Structured estimate v2** | Formula with processes, weight rate, welding hours, overhead, margin |
| **SOP promotion** | One click to promote any estimate to a reusable catalog template |
| **Role-based UI** | 3 PINs: Biuro, Technolog, Dyrektor — each sees only their workflow |
| **Two PDF documents** | Arkusz (shop floor, no PLN) + Oferta (client, no rates/margin) |
| **File attachments** | Upload drawings/PDFs per order — multipart, max 50 MB |
| **Director analytics** | Order funnel, avg quote value, technologist queue length |
| **Live settings** | Labor rate, overhead %, margin % editable without restart |
| **Idempotent migrations** | `_ensure_quote_v2_columns()` at startup — no DB wipe when schema changes |
| **Docker deploy** | `docker-compose up` → accessible across the office LAN |

---

## Tech Stack

| Layer | Technology | Why this choice |
|-------|------------|----------------|
| Backend | FastAPI 0.115 + SQLAlchemy 2.0 | Async-ready, auto OpenAPI docs, Pydantic v2 validation |
| Database | SQLite → PostgreSQL | Zero infra for MVP; swap with one connection string change |
| Frontend | Vue 3 CDN, single `app.html` | No build step — one file, works without Node.js toolchain |
| PDF | Jinja2 → WeasyPrint | HTML templates → proper PDF with Polish fonts; HTML fallback on Windows |
| HTTP client | httpx | Async-first, used for future Make.com webhook (WhatsApp notifications) |
| Deploy | Docker + docker-compose | Single command, LAN-ready, no cloud dependency |

---

## Quick Start

```bash
git clone https://github.com/fearlesstilted/rcm-erp.git
cd rcm-erp/backend

pip install -r requirements.txt
set PYTHONIOENCODING=utf-8          # Windows only — Polish characters in seed data

python seed.py                      # Creates DB: 10 templates, 4 rules, 4 settings, ~35 prices
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
# Open: http://localhost:8001
```

> **Port 8000 busy on Windows?** PID 4 (System) often holds 8000. Use `--port 8001`.

**Login PINs:**

| Role | PIN | What they see |
|------|-----|---------------|
| Biuro | `1111` | Order wizard, quote confirmation, Oferta PDF download |
| Technolog | `2222` | Estimation queue, structured form, file attachments |
| Dyrektor | `3333` | Analytics dashboard, settings (rates/margins), reject rules |

> **Upgrading an existing DB?** No wipe needed. `_ensure_quote_v2_columns()` runs at startup and adds new columns via idempotent `ALTER TABLE IF NOT EXISTS`.

---

## Project Structure

```
rcm-erp/
├── backend/
│   ├── main.py          # FastAPI app — all 19 endpoints
│   ├── models.py        # SQLAlchemy ORM — 15 tables
│   ├── schemas.py       # Pydantic schemas — request/response contracts
│   ├── triage.py        # 3-branch routing engine (pure functions, fully unit-tested)
│   ├── seed.py          # Initial data loader + xlsx price parser
│   ├── pdf_gen.py       # PDF generation (arkusz + oferta), WeasyPrint with HTML fallback
│   └── uploads/         # File attachments per order — gitignored
├── frontend/
│   └── app.html         # Full SPA — Vue 3 CDN, 3 roles, ~1600 lines, no build step
├── templates/
│   ├── arkusz.html      # Shop-floor PDF template (no prices — commercial secret)
│   └── oferta.html      # Client PDF template (final price only — no rates or margin)
└── tests/
    ├── test_triage.py   # Unit tests — triage engine (MagicMock, no DB)
    └── test_api.py      # Integration tests — FastAPI + in-memory SQLite (~30 tests)
```

---

## API Reference

| Method | Endpoint | Role | Description |
|--------|----------|------|-------------|
| `POST` | `/api/orders` | Biuro | Create order via wizard |
| `GET` | `/api/orders` | any | List all orders |
| `GET` | `/api/orders/{id}` | any | Order details |
| `POST` | `/api/orders/{id}/triage` | auto | Route order through decision tree |
| `POST` | `/api/orders/{id}/quote/structured` | Technolog | V2 estimate — formula with breakdown |
| `POST` | `/api/orders/{id}/quote` | Technolog | V1 estimate — simple total price |
| `POST` | `/api/orders/{id}/quote/zapor` | Technolog | Deterrent price ×3–4.5 (politely decline) |
| `POST` | `/api/orders/{id}/confirm` | Biuro | Approve quoted → in_production |
| `POST` | `/api/orders/{id}/save-as-template` | Technolog | Promote estimate to SOP catalog |
| `POST` | `/api/orders/{id}/attachments` | Technolog | Upload drawing/PDF (max 50 MB) |
| `GET` | `/api/orders/{id}/attachments` | any | List attachments |
| `DELETE` | `/api/attachments/{id}` | Technolog | Remove attachment |
| `GET` | `/api/orders/{id}/pdf` | any | Arkusz PDF (shop floor, no PLN) |
| `GET` | `/api/orders/{id}/oferta` | Biuro | Oferta PDF (client, final price only) |
| `GET` | `/api/analytics` | Dyrektor | Dashboard summary |
| `PATCH` | `/api/settings/{key}` | Dyrektor | Update labor rate, overhead %, margin % |

---

## Two PDF Documents

One order generates two documents — deliberately separated, each hiding what the other reveals:

| Document | Audience | Shows | Hides |
|----------|----------|-------|-------|
| **Arkusz** (`/pdf`) | Shop floor | Operations, SOP steps, material list | All PLN figures |
| **Oferta** (`/oferta`) | Client | Final price net + VAT, delivery scope | Rates, margin, SOP steps |

---

## Business Rules (do not break)

- **Soft delete on templates** — `DELETE /api/templates/{id}` sets `is_active=False`. Never hard-deletes — old orders keep their FK references intact.
- **Quote upsert** — each order has at most one Quote. Re-submitting overwrites.
- **`is_defence` flag** — MON/defence projects get a red stripe. Documents marked *poufne*.
- **Labor rate in DB** — Director changes via `PATCH /api/settings/labor_rate_pln`, takes effect immediately without restart.
- **Arkusz must never show PLN** — commercial secret from welders.
- **Oferta must never show rates or margin** — no basis for negotiation from the client side.

---

## Running Tests

```bash
cd rcm-erp
pip install pytest httpx
pytest tests/ -v
```

Expected: **~30 tests, all green**.

---

## Docker (office LAN deploy)

```bash
docker-compose up --build
# Accessible at http://192.168.1.15:8000 from any machine on the network
```

---

## Estimated Business Impact

Replacing a fully manual process at a steel fabrication shop:

| Pain point | Before | After | Annual saving |
|------------|--------|-------|---------------|
| Technologist time on estimates | ~2 h/day | ~30 min/day | ~10 000 PLN |
| Phone interruptions (office → tech) | 4×/day | <1×/day | ~8 000 PLN |
| Pricing errors (5–15% margin loss) | frequent | near-zero | ~15 000 PLN |
| **Total** | | | **~33 000 PLN/year** |

