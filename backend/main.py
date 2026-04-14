"""
RCM ERP — FastAPI Backend
Uruchomienie: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Dostęp z sieci lokalnej: http://192.168.1.15:8000
"""
import random
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pdf_gen import generate_arkusz_pdf
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from models import (
    Base, Order, OrderOperation, Quote, MaterialRequest,
    QualityCard, ProductTemplate, ConstraintRule, PriceHistory,
    ParameterRequest, StockMovement, ComponentContainer,
    Setting, OrderStatus, TriageBranch, init_db
)
from schemas import (
    OrderCreate, OrderOut, TriageResponse,
    QuoteCreate, QuoteZaporCreate, QuoteOut,
    MaterialRequestCreate, MaterialRequestOut,
    QualityCardCreate, QualityCardOut,
    ParameterRequestCreate, ParameterRequestAnswer, ParameterRequestOut,
    TemplateCreate, TemplateOut,
    AnalyticsSummary,
)
from triage import run_triage, TriageInput

# ─── Konfiguracja bazy ────────────────────────────────────────────────────────
DATABASE_URL = "sqlite:///./rcm_erp.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)  # tworzy tabele jeśli nie istnieją

# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="RCM ERP",
    description="System CPQ/ERP dla RCM Sp. z o.o. — Gołdap",
    version="0.1.0",
)

# CORS — pozwala frontendowi Vue na localhost lub 192.168.x.x łączyć się z API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # zawęzić po wdrożeniu do adresu IP biura
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Dependency — sesja bazy ──────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Helper: generowanie numeru zlecenia ──────────────────────────────────────
def _generate_order_number(db: Session) -> str:
    """Format: {liczba_porządkowa}/{rok}, np. "23/2026"."""
    year = date.today().year
    count = db.query(func.count(Order.id)).scalar() + 1
    return f"{count}/{year}"


# =============================================================================
# HEALTH CHECK
# =============================================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "system": "RCM ERP"}


# =============================================================================
# ORDERS — Zlecenia Wewnętrzne
# =============================================================================

