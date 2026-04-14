# RCM ERP — Internal Work Order System

Manufacturing ERP for **RCM Sp. z o.o.** (Gołdap) — steel fabrication, welding, precast concrete.
Replaces paper *Zlecenie Wewnętrzne* (internal work orders) with a browser-based system that routes orders to the right team automatically.

## The Problem It Solves

The office creates a work order. The technologist gets it on paper. Someone calculates the price. The client waits.  
Every step is manual, and the technologist's time gets eaten by non-technical questions from the office.

This system routes orders in seconds: catalog jobs go straight to production with an auto-quote, non-standard jobs get queued for the technologist, and anything outside RCM's capabilities gets rejected immediately — before anyone spends time on it.

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
        │         → Price generated automatically from template. Technologist not needed.
        │
        ▼ (no template match)
  [NIESTANDARD] → Queued for Technologist. Manual wycena required.
```

Reject rules live in the database — the Director can add new ones from the UI without touching code.

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

# Run
uvicorn main:app --reload --port 8000
# Open: http://localhost:8000
```

**PINs (MVP auth):**
- Biuro (office): `1111`
- Technolog: `2222`
- Dyrektor: `3333`

## Project Structure

```
rcm_erp/
├── backend/
│   ├── main.py          # FastAPI — all endpoints
│   ├── models.py        # SQLAlchemy — 14 tables
│   ├── schemas.py       # Pydantic — request/response validation
│   ├── triage.py        # 3-branch routing engine
│   ├── seed.py          # Initial data + xlsx price parser
│   ├── pdf_gen.py       # PDF generation (arkusz + oferta)
│   └── rcm_erp.db       # SQLite — gitignored, generate via seed.py
├── frontend/
│   └── app.html         # Full UI — Vue 3 CDN, 3 roles
├── templates/
│   ├── arkusz.html      # Shop-floor PDF (no prices — commercial secret)
│   └── oferta.html      # Client PDF (final price only, no rates/margin)
├── tests/
│   ├── test_triage.py   # Unit tests — triage engine (no DB)
│   └── test_api.py      # Integration tests — FastAPI + in-memory SQLite
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Two PDF Documents

The system generates two distinct documents from one order — deliberately separated:

- **Arkusz** (`/api/orders/{id}/pdf`) — shop floor copy. Operations, SOP steps, material list. **No PLN anywhere** — welders don't need to know the margin.
- **Oferta** (`/api/orders/{id}/oferta`) — client copy. Final price net+VAT, delivery scope. **No rates, no SOP steps** — clients don't get to pick apart the quote.

## Key Business Rules (don't break these)

- Soft delete on templates — `DELETE /api/templates/{id}` sets `is_active=False`. Never hard-deletes. Old orders keep their FK references intact.
- `is_defence` flag — defence/MON projects get a red stripe in the order list. Documents marked *poufne*.
- Labor rate stored in `Setting` table (`labor_rate_pln`). Director changes it via `PATCH /api/settings/labor_rate_pln` — no restart needed.
- Price history table (`PriceHistory`) seeded from real `ceny/*.xlsx` files — basis for future AI-assisted quoting.

## Running Tests

```bash
cd rcm_erp
pip install pytest httpx
pytest tests/ -v
```

## Docker (office LAN deploy)

```bash
docker-compose up --build
# Accessible at http://192.168.1.15:8000 from any machine on the network
```
