# RCM ERP — Internal Work Order System

Manufacturing ERP for **RCM Sp. z o.o.** (Gołdap) — steel fabrication, welding, precast concrete.
Replaces paper *Zlecenie Wewnętrzne* (internal work orders) with a browser-based system that routes orders, generates quotes, and tracks production status automatically.

## The Problem It Solves

The office creates a work order. The technologist gets it on paper. Someone calculates the price. The client waits.
Every step is manual, and the technologist's time gets eaten by non-technical questions from the office.

This system routes orders in seconds: catalog jobs go straight to production with an auto-quote, non-standard jobs get queued for the technologist with a structured estimation form, and anything outside RCM's capabilities gets rejected immediately — before anyone spends time on it.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + SQLAlchemy 2.0 |
| Database | SQLite (MVP) — swap to PostgreSQL when ready |
| Frontend | Vue 3 CDN — single `app.html`, no build step |
| PDF | Jinja2 → WeasyPrint (HTML fallback on Windows without GTK) |
| Deploy | Docker, office LAN at `http://192.168.1.15:8000` |

## How Orders Flow — the Triage Engine

Every new order runs through a 3-branch decision tree before it touches a human:

```
New Order (Biuro submits form)
        │
        ▼
  [ODRZUT] ──── Hard rules from DB: aluminium, stainless, deadline < 2 days
        │         → Immediate rejection with reason. No technologist time wasted.
        │
        ▼ (not rejected)
  [STANDARD] ── Catalog product selected, OR has drawing + matching SOP template
        │         → Price generated automatically. Technologist not needed.
        │
        ▼ (no template match)
  [NIESTANDARD] → Technologist queue. Structured estimate form (see below).
        │
        ▼ (technologist saves wycena)
  [QUOTED] ──── Biuro sees "✅ Zatwierdź" button. One click → in_production.
        │
        ▼
  [IN_PRODUCTION] → [DONE]
```

Reject rules live in the database — the Director can add new ones from the UI without touching code.

## Technologist Estimate Formula (v2)

When an order hits NIESTANDARD, the technologist fills a structured form. Final price:

```
base = Σ(process costs)
     + material purchase cost
     + weight_kg × weight_rate_pln_kg   ← 7–30 PLN/kg by complexity
     + welding_hours × labor_rate
     + extra_labor_hours × labor_rate

total_net = base × (1 + overhead_pct) × (1 + margin_pct)
```

`weight_netto_kg` and `weight_brutto_kg` are stored as reference only — they don't affect the price.
After saving, one-click **"💾 Zapisz jako szablon SOP"** promotes the job to the Standard catalog for future orders.

## Quick Start

```bash
cd rcm_erp/backend

# Install dependencies
pip install -r requirements.txt

# On Windows: Polish characters need this env var
set PYTHONIOENCODING=utf-8

# Create DB + seed (templates, rules, prices from xlsx)
python seed.py
# Expected: 10 templates, 4 rules, 4 settings, 35 prices

# Run (note: port 8000 may be blocked on Windows by PID 4 — use 8001)
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
# Open: http://localhost:8001
```

**PINs (MVP auth):**
- Biuro (office): `1111`
- Technolog: `2222`
- Dyrektor: `3333`

> **Existing DB?** No wipe needed. `_ensure_quote_v2_columns()` runs at startup and adds new columns via idempotent `ALTER TABLE`.

## Project Structure

```
rcm_erp/
├── backend/
│   ├── main.py          # FastAPI — all endpoints
│   ├── models.py        # SQLAlchemy — 15 tables
│   ├── schemas.py       # Pydantic — request/response validation
│   ├── triage.py        # 3-branch routing engine
│   ├── seed.py          # Initial data + xlsx price parser
│   ├── pdf_gen.py       # PDF generation (arkusz + oferta)
│   ├── uploads/         # File attachments (drawings, docs) — gitignored
│   └── rcm_erp.db       # SQLite — gitignored, generate via seed.py
├── frontend/
│   └── app.html         # Full UI — Vue 3 CDN, 3 roles, ~1300 lines
├── templates/
│   ├── arkusz.html      # Shop-floor PDF (no prices — commercial secret)
│   └── oferta.html      # Client PDF (final price only, no rates/margin)
├── tests/
│   ├── test_triage.py   # Unit tests — triage engine (no DB, mock only)
│   └── test_api.py      # Integration tests — FastAPI + in-memory SQLite
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## API Reference (key endpoints)

| Method | Endpoint | Who | What |
|--------|----------|-----|------|
| POST | `/api/orders` | Biuro | Create order via wizard |
| POST | `/api/orders/{id}/triage` | auto | Route order to branch |
| POST | `/api/orders/{id}/quote/structured` | Technolog | V2 estimate (formula) |
| POST | `/api/orders/{id}/quote` | Technolog | V1 estimate (simple) |
| POST | `/api/orders/{id}/quote/zapor` | Technolog | Fuck-off price ×3–4.5 |
| POST | `/api/orders/{id}/confirm` | Biuro | Approve quoted → in_production |
| POST | `/api/orders/{id}/save-as-template` | Technolog | Promote job to SOP catalog |
| POST | `/api/orders/{id}/attachments` | Technolog | Upload drawing/PDF (max 50MB) |
| GET | `/api/orders/{id}/attachments` | any | List attachments |
| DELETE | `/api/attachments/{id}` | Technolog | Remove attachment |
| GET | `/api/orders/{id}/pdf` | any | Arkusz (shop floor, no PLN) |
| GET | `/api/orders/{id}/oferta` | Biuro | Oferta (client, final price only) |
| GET | `/api/analytics` | Dyrektor | Dashboard summary |
| PATCH | `/api/settings/{key}` | Dyrektor | Update labor rate, margins etc. |

## Two PDF Documents

The system generates two distinct documents from one order — deliberately separated:

- **Arkusz** (`/pdf`) — shop floor copy. Operations, SOP steps, material list. **No PLN anywhere** — welders don't need to know the margin.
- **Oferta** (`/oferta`) — client copy. Final price net+VAT, delivery scope. **No rates, no SOP steps** — clients don't get to pick apart the quote.

## Key Business Rules (don't break these)

- Soft delete on templates — `DELETE /api/templates/{id}` sets `is_active=False`. Never hard-deletes. Old orders keep their FK references intact.
- Arkusz for the shop floor must **never show PLN** — commercial secret from welders.
- Oferta for the client must **never show rates or margin** — no basis for negotiation.
- `is_defence` flag — defence/MON projects get a red stripe. Documents marked *poufne*.
- Labor rate stored in `Setting` table (`labor_rate_pln`). Director changes via `PATCH /api/settings/labor_rate_pln` — no restart.
- Quote upsert — each order has at most one Quote. Re-submitting overwrites.
- File uploads stored at `backend/uploads/{order_id}/` — served via `/uploads` static mount.

## Running Tests

```bash
cd rcm_erp
pip install pytest httpx
pytest tests/ -v
```

Expected output: **~30 tests, all passing**.

## Docker (office LAN deploy)

```bash
docker-compose up --build
# Accessible at http://192.168.1.15:8000 from any machine on the network
```