@app.post("/api/orders", response_model=OrderOut, status_code=201)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    """Biuro tworzy nowe zlecenie przez Wizarda (Step 1-3)."""
    order = Order(
        order_number    = _generate_order_number(db),
        client          = payload.client,
        deadline        = payload.deadline,
        material        = payload.material,
        has_drawing     = payload.has_drawing,
        notes           = payload.notes,
        purpose         = payload.purpose,
        estimated_value = payload.estimated_value,
        order_type      = payload.order_type,
        description     = payload.description,
        requires_visit  = payload.requires_visit,
        template_id     = payload.template_id,
        quantity        = payload.quantity,
        is_defence      = payload.is_defence,
        status          = OrderStatus.draft,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@app.get("/api/orders", response_model=List[OrderOut])
def list_orders(
    status: Optional[str] = None,
    branch: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Lista wszystkich zleceń z opcjonalnym filtrem po statusie/gałęzi."""
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status)
    if branch:
        q = q.filter(Order.triage_branch == branch)
    return q.order_by(Order.deadline.asc()).all()


@app.get("/api/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")
    return order


# =============================================================================
# TRIAGE — silnik routingu (Odrzut / Standard / Niestandard)
# =============================================================================

@app.post("/api/orders/{order_id}/triage", response_model=TriageResponse)
def triage_order(order_id: int, db: Session = Depends(get_db)):
    """
    Uruchamia Triage Engine dla zlecenia.
    Aktualizuje order.status i order.triage_branch w bazie.
    """
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")

    # Zbuduj obiekt wejściowy dla Triage Engine
    triage_input = TriageInput(
        client          = order.client,
        material        = order.material or "",
        deadline_days   = (order.deadline - date.today()).days if order.deadline else 999,
        has_drawing     = order.has_drawing,
        order_type      = order.order_type or "remont",
        sop_name        = order.sop_name,        # Fix #1: nie None!
        template_id     = order.template_id,     # Fix #5: przekazać wprost
        estimated_value = float(order.estimated_value or 0),
    )

    result = run_triage(triage_input, db)

    # Zapisz wynik triage w bazie (w tym dopasowany template_id)
    order.triage_branch = result.branch
    order.status = {
        "odrzut":      OrderStatus.rejected,
        "standard":    OrderStatus.standard,
        "niestandard": OrderStatus.niestandard,
    }[result.branch]
    if result.template_id and not order.template_id:
        order.template_id = result.template_id  # zapisz dopasowany szablon SOP
    db.commit()

    return TriageResponse(
        branch      = result.branch,
        message     = result.message,
        template_id = result.template_id,
        rule_name   = result.rule_name,
        warnings    = result.warnings or [],
    )


# =============================================================================
# QUOTES — Wycena
# =============================================================================

@app.post("/api/orders/{order_id}/quote", response_model=QuoteOut, status_code=201)
def create_quote(order_id: int, payload: QuoteCreate, db: Session = Depends(get_db)):
    """Ręczna wycena przez Technologa (gałąź Niestandard)."""
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")

    # Pobierz stawkę robocizny z bazy — Fix #3: nie hardkod
    rate_setting = db.query(Setting).get("labor_rate_pln")
    labor_rate   = float(rate_setting.value) if rate_setting else 90.0
    # Oblicz cenę: (material + robocizna) × (1 + overhead) × (1 + marża)
    labor_cost  = payload.labor_hours * labor_rate
    subtotal    = (payload.material_cost + labor_cost) * (1 + payload.overhead_pct)
    total_net   = subtotal * (1 + payload.margin_pct)

    quote = Quote(
        order_id      = order_id,
        line_items    = payload.line_items,
        labor_hours   = payload.labor_hours,
        material_cost = payload.material_cost,
        overhead_pct  = payload.overhead_pct,
        margin_pct    = payload.margin_pct,
        total_net     = round(total_net, 2),
        is_zapor      = False,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


@app.post("/api/orders/{order_id}/quote/zapor", response_model=QuoteOut, status_code=201)
def create_zapor_quote(order_id: int, payload: QuoteZaporCreate, db: Session = Depends(get_db)):
    """
    Zaporowa marża — Technolog klika jeden przycisk.
    Cena = material × godziny × mnożnik losowy (3.0–4.5).
    Klient albo zapłaci dużo, albo sam odpadnie — obie opcje są dobre dla zakładu.
    """
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")

    # Mnożnik zaporowy — losowy z przedziału [3.0, 4.5]
    multiplier = random.uniform(3.0, 4.5)
    total_net  = payload.material_cost * payload.hours_estimate * multiplier

    quote = Quote(
        order_id      = order_id,
        line_items    = [],
        labor_hours   = payload.hours_estimate,
        material_cost = payload.material_cost,
        overhead_pct  = 0.0,
        margin_pct    = round(multiplier - 1, 4),   # dla widoku Dyrektora
        total_net     = round(total_net, 2),
        is_zapor      = True,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


@app.get("/api/orders/{order_id}/quote", response_model=QuoteOut)
def get_quote(order_id: int, db: Session = Depends(get_db)):
    quote = db.query(Quote).filter(Quote.order_id == order_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Brak wyceny dla tego zlecenia")
    return quote


# =============================================================================
# MATERIAL REQUESTS — Zapotrzebowanie Materiałowe
# =============================================================================

@app.post("/api/orders/{order_id}/materials", response_model=MaterialRequestOut, status_code=201)
def create_material_request(
    order_id: int, payload: MaterialRequestCreate, db: Session = Depends(get_db)
):
    req = MaterialRequest(
        order_id    = order_id,
        client      = payload.client,
        materials   = payload.materials,
        extra_notes = payload.extra_notes,
        priority    = payload.priority,
        status      = "pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@app.get("/api/orders/{order_id}/materials", response_model=MaterialRequestOut)
def get_material_request(order_id: int, db: Session = Depends(get_db)):
    req = db.query(MaterialRequest).filter(MaterialRequest.order_id == order_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Brak zapotrzebowania materiałowego")
    return req


# =============================================================================
# QUALITY CARDS — Karta Kontrolna (po każdym etapie!)
# =============================================================================

@app.post("/api/orders/{order_id}/quality", response_model=QualityCardOut, status_code=201)
def add_quality_card(
    order_id: int, payload: QualityCardCreate, db: Session = Depends(get_db)
):
    card = QualityCard(
        order_id        = order_id,
        operation_id    = payload.operation_id,
        stage_name      = payload.stage_name,
        check_linear    = payload.check_linear,
        check_geometric = payload.check_geometric,
        check_surface   = payload.check_surface,
        passed          = payload.passed,
        checked_by      = payload.checked_by,
        checked_at      = datetime.utcnow(),
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@app.get("/api/orders/{order_id}/quality", response_model=List[QualityCardOut])
def get_quality_cards(order_id: int, db: Session = Depends(get_db)):
    return db.query(QualityCard).filter(QualityCard.order_id == order_id).all()


# =============================================================================
# PARAMETER REQUESTS — "Zapytaj o parametry" (Technolog → Biuro)
# =============================================================================

@app.post("/api/orders/{order_id}/params", response_model=ParameterRequestOut, status_code=201)
def ask_for_params(
    order_id: int, payload: ParameterRequestCreate, db: Session = Depends(get_db)
):
    """Technolog wysyła pytanie do Biuro — bez telefonu."""
    req = ParameterRequest(
        order_id      = order_id,
        question_text = payload.question_text,
        status        = "pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@app.patch("/api/params/{param_id}/answer", response_model=ParameterRequestOut)
def answer_param_request(
    param_id: int, payload: ParameterRequestAnswer, db: Session = Depends(get_db)
):
    """Biuro odpowiada na pytanie Technologa."""
    req = db.query(ParameterRequest).get(param_id)
    if not req:
        raise HTTPException(status_code=404, detail="Pytanie nie znalezione")

    req.answer_text  = payload.answer_text
    req.status       = "answered"
    req.answered_at  = datetime.utcnow()
    db.commit()
    db.refresh(req)
    return req


@app.get("/api/orders/{order_id}/params", response_model=List[ParameterRequestOut])
def get_param_requests(order_id: int, db: Session = Depends(get_db)):
    return db.query(ParameterRequest).filter(ParameterRequest.order_id == order_id).all()


@app.get("/api/params", response_model=List[ParameterRequestOut])
def get_all_param_requests(status: Optional[str] = None, db: Session = Depends(get_db)):
    """Wszystkie pytania Technologa — dla Biuro (filtr po statusie: pending/answered)."""
    q = db.query(ParameterRequest)
    if status:
        q = q.filter(ParameterRequest.status == status)
    return q.order_by(ParameterRequest.asked_at.desc()).all()


# =============================================================================
# PRODUCT TEMPLATES — Katalog SOP (Technolog zarządza)
# =============================================================================

@app.get("/api/templates", response_model=List[TemplateOut])
def list_templates(category: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ProductTemplate).filter(ProductTemplate.is_active == True)
    if category:
        q = q.filter(ProductTemplate.category == category)
    return q.all()


@app.post("/api/templates", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    """Technolog tworzy szablon SOP raz → Biuro używa wiele razy."""
    tmpl = ProductTemplate(
        name               = payload.name,
        category           = payload.category,
        operations_json    = payload.operations_json,
        materials_json     = payload.materials_json,
        instruction_blocks = payload.instruction_blocks,
        machines_json      = payload.machines_json,
        base_price_pln     = payload.base_price_pln,
        margin_pct         = payload.margin_pct,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl


@app.delete("/api/templates/{template_id}", status_code=204)
def archive_template(template_id: int, db: Session = Depends(get_db)):
    """
    Soft delete szablonu — ustawia is_active=False, NIE usuwa z bazy.
    Stare zlecenia z tym template_id nadal działają i generują PDF.
    Szablon znika z listy u Biuro, ale historia jest nienaruszona.
    """
    tmpl = db.query(ProductTemplate).get(template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Szablon nie znaleziony")
    if not tmpl.is_active:
        raise HTTPException(status_code=409, detail="Szablon już zarchiwizowany")
    tmpl.is_active = False
    db.commit()


@app.patch("/api/templates/{template_id}/restore", response_model=TemplateOut)
def restore_template(template_id: int, db: Session = Depends(get_db)):
    """Przywraca zarchiwizowany szablon (odwrócenie soft delete)."""
    tmpl = db.query(ProductTemplate).get(template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Szablon nie znaleziony")
    tmpl.is_active = True
    db.commit()
    db.refresh(tmpl)
    return tmpl


# =============================================================================
# ANALYTICS — Dashboard Dyrektora (tylko odczyt)
# =============================================================================

@app.get("/api/analytics", response_model=AnalyticsSummary)
def get_analytics(db: Session = Depends(get_db)):
    total         = db.query(func.count(Order.id)).scalar()
    odrzut_count  = db.query(func.count(Order.id)).filter(Order.triage_branch == "odrzut").scalar()
    standard      = db.query(func.count(Order.id)).filter(Order.triage_branch == "standard").scalar()
    niestandard   = db.query(func.count(Order.id)).filter(Order.triage_branch == "niestandard").scalar()
    in_prod       = db.query(func.count(Order.id)).filter(Order.status == "in_production").scalar()
    done          = db.query(func.count(Order.id)).filter(Order.status == "done").scalar()
    avg_margin    = db.query(func.avg(Quote.margin_pct)).scalar()

    return AnalyticsSummary(
        total_orders          = total or 0,
        odrzut_count          = odrzut_count or 0,
        odrzut_pct            = round((odrzut_count / total * 100) if total else 0, 1),
        standard_count        = standard or 0,
        niestandard_count     = niestandard or 0,
        avg_margin_pct        = round(float(avg_margin) * 100, 1) if avg_margin else None,
        orders_in_production  = in_prod or 0,
        orders_done           = done or 0,
    )


# =============================================================================
# HARMONOGRAM — Lista Zleceń (Dokument 4 z kartki)
# Dyrektor i Technolog widzą wszystkie zlecenia posortowane po terminie
# Brak osobnej tabeli — to jest widok SELECT orders JOIN operations
# =============================================================================

@app.get("/api/harmonogram")
def get_harmonogram(db: Session = Depends(get_db)):
    """Zwraca wszystkie aktywne zlecenia posortowane po deadlinie."""
    orders = (
        db.query(Order)
        .filter(Order.status.notin_(["rejected", "done"]))
        .order_by(Order.deadline.asc())
        .all()
    )
    return [
        {
            "id":           o.id,
            "order_number": o.order_number,
            "client":       o.client,
            "status":       o.status,
            "deadline":     o.deadline.isoformat() if o.deadline else None,
            "branch":       o.triage_branch,
        }
        for o in orders
    ]


# =============================================================================
# SETTINGS — Ustawienia systemowe (stawka robocizny, itp.)
# =============================================================================

@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    """Lista wszystkich ustawień systemowych."""
    return db.query(Setting).all()


@app.patch("/api/settings/{key}")
def update_setting(key: str, payload: dict, db: Session = Depends(get_db)):
    """Zmień wartość ustawienia (np. stawkę robocizny) bez restartu serwera."""
    setting = db.query(Setting).get(key)
    if not setting:
        raise HTTPException(status_code=404, detail=f"Ustawienie '{key}' nie istnieje")
    setting.value = str(payload.get("value", setting.value))
    db.commit()
    return {"key": key, "value": setting.value}


# =============================================================================
# PDF — Arkusz Zlecenia (zastępuje ręczne Excele CNC/Montaż)
# =============================================================================

@app.get("/api/orders/{order_id}/pdf")
def get_order_pdf(order_id: int, db: Session = Depends(get_db)):
    """
    Generuje PDF Arkusza Zlecenia dla operatorów CNC/Montaż.
    Zawiera: dane zlecenia, instrukcję SOP (instruction_blocks), Kartę Kontrolną.
    """
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")

    template = db.query(ProductTemplate).get(order.template_id) if order.template_id else None
    quote    = db.query(Quote).filter(Quote.order_id == order_id).first()

    try:
        from pdf_gen import get_content_type
        pdf_bytes    = generate_arkusz_pdf(order, template, quote)
        content_type = get_content_type(order)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd generowania PDF: {e}")

    ext      = "pdf" if "pdf" in content_type else "html"
    filename = f"Arkusz_{(order.order_number or str(order_id)).replace('/', '-')}.{ext}"
    return Response(
        content=pdf_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/orders/{order_id}/oferta")
def get_order_oferta(order_id: int, db: Session = Depends(get_db)):
    """
    Generuje PDF Oferty Handlowej dla klienta (Biuro → klient email/WhatsApp).
    Zawiera: cena netto + brutto, termin, warunki płatności.
    NIE zawiera: stawek, robocizny, marży — to tajemnica handlowa.
    """
    order = db.query(Order).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Zlecenie nie znalezione")

    template = db.query(ProductTemplate).get(order.template_id) if order.template_id else None
    quote    = db.query(Quote).filter(Quote.order_id == order_id).first()

    try:
        from pdf_gen import generate_oferta_pdf, get_content_type
        pdf_bytes    = generate_oferta_pdf(order, template, quote)
        content_type = get_content_type(order)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd generowania Oferty: {e}")

    ext      = "pdf" if "pdf" in content_type else "html"
    filename = f"Oferta_{(order.order_number or str(order_id)).replace('/', '-')}.{ext}"
    return Response(
        content=pdf_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =============================================================================
# FRONTEND — serwowanie pliku Vue
# =============================================================================

@app.get("/", response_class=FileResponse)
def serve_frontend():
    """Serwuje główny plik Vue 3 CDN — jeden plik dla wszystkich ról."""
    return FileResponse("../frontend/app.html")
